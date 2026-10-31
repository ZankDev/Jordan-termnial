"""EVENT MOVERS: which small caps are really breaking out because of a critical event, with a chart from the news time and chart-based targets.

After every critical event / AI market-moving story, the event's themes (oil & tankers for an energy shock, defence for an attack, ...) are watched for
a few hours of US trading (pre-market and after-hours count):
  universe   Yahoo screener per industry: US-listed common stock, market cap $30M-$2.5B, price >= $1, liquid (10-day volume >= 150k)
  qualifies  +7% or more since the news AND today >= +5%; heavy volume after the news (>= 3x the normal pace for that much time, and >= $750k traded);
             the move started AFTER the news (less than 40% of it happened in the 2 hours before); it still holds >= 60% of its post-news gain;
             no earnings within a day (its own catalyst)
  ranked by  move since the news x sqrt(relative volume), up to 3 names per alert, each symbol at most once every 6 hours
  targets    T1 = nearest daily swing high above the price (last 12 months), T2 = measured move (the news leg repeated) or the next swing high,
             invalidation = the price at the news. Chart levels, not a forecast.
Every pick is tracked (T1 first or invalidation first, best move, worst move) and the track record is printed on every card.
Not investment advice; nothing here places orders.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import math
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

from app import db

log = logging.getLogger(__name__)
NY, IL = ZoneInfo("America/New_York"), ZoneInfo("Asia/Jerusalem")

THEME_INDUSTRIES: dict[str, list[str]] = {
    "OIL & ENERGY": ["Oil & Gas E&P", "Oil & Gas Integrated", "Oil & Gas Refining & Marketing", "Oil & Gas Equipment & Services", "Oil & Gas Drilling"],
    "TANKERS & SHIPPING": ["Marine Shipping", "Oil & Gas Midstream"],
    "DEFENCE": ["Aerospace & Defense", "Security & Protection Services"],
    "GOLD & MINERS": ["Gold", "Silver", "Other Precious Metals & Mining"],
    "URANIUM & NUCLEAR": ["Uranium", "Utilities - Independent Power Producers"],
    "CYBER": ["Software - Infrastructure"],
    "ISRAEL": ["Aerospace & Defense", "Software - Infrastructure"],
    "CRYPTO": ["Capital Markets", "Financial Data & Stock Exchanges"],
    "AGRICULTURE": ["Agricultural Inputs", "Farm Products"],
    "CHINA": [],
}
KIND_THEMES = {"energy": ["OIL & ENERGY", "TANKERS & SHIPPING"], "war": ["DEFENCE", "OIL & ENERGY", "TANKERS & SHIPPING", "GOLD & MINERS"],
               "macro": ["GOLD & MINERS"], "market": [], "geopolitics": ["DEFENCE", "OIL & ENERGY"], "commodities": ["OIL & ENERGY", "GOLD & MINERS"]}
WORD_THEMES = [(r"tanker|shipping|strait|hormuz|red sea|houthi|vessel|bulker|cargo ship|suez|bab el", ["TANKERS & SHIPPING", "OIL & ENERGY"]),
               (r"\boil\b|crude|opec|refiner|pipeline|brent|wti|natural gas|lng", ["OIL & ENERGY", "TANKERS & SHIPPING"]),
               (r"missile|drone|strike|attack|airstrike|war\b|military|troops|nato|defen[cs]e", ["DEFENCE"]),
               (r"uranium|nuclear|enrichment|reactor", ["URANIUM & NUCLEAR"]),
               (r"\bgold\b|silver|safe haven", ["GOLD & MINERS"]),
               (r"cyber|hack|ransomware|breach", ["CYBER"]),
               (r"bitcoin|crypto|\bbtc\b|ether", ["CRYPTO"]),
               (r"wheat|grain|fertili[sz]er|corn|soybean", ["AGRICULTURE"])]
EXCHANGES = ["NMS", "NYQ", "ASE", "NGM", "NCM", "PCX", "BTS"]
MIN_MOVE, MIN_DAY, MIN_RVOL, MIN_DOLLARS = 0.07, 0.05, 3.0, 750_000
MAX_CAP = 2.0e9          # small caps only: nothing from the S&P 500 / large caps
FAST_WINDOW = 45 * 60    # scan every minute in the first 45 minutes of a watch (when the explosion happens), then every 2.5 minutes
PER_ALERT, SYMBOL_COOLDOWN, MAX_ALERTS_DAY = 3, 6 * 3600, 8
SCHEMA = """
CREATE TABLE IF NOT EXISTS mover_watch (key TEXT PRIMARY KEY, ts INTEGER, title TEXT, kind TEXT, themes TEXT, until INTEGER, alerts INTEGER DEFAULT 0, symbols TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS mover_picks (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, title TEXT, theme TEXT, symbol TEXT, name TEXT, mcap REAL,
  ts_event INTEGER, ts_alert INTEGER, p_event REAL, p_alert REAL, t1 REAL, t2 REAL, inval REAL, move REAL, rvol REAL,
  best REAL, worst REAL, outcome TEXT, ts_outcome INTEGER, notified INTEGER DEFAULT 0, closed INTEGER DEFAULT 0);
"""
_cache: dict[str, tuple[float, Any]] = {}
_last_scan: dict[str, int] = {}
_ready = False


def ensure() -> None:
    global _ready
    if not _ready:
        with db.get_conn() as c:
            c.executescript(SCHEMA)
            if "symbols" not in [r[1] for r in c.execute("PRAGMA table_info(mover_watch)")]:
                c.execute("ALTER TABLE mover_watch ADD COLUMN symbols TEXT DEFAULT '[]'")
            if "float" not in [r[1] for r in c.execute("PRAGMA table_info(mover_picks)")]:
                c.execute("ALTER TABLE mover_picks ADD COLUMN float REAL")
                c.execute("ALTER TABLE mover_picks ADD COLUMN source TEXT")
        _ready = True


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    if len(_cache) > 600:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:200]:
            _cache.pop(k, None)
    return val


# ---------------------------------------------------------------- market hours (US extended session 04:00-20:00 ET, weekdays)
def _in_session(t: dt.datetime) -> bool:
    t = t.astimezone(NY)
    return t.weekday() < 5 and dt.time(4, 0) <= t.time() < dt.time(20, 0)


def _next_open(ts: int) -> int:
    t = dt.datetime.fromtimestamp(ts, NY)
    for _ in range(8 * 24 * 4):
        if _in_session(t):
            return int(t.timestamp())
        t += dt.timedelta(minutes=15)
    return ts


def themes_for(title: str, kind: str | None, extra: list[str] | None = None) -> list[str]:
    from app.intel import impact

    t = (title or "").lower()
    out: list[str] = []
    for rx, th in WORD_THEMES:
        if re.search(rx, t):
            out += [x for x in th if x not in out]
    for x in (impact.understand(title, kind).get("themes") or []) + (extra or []) + KIND_THEMES.get(kind or "", []):
        if x in THEME_INDUSTRIES and THEME_INDUSTRIES[x] and x not in out:
            out.append(x)
    return out[:4]


def watch(key: str, ts: int, title: str, kind: str | None = None, extra_themes: list[str] | None = None, symbols: list[str] | None = None) -> list[str]:
    """Start watching an event for small-cap breakouts: its themes (industries) plus the whole small-cap tape, and any company the headline names.
    Called by the critical-event alerts, the news-engine alerts and the AI reader (company news)."""
    ensure()
    th = themes_for(title, kind, extra_themes) if kind != "company" else []
    syms = [x.upper() for x in (symbols or []) if re.fullmatch(r"[A-Za-z]{1,5}", x or "")]
    if not th and not syms:
        return []
    start = ts if _in_session(dt.datetime.fromtimestamp(ts, NY)) else _next_open(ts)
    until = start + (4 if start == ts else 3) * 3600
    with db.get_conn() as c:
        c.execute("INSERT OR IGNORE INTO mover_watch (key, ts, title, kind, themes, until, symbols) VALUES (?,?,?,?,?,?,?)",
                  (key, int(ts), title[:300], kind or "", json.dumps(th), until, json.dumps(syms)))
    return th or syms


# ---------------------------------------------------------------- data
def _screen(industry: str) -> list[dict[str, Any]]:
    from app.api import invest_routes as ir

    def run():
        body = {"offset": 0, "size": 100, "sortField": "percentchange", "sortType": "DESC", "quoteType": "EQUITY",
                "query": {"operator": "AND", "operands": [
                    {"operator": "eq", "operands": ["region", "us"]},
                    {"operator": "btwn", "operands": ["intradaymarketcap", 3e7, MAX_CAP]},
                    {"operator": "eq", "operands": ["industry", industry]},
                    {"operator": "or", "operands": [{"operator": "eq", "operands": ["exchange", x]} for x in EXCHANGES]},
                    {"operator": "gt", "operands": ["intradayprice", 1]},
                    {"operator": "gt", "operands": ["avgdailyvol3m", 150000]}]}}
        for attempt in range(2):
            r = ir._ys.post("https://query1.finance.yahoo.com/v1/finance/screener", params={"crumb": ir._yahoo_crumb(force=attempt == 1), "lang": "en-US", "region": "US"},
                            json=body, timeout=20)
            if r.status_code in (401, 403) and attempt == 0:
                continue
            r.raise_for_status()
            q = r.json()["finance"]["result"][0]["quotes"]
            return [{**x, "industry": x.get("industry") or industry} for x in q if "-" not in x["symbol"] and x.get("quoteType") == "EQUITY"]
        return []
    return _cached(f"scr:{industry}", 240, run)


def _tape() -> list[dict[str, Any]]:
    """Every US small cap up 4%+ today (regular session), biggest first: catches beneficiaries outside the obvious industries."""
    from app.api import invest_routes as ir

    def run():
        body = {"offset": 0, "size": 100, "sortField": "percentchange", "sortType": "DESC", "quoteType": "EQUITY",
                "query": {"operator": "AND", "operands": [
                    {"operator": "eq", "operands": ["region", "us"]},
                    {"operator": "btwn", "operands": ["intradaymarketcap", 3e7, MAX_CAP]},
                    {"operator": "or", "operands": [{"operator": "eq", "operands": ["exchange", x]} for x in EXCHANGES]},
                    {"operator": "gt", "operands": ["intradayprice", 1]},
                    {"operator": "gt", "operands": ["percentchange", 4]},
                    {"operator": "gt", "operands": ["avgdailyvol3m", 150000]}]}}
        for attempt in range(2):
            r = ir._ys.post("https://query1.finance.yahoo.com/v1/finance/screener", params={"crumb": ir._yahoo_crumb(force=attempt == 1), "lang": "en-US", "region": "US"},
                            json=body, timeout=20)
            if r.status_code in (401, 403) and attempt == 0:
                continue
            r.raise_for_status()
            return [x for x in r.json()["finance"]["result"][0]["quotes"] if "-" not in x["symbol"] and x.get("quoteType") == "EQUITY"]
        return []
    return _cached("tape", 90, run)


def _quotes(syms: list[str]) -> list[dict[str, Any]]:
    from app.api import invest_routes as ir

    if not syms:
        return []
    j = ir._yget("https://query1.finance.yahoo.com/v7/finance/quote", {"symbols": ",".join(syms[:40])})
    return [q for q in (j.get("quoteResponse") or {}).get("result", []) if q.get("quoteType") == "EQUITY"]


def _profile(sym: str) -> dict[str, Any]:
    """Float, short interest and what the company does (for the AI check and the card). Cached a day."""
    from app.api import invest_routes as ir

    def run():
        r = ir._qsummary(sym, "defaultKeyStatistics,assetProfile")
        k, a = r.get("defaultKeyStatistics") or {}, r.get("assetProfile") or {}
        return {"float": k.get("floatShares"), "short": k.get("shortPercentOfFloat"), "industry": a.get("industry"), "about": (a.get("longBusinessSummary") or "")[:260]}
    try:
        return _cached(f"prof:{sym}", 86400, run)
    except Exception:  # noqa: BLE001
        return {}


def _bars1(sym: str) -> list[list[float]]:
    from app.api import invest_routes as ir

    return _cached(f"b1:{sym}", 45, lambda: ir._history(sym, "2d", "1m", prepost=True)["candles"])


def _bars(sym: str) -> list[list[float]]:
    from app.api import invest_routes as ir

    return _cached(f"b5:{sym}", 90, lambda: ir._history(sym, "5d", "5m", prepost=True)["candles"])


def _daily(sym: str) -> list[list[float]]:
    from app.api import invest_routes as ir

    return _cached(f"d1:{sym}", 3600, lambda: ir._history(sym, "1y", "1d")["candles"])


def _price_at(bars: list[list[float]], ts: int) -> float | None:
    p = None
    for b in bars:
        if b[0] <= ts:
            p = b[4]
        else:
            break
    return p


# ---------------------------------------------------------------- the test for one stock
def evaluate(q: dict[str, Any], ts: int, now: int | None = None) -> dict[str, Any] | None:
    sym = q["symbol"]
    now = now or int(time.time())
    if (q.get("marketCap") or 0) > MAX_CAP or (q.get("marketCap") or 0) < 2e7:
        return None
    if (q.get("regularMarketChangePercent") or 0) / 100 < MIN_DAY and (q.get("postMarketChangePercent") or q.get("preMarketChangePercent") or 0) / 100 < MIN_DAY:
        return None
    e_ts = q.get("earningsTimestamp") or q.get("earningsTimestampStart")
    if e_ts and abs(e_ts - ts) < 30 * 3600:
        return None  # its own catalyst
    try:
        fine = now - ts < 30 * 3600  # 1-minute bars around a fresh event: the exact price at the news and the first minutes of the pop
        bars = [b for b in (_bars1(sym) if fine else _bars(sym)) if b[0] <= now]
    except Exception:  # noqa: BLE001
        return None
    if len(bars) < 20:
        return None
    p_event = _price_at(bars, ts)
    p_before = _price_at(bars, ts - 2 * 3600)
    if not p_event or not p_before:
        return None
    after = [b for b in bars if b[0] > ts]
    if len(after) < 2:
        return None
    last = after[-1][4]
    hi = max(b[2] for b in after)
    move = last / p_event - 1
    if move < MIN_MOVE:
        return None
    total = last / p_before - 1
    if total > 0 and (p_event / p_before - 1) > 0.4 * total:
        return None  # it was already running before the news
    if hi > p_event and (last - p_event) < 0.6 * (hi - p_event):
        return None  # most of the pop is gone already
    vol_after = sum(b[5] for b in after)
    dollars = sum(b[5] * b[4] for b in after)
    mins = max(5, (after[-1][0] - after[0][0]) / 60 + (1 if fine else 5))
    avg = q.get("averageDailyVolume10Day") or q.get("averageDailyVolume3Month") or 0
    pace = avg / 390 * mins if avg else 0  # normal volume for that many regular-session minutes
    rvol = vol_after / pace if pace else 0
    if rvol < MIN_RVOL or dollars < MIN_DOLLARS:
        return None
    tg = targets(sym, last, p_event, hi)
    first = next((b for b in after if b[4] >= p_event * (1 + MIN_MOVE / 2)), None)
    return {"symbol": sym, "react_min": round((first[0] - ts) / 60) if first else None, "name": (q.get("shortName") or q.get("longName") or sym)[:40], "industry": q.get("industry") or "", "mcap": q.get("marketCap") or 0,
            "p_event": p_event, "price": last, "high": hi, "move": move, "rvol": rvol, "dollars": dollars, "score": move * math.sqrt(min(rvol, 25)),
            "day": (q.get("regularMarketChangePercent") or 0) / 100, **tg}


def targets(sym: str, price: float, p_event: float, hi_after: float) -> dict[str, Any]:
    """T1 = nearest daily swing high above the price in the last year; T2 = the news leg repeated (measured move) or the next swing high."""
    try:
        d = _daily(sym)
    except Exception:  # noqa: BLE001
        d = []
    swings: list[tuple[float, int]] = []
    for i in range(3, len(d) - 3):
        h = d[i][2]
        if h > price * 1.015 and h == max(x[2] for x in d[i - 3:i + 4]):
            swings.append((h, int(d[i][0])))
    swings.sort()
    atr = (sum(max(d[i][2], d[i - 1][4]) - min(d[i][3], d[i - 1][4]) for i in range(len(d) - 14, len(d))) / 14) if len(d) > 15 else price * 0.06
    t1, t1_note = (swings[0][0], "swing high " + dt.datetime.fromtimestamp(swings[0][1], NY).strftime("%b %d")) if swings else (price + 1.5 * atr, "1.5 x daily ATR")
    if t1 / price - 1 > 0.6:  # nearest resistance far away: use volatility instead
        t1, t1_note = price + 1.5 * atr, "1.5 x daily ATR"
    mm = price + (max(hi_after, price) - p_event)
    nxt = [s for s in swings if s[0] > t1 * 1.03]
    t2, t2_note = (mm, "measured move (the news leg repeated)")
    if nxt and nxt[0][0] < mm:
        t2, t2_note = nxt[0][0], "next swing high " + dt.datetime.fromtimestamp(nxt[0][1], NY).strftime("%b %d")
    if t2 <= t1 * 1.02:
        t2, t2_note = t1 + atr, "T1 + 1 daily ATR"
    return {"t1": round(t1, 4), "t1_note": t1_note, "t2": round(t2, 4), "t2_note": t2_note, "inval": round(p_event, 4), "atr": atr,
            "y_high": max((x[2] for x in d), default=None)}


# ---------------------------------------------------------------- chart image
def chart_png(m: dict[str, Any], ts: int, title: str) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    bars = [b for b in _bars(m["symbol"]) if b[0] >= ts - 3 * 3600]
    if len(bars) < 5:
        bars = _bars(m["symbol"])[-80:]
    bars = bars[-160:]
    BG, FG, DIM, ORA, GRN, RED = "#0b0b0d", "#ececf1", "#7d7d88", "#ffa028", "#2be07e", "#ff4a4a"
    fig = plt.figure(figsize=(10.8, 6.4), dpi=110, facecolor=BG)
    ax = fig.add_axes([0.05, 0.27, 0.86, 0.58], facecolor=BG)
    av = fig.add_axes([0.05, 0.08, 0.86, 0.17], facecolor=BG, sharex=ax)
    xs = list(range(len(bars)))
    ev_i = next((i for i, b in enumerate(bars) if b[0] > ts), len(bars) - 1)
    for i, b in enumerate(bars):
        o, h, l, c = b[1], b[2], b[3], b[4]
        col = GRN if c >= o else RED
        ax.plot([i, i], [l, h], color=col, lw=1.0, zorder=2)
        ax.add_patch(Rectangle((i - 0.33, min(o, c)), 0.66, max(abs(c - o), (h - l) * 0.02 + 1e-9), color=col, zorder=3))
        av.bar(i, b[5], width=0.7, color=ORA if i >= ev_i else "#3a3a44")
    ymin = min(min(b[3] for b in bars), m["inval"]) * 0.985
    ymax = max(max(b[2] for b in bars), m["t1"]) * 1.015
    show_t2 = m["t2"] < ymax * 1.25
    if show_t2:
        ymax = max(ymax, m["t2"] * 1.01)
    ax.set_ylim(ymin, ymax)
    ax.axvline(ev_i - 0.5, color=ORA, lw=1.6, ls="-", zorder=1)
    ax.text(ev_i - 0.3, ymax, " NEWS " + dt.datetime.fromtimestamp(ts, IL).strftime("%H:%M"), color=BG, fontsize=9, fontweight="bold", va="top",
            bbox=dict(boxstyle="round,pad=0.25", fc=ORA, ec="none"))
    lv = [(m["inval"], RED, "--", f"INVALID {m['inval']:.2f} · price at the news"), (m["t1"], GRN, "--", f"T1 {m['t1']:.2f} · +{(m['t1'] / m['price'] - 1) * 100:.0f}%")]
    if show_t2:
        lv.append((m["t2"], GRN, ":", f"T2 {m['t2']:.2f} · +{(m['t2'] / m['price'] - 1) * 100:.0f}%"))
    for y, col, ls, lab in lv:
        ax.axhline(y, color=col, lw=1.2, ls=ls, alpha=0.9, zorder=1)
        ax.text(0.5, y, " " + lab + " ", color=BG, fontsize=8.5, va="center", fontweight="bold", zorder=5, bbox=dict(boxstyle="round,pad=0.2", fc=col, ec="none", alpha=0.92))
    ax.axhline(m["price"], color=FG, lw=0.6, alpha=0.4)
    for a in (ax, av):
        a.tick_params(colors=DIM, labelsize=8)
        for s in a.spines.values():
            s.set_color("#26262c")
        a.grid(color="#1b1b21", lw=0.6)
    step = max(1, len(bars) // 8)
    av.set_xticks(xs[::step])
    av.set_xticklabels([dt.datetime.fromtimestamp(bars[i][0], IL).strftime("%H:%M") for i in xs[::step]])
    plt.setp(ax.get_xticklabels(), visible=False)
    av.set_yticks([])
    ax.yaxis.tick_right()
    ax.set_xlim(-1, len(bars) + 0.5)
    tk = fig.text(0.05, 0.93, f"{m['symbol']}", color=FG, fontsize=22, fontweight="bold")
    fig.canvas.draw()
    x2 = fig.transFigure.inverted().transform(tk.get_window_extent().transformed(fig.dpi_scale_trans.inverted() * fig.dpi_scale_trans))[1][0] if False else         fig.transFigure.inverted().transform((tk.get_window_extent().x1, 0))[0]
    fig.text(x2 + 0.012, 0.936, f"{m['name']}  ·  ${m['mcap'] / 1e6:,.0f}M  ·  {m['industry']}", color=DIM, fontsize=10)
    fig.text(0.91, 0.93, f"+{m['move'] * 100:.1f}%", color=GRN, fontsize=22, fontweight="bold", ha="right")
    fig.text(0.91, 0.895, f"since the news  ·  {m['rvol']:.1f}x volume", color=DIM, fontsize=9, ha="right")
    fig.text(0.05, 0.885, (title[:105] + ("…" if len(title) > 105 else "")), color=ORA, fontsize=9.5)
    fig.text(0.05, 0.02, "◆ JORDAN TERMINAL · EVENT MOVERS · 5-min candles, Israel time · chart levels, not advice", color=DIM, fontsize=8)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------- is the move really about the event?
def own_news(sym: str, since: int) -> list[dict[str, Any]]:
    from app.api import invest_routes as ir

    def run():
        j = ir._yget("https://query1.finance.yahoo.com/v1/finance/search", {"q": sym, "newsCount": 8, "quotesCount": 0}, crumb=False)
        return [{"title": n.get("title", ""), "ts": n.get("providerPublishTime") or 0, "pub": n.get("publisher", "")} for n in j.get("news", [])]
    try:
        return [n for n in _cached(f"news:{sym}", 600, run) if n["ts"] >= since]
    except Exception:  # noqa: BLE001
        return []


RELEVANCE_SYSTEM = (
    "You check whether small-cap stock moves are CAUSED by a market event. For each stock you get its industry, its move since the event and its own "
    "recent headlines. A stock is related only if the event plausibly drives its business today (e.g. tanker attacks -> tanker owners and oil producers; "
    "missile strikes -> defence and drone makers). If the stock has its own company news (contract, merger, offering, earnings, FDA, index inclusion, "
    "government selection...) that explains the move, it is NOT related. Answer JSON only: {\"items\":[{\"symbol\":\"XXX\",\"related\":true|false,"
    "\"confidence\":0.0-1.0,\"why\":\"max 14 words\"}]}")


def relevance(title: str, ts: int, cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only candidates whose move is about the event: the AI reader judges with each stock's own headlines; without AI, any own headline drops it."""
    for m in cands:
        m["news"] = own_news(m["symbol"], ts - 18 * 3600)
        m.update({k: v for k, v in _profile(m["symbol"]).items() if v is not None and (k != "industry" or not m.get("industry"))})
    named = [m for m in cands if m.get("named")]
    cands = [m for m in cands if not m.get("named")]
    if not cands:
        return named
    try:
        from app.intel import ai_news
        use_ai = ai_news.enabled()
    except Exception:  # noqa: BLE001
        use_ai = False
    if not use_ai:
        return named + [m for m in cands if not m["news"] and m.get("theme") != "OTHER"]
    lines = [f"{m['symbol']} | {m['name']} | {m['industry']} | business: {m.get('about', '')[:200]} | +{m['move'] * 100:.1f}% since the event | own headlines: "
             + ("; ".join(n["title"][:140] for n in m["news"][:4]) or "none") for m in cands]
    user = f"EVENT: {title}" + chr(10) * 2 + "STOCKS:" + chr(10) + chr(10).join(lines)
    out, _ = ai_news._call(ai_news.ANALYSE_MODELS, RELEVANCE_SYSTEM, user, max_tokens=1500)
    verdict = {str(it.get("symbol", "")).upper(): it for it in ((out or {}).get("items") or []) if isinstance(it, dict)}
    keep = list(named)
    for m in cands:
        v = verdict.get(m["symbol"])
        need = 0.75 if m.get("theme") == "OTHER" else 0.6  # outside the obvious industries the AI must be surer
        if v and v.get("related") and float(v.get("confidence") or 0) >= need:
            m["why"] = str(v.get("why") or "")[:120]
            keep.append(m)
        else:
            log.info("mover dropped (not the event): %s %s", m["symbol"], (v or {}).get("why", "no verdict"))
    return keep


# ---------------------------------------------------------------- scanning + alerts
def scan_event(w: dict[str, Any], now: int | None = None) -> list[dict[str, Any]]:
    """Candidates = the event's industries (top 40 each) + the whole small-cap tape (top 80 gainers) + companies named in the headline."""
    now = now or int(time.time())
    found: dict[str, dict[str, Any]] = {}
    themes = json.loads(w.get("themes") or "[]")
    pool: list[tuple[dict[str, Any], str]] = []
    for th in themes:
        for ind in THEME_INDUSTRIES.get(th, []):
            try:
                pool += [(q, th) for q in _screen(ind)[:40]]
            except Exception:  # noqa: BLE001
                log.warning("screener failed for %s", ind)
    theme_inds = {ind for th in themes for ind in THEME_INDUSTRIES.get(th, [])}
    if themes:
        try:
            pool += [(q, "OTHER") for q in _tape()[:80]]
        except Exception:  # noqa: BLE001
            log.warning("small-cap tape screener failed")
    named = json.loads(w.get("symbols") or "[]")
    if named:
        try:
            pool = [(q, "NAMED") for q in _quotes(named)] + pool
        except Exception:  # noqa: BLE001
            log.warning("quotes failed for %s", named)
    for q, th in pool:
        sym = q["symbol"]
        if sym in found:
            continue
        m = evaluate(q, w["ts"], now)
        if not m:
            continue
        if th == "OTHER" and m["industry"] in theme_inds:
            th = next(t for t in themes if m["industry"] in THEME_INDUSTRIES.get(t, []))
        m["theme"], m["named"] = (th if th != "NAMED" else "NAMED IN THE NEWS"), th == "NAMED"
        found[sym] = m
    for m in found.values():  # explosive potential: low float and high short interest move hardest
        pr = _profile(m["symbol"])
        fl, sh = pr.get("float") or 0, pr.get("short") or 0
        m["float"], m["short"] = fl or None, sh or None
        m["score"] *= (1.3 if m["named"] else 1.15 if m["theme"] != "OTHER" else 1.0) * (1.2 if 0 < fl < 20e6 else 1.0) * (1.1 if sh > 0.15 else 1.0)
        if m.get("react_min") is not None and m["react_min"] <= 15:
            m["score"] *= 1.1  # reacted within 15 minutes: the market linked it to the news fast
    ranked = sorted(found.values(), key=lambda m: -m["score"])[:8]
    return relevance(w["title"], w["ts"], ranked) if ranked else []


def _record() -> dict[str, Any]:
    ensure()
    with db.read_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT outcome, best FROM mover_picks WHERE ts_alert > ? AND outcome IS NOT NULL", (int(time.time()) - 60 * 86400,))]
    n = len(rows)
    w = sum(1 for r in rows if r["outcome"] in ("T1", "T2"))
    return {"n": n, "wins": w, "rate": (w / n) if n else None}


def _card(w: dict[str, Any], picks: list[dict[str, Any]]) -> str:
    from html import escape

    when = dt.datetime.fromtimestamp(w["ts"], IL).strftime("%H:%M")
    head = "SMALL CAPS EXPLODING ON THE NEWS" if picks[0]["theme"] in ("OTHER", "NAMED IN THE NEWS") else f"SMALL CAPS EXPLODING · {picks[0]['theme']}"
    lines = [f"🚀 <b>{escape(head)}</b>", f"After: <i>{escape(w['title'][:160])}</i> ({when})", ""]
    for i, m in enumerate(picks, 1):
        fuel = " · ".join(x for x in [f"float {m['float'] / 1e6:,.1f}M" if m.get("float") else "", f"short {m['short'] * 100:.0f}%" if m.get("short") else "",
                                        f"reacted in {m['react_min']} min" if m.get("react_min") is not None else ""] if x)
        lines += [f"<b>{i}) ${m['symbol']}</b> {escape(m['name'])} · ${m['mcap'] / 1e6:,.0f}M · {escape(m['industry'])}" + ("  📰 <b>named in the news</b>" if m.get("named") else ""),
                  f"   <b>+{m['move'] * 100:.1f}%</b> since the news · {m['rvol']:.1f}× normal volume · ${m['dollars'] / 1e6:,.1f}M traded",
                  f"   🎯 T1 <b>{m['t1']:.2f}</b> (+{(m['t1'] / m['price'] - 1) * 100:.0f}%, {escape(m['t1_note'])}) · T2 <b>{m['t2']:.2f}</b> (+{(m['t2'] / m['price'] - 1) * 100:.0f}%, {escape(m['t2_note'])})",
                  f"   ❌ Invalid below <b>{m['inval']:.2f}</b> (price at the news) · now {m['price']:.2f}"] + ([f"   ⛽ {fuel}"] if fuel else [])
        if m.get("why"):
            lines.append(f"   🧠 {escape(m['why'])}")
        lines.append("")
    rec = _record()
    lines.append(f"📊 Track record (60 days): T1 reached before invalidation in <b>{rec['wins']}/{rec['n']}</b> ({rec['rate'] * 100:.0f}%)" if rec["n"] >= 5
                 else f"📊 Track record: building ({rec['n']} finished picks so far)")
    lines.append("<i>Small caps move fast and often give the move back. Chart levels, not advice; no orders are sent.</i>")
    return "\n".join(lines)


def _sent_today() -> int:
    with db.read_conn() as c:
        return c.execute("SELECT COUNT(DISTINCT key || ts_alert) FROM mover_picks WHERE ts_alert > ?", (int(time.time()) - 86400,)).fetchone()[0]


def tick(now: int | None = None, send: bool = True) -> dict[str, Any]:
    """Every couple of minutes: scan the events being watched; alert new breakouts; then follow up on earlier picks."""
    ensure()
    now = now or int(time.time())
    out = {"events": 0, "alerts": 0, "followups": 0}
    if _in_session(dt.datetime.fromtimestamp(now, NY)):
        with db.read_conn() as c:
            ws = [dict(r) for r in c.execute("SELECT * FROM mover_watch WHERE until > ? AND ts <= ?", (now, now))]
            recent = {r[0] for r in c.execute("SELECT symbol FROM mover_picks WHERE ts_alert > ?", (now - SYMBOL_COOLDOWN,))}
        for w in ws:
            age = now - max(w["ts"], _next_open(w["ts"]))
            last = _last_scan.get(w["key"], 0)
            if now - last < (55 if age < FAST_WINDOW else 150):
                continue
            _last_scan[w["key"]] = now
            out["events"] += 1
            picks = [m for m in scan_event(w, now) if m["symbol"] not in recent][:PER_ALERT]
            if not picks or _sent_today() >= MAX_ALERTS_DAY:
                continue
            if send:
                _send(w, picks)
            with db.get_conn() as c:
                for m in picks:
                    c.execute("INSERT INTO mover_picks (key, title, theme, symbol, name, mcap, ts_event, ts_alert, p_event, p_alert, t1, t2, inval, move, rvol, best, worst, float, source) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (w["key"], w["title"], m["theme"], m["symbol"], m["name"], m["mcap"], w["ts"], now, m["p_event"], m["price"], m["t1"], m["t2"], m["inval"],
                               m["move"], m["rvol"], m["price"], m["price"], m.get("float"), "named" if m.get("named") else ("tape" if m["theme"] == "OTHER" else "theme")))
                c.execute("UPDATE mover_watch SET alerts=alerts+1 WHERE key=?", (w["key"],))
            recent |= {m["symbol"] for m in picks}
            out["alerts"] += 1
    out["followups"] = followups(now, send)
    return out


def _send(w: dict[str, Any], picks: list[dict[str, Any]]) -> None:
    from app.notify import telegram_bot

    imgs = []
    for m in picks:
        try:
            imgs.append(chart_png(m, w["ts"], w["title"]))
        except Exception:  # noqa: BLE001
            log.exception("mover chart failed for %s", m["symbol"])
    html = _card(w, picks)
    if len(imgs) >= 2:
        telegram_bot.send_album(imgs, "")
    elif imgs:
        telegram_bot.send_photo(imgs[0], f"${picks[0]['symbol']} · +{picks[0]['move'] * 100:.1f}% since the news")
    telegram_bot.send_rich(html)


def followups(now: int | None = None, send: bool = True) -> int:
    """Track each pick for 2 days: T1 / T2 reached or invalidation first. One short message when the outcome is known."""
    from app.notify import telegram_bot

    now = now or int(time.time())
    with db.read_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM mover_picks WHERE closed=0")]
    n = 0
    for p in rows:
        try:
            bars = [b for b in _bars(p["symbol"]) if b[0] > p["ts_alert"]]
        except Exception:  # noqa: BLE001
            continue
        best, worst, outcome, t_out = p["best"], p["worst"], p["outcome"], p["ts_outcome"]
        for b in bars:
            best, worst = max(best, b[2]), min(worst, b[3])
            if outcome in (None, "T1"):
                if b[3] <= p["inval"] and outcome is None:
                    outcome, t_out = "INVALID", int(b[0])
                elif b[2] >= p["t2"]:
                    outcome, t_out = "T2", int(b[0])
                elif b[2] >= p["t1"] and outcome is None:
                    outcome, t_out = "T1", int(b[0])
        closed = 1 if (outcome in ("T2", "INVALID") or now - p["ts_alert"] > 2 * 86400) else 0
        if closed and outcome is None:
            outcome = "TIMEOUT"
        msg = None
        if outcome and outcome != p["outcome"] and send:
            mins = (t_out - p["ts_alert"]) / 60 if t_out else 0
            took = f"{mins:.0f} min" if mins < 120 else f"{mins / 60:.1f} h"
            if outcome in ("T1", "T2"):
                lvl = p["t1"] if outcome == "T1" else p["t2"]
                msg = f"✅ ${p['symbol']} reached {outcome} {lvl:.2f} (+{(lvl / p['p_alert'] - 1) * 100:.0f}% from the alert) after {took} · event: {p['title'][:80]}"
            elif outcome == "INVALID":
                msg = f"❌ ${p['symbol']} fell back to {p['inval']:.2f} (price at the news) after {took}: the event move failed. Best was +{(best / p['p_alert'] - 1) * 100:.0f}%."
            elif outcome == "TIMEOUT":
                msg = f"⏱ ${p['symbol']}: no target and no invalidation in 2 days. Best +{(best / p['p_alert'] - 1) * 100:.0f}%, worst {(worst / p['p_alert'] - 1) * 100:.0f}% from the alert."
        if msg:
            telegram_bot.send_text(msg)
            n += 1
        with db.get_conn() as c:
            c.execute("UPDATE mover_picks SET best=?, worst=?, outcome=?, ts_outcome=?, closed=? WHERE id=?", (best, worst, outcome, t_out, closed, p["id"]))
    return n


def board(days: int = 14) -> dict[str, Any]:
    ensure()
    with db.read_conn() as c:
        picks = [dict(r) for r in c.execute("SELECT * FROM mover_picks WHERE ts_alert > ? ORDER BY ts_alert DESC LIMIT 100", (int(time.time()) - days * 86400,))]
        ws = [dict(r) for r in c.execute("SELECT * FROM mover_watch WHERE until > ? ORDER BY ts DESC", (int(time.time()),))]
    return {"picks": picks, "watching": ws, "record": _record()}
