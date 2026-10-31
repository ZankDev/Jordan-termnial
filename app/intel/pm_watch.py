"""Polymarket shock detector: a sharp move in a market-relevant probability, often before the headline exists.

Every 5 minutes the top markets by 24h volume are snapshotted (only events Polymarket tags with a finance / macro / geopolitics tag; sports and culture never).
A SHOCK is a move of >= 10 points in about an hour (our own snapshots, or Polymarket's oneHourPriceChange) or >= 20 points in a day, in an event that traded
>= $50k in 24h. Thin markets are easy to push around, so they are ignored. Each market alerts at most once every 6 hours.
The card says whether matching news exists yet: "no news yet" is the interesting case, the crowd may know first.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
MIN_VOL = 50_000
HOUR_PT, DAY_PT = 10.0, 20.0
COOLDOWN = 6 * 3600
MIN_AGE = 36 * 3600  # markets younger than this are skipped (opening price discovery)
# price-ladder markets only mirror the spot price (Bitcoin above X today), so their swings carry no news
PRICE_LADDER = r"up or down|above _+|below _+|price on|what price|price will|hit.*\$|close (above|below)|price (at|by|end)|high.*on"
MAX_PER_HOUR = 3
EXTRA_TAGS = {"economy", "finance", "stocks", "business", "geopolitics", "middle east", "commodities", "trade", "recession", "fed", "tariffs"}
SCHEMA = ("CREATE TABLE IF NOT EXISTS pm_ticks (ts INTEGER, market_id TEXT, prob REAL)",
          "CREATE INDEX IF NOT EXISTS pm_ticks_m ON pm_ticks (market_id, ts)",
          "CREATE TABLE IF NOT EXISTS pm_shocks (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, market_id TEXT, slug TEXT, title TEXT, question TEXT, p0 REAL, p1 REAL, "
          "win TEXT, vol24 REAL, news TEXT, pushed INTEGER DEFAULT 0)")


def ensure() -> None:
    with db.get_conn() as conn:
        for s in SCHEMA:
            conn.execute(s)


def _wanted_tags() -> set[str]:
    from app.intel import impact

    tags = set(EXTRA_TAGS)
    for d in impact.TOPICS.values():
        tags |= {t.lower() for t in d["tags"]}
    return tags


def _bad_tags() -> set[str]:
    from app.intel import impact

    return {t.lower() for t in impact.BAD_TAGS}


def snapshot() -> list[dict[str, Any]]:
    """Relevant live markets: id, event, question, probability (%), Polymarket's own 1h/24h change (points), event 24h volume."""
    r = requests.get("https://gamma-api.polymarket.com/events", params={"active": "true", "closed": "false", "order": "volume24hr", "ascending": "false", "limit": 300},
                     headers=UA, timeout=25)
    r.raise_for_status()
    want, bad = _wanted_tags(), _bad_tags()
    out = []
    for ev in r.json():
        tags = {str(t.get("label", "")).lower() for t in ev.get("tags") or []}
        if not tags & want or tags & bad:
            continue
        vol = float(ev.get("volume24hr") or 0)
        if re.search(PRICE_LADDER, str(ev.get("title", "")).lower()):
            continue
        for m in ev.get("markets") or []:
            if m.get("closed"):
                continue
            try:  # a market resolving within a day drifts to 0/100 on its own
                import datetime as _dt
                end = m.get("endDate") or ev.get("endDate")
                if end and _dt.datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp() - time.time() < 86400:
                    continue
            except ValueError:
                pass
            try:
                p = m.get("outcomePrices")
                p = json.loads(p) if isinstance(p, str) else p
                prob = float(p[0]) * 100
            except Exception:  # noqa: BLE001
                continue
            try:  # a brand-new market jumps around while it finds its first price: that is not news
                born = m.get("acceptingOrdersTimestamp") or m.get("startDate") or m.get("createdAt")
                if born and time.time() - _dt.datetime.fromisoformat(born.replace("Z", "+00:00")).timestamp() < MIN_AGE:
                    continue
            except ValueError:
                pass
            try:
                ids = m.get("clobTokenIds")
                tok = (json.loads(ids) if isinstance(ids, str) else ids or [None])[0]
            except Exception:  # noqa: BLE001
                tok = None
            out.append({"market_id": str(m.get("id")), "token": tok, "slug": ev.get("slug"), "title": ev.get("title"), "question": m.get("groupItemTitle") or m.get("question"),
                        "prob": round(prob, 1), "h1": None if m.get("oneHourPriceChange") is None else float(m["oneHourPriceChange"]) * 100,
                        "d1": None if m.get("oneDayPriceChange") is None else float(m["oneDayPriceChange"]) * 100, "vol24": vol,
                        "tags": sorted(tags & want)})
    return out


def _news_for(title: str, question: str) -> list[str]:
    from app.intel import feeds

    stop = {"will", "what", "when", "with", "before", "after", "decision", "market", "price", "by", "the", "and", "end", "of", "in", "2026", "2027"}
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", f"{title} {question}")} - stop
    hits = []
    for it in feeds.fetch_all(max_age=120):
        if it["ts"] < time.time() - 7200:
            continue
        tw = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", it["title"])}
        if len(words & tw) >= 2:
            hits.append(it["title"])
    return hits[:3]


def card(s: dict[str, Any]) -> tuple[str, list[list[dict]]]:
    from html import escape

    from app.intel import impact

    info = impact.understand(f"{s['title']} {s['question']}", None)
    d = s["p1"] - s["p0"]
    L = [f"⚡ <b>POLYMARKET SHOCK</b>   <code>{escape(' · '.join(info['topics']) or 'MARKETS')}</code>", "",
         f"<b>{escape(str(s['title']))}</b>" + (f"\n<i>{escape(str(s['question']))}</i>" if s["question"] and s["question"] != s["title"] else ""), "",
         f"<code>{s['p0']:.0f}% → {s['p1']:.0f}%   {'▲' if d > 0 else '▼'}{abs(d):.0f}pt in {s['window']}</code>", f"<i>24h volume ${s['vol24']:,.0f}</i>", ""]
    news = json.loads(s["news"]) if isinstance(s.get("news"), str) else (s.get("news") or [])
    if news:
        L += ["📰 <b>Matching news</b>"] + [f"• {escape(n[:110])}" for n in news] + [""]
    else:
        L += ["📰 <b>No matching news yet</b> — the crowd may know first.", ""]
    if info["themes"]:
        L += ["👀 <b>Watch</b>: " + escape(", ".join(impact.SHORT.get(t, t) for t in info["themes"][:5])), ""]
    L.append("<i>NOSTRO Intel · crowd odds move fast and reverse; check volume</i>")
    return "\n".join(L), [[{"text": "🎲 Open on Polymarket", "url": f"https://polymarket.com/event/{s['slug']}"}]]


# theme -> one liquid proxy for the "did markets react?" panel
THEME_PROXY = {"OIL & ENERGY": ("CL=F", "WTI crude"), "DEFENCE": ("ITA", "US defence ETF"), "GOLD & MINERS": ("GC=F", "Gold"),
               "URANIUM & NUCLEAR": ("URA", "Uranium ETF"), "CYBER": ("CIBR", "Cyber ETF"), "ISRAEL": ("EIS", "Israel ETF"),
               "TANKERS & SHIPPING": ("FRO", "Frontline (tankers)"), "AIRLINES & TRAVEL": ("JETS", "Airlines ETF"), "BONDS": ("TLT", "20y Treasuries"),
               "BANKS": ("XLF", "Financials"), "HOMEBUILDERS": ("ITB", "Homebuilders"), "GROWTH TECH": ("NQ=F", "Nasdaq futures"),
               "CRYPTO": ("BTC-USD", "Bitcoin"), "CHINA": ("FXI", "China large caps"), "AGRICULTURE": ("DBA", "Agriculture"), "DOLLAR": ("DX-Y.NYB", "Dollar index")}


def _prob_history(token: str) -> list[tuple[int, float]]:
    r = requests.get("https://clob.polymarket.com/prices-history", params={"market": token, "interval": "1w", "fidelity": 30}, headers=UA, timeout=20)
    r.raise_for_status()
    return [(int(h["t"]), float(h["p"]) * 100) for h in r.json().get("history", [])]


def chart_png(s: dict[str, Any], themes: list[str]) -> bytes | None:
    """Odds over the last week (shock window shaded) + the first watched asset over the same hours, so the card shows whether markets reacted."""
    import datetime as dt
    import io
    from zoneinfo import ZoneInfo

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    if not s.get("token"):
        return None
    hist = _prob_history(s["token"])
    if len(hist) < 3:
        return None
    IL = ZoneInfo("Asia/Jerusalem")
    now = time.time()
    hist.append((int(now), s["p1"]))
    t0 = hist[0][0]
    proxy = next((THEME_PROXY[t] for t in themes if t in THEME_PROXY), ("ES=F", "S&P futures"))
    px: list[tuple[int, float]] = []
    try:
        from app.api import invest_routes as ir

        rng = "5d" if now - t0 < 4.5 * 86400 else "1mo"
        px = [(int(c[0]), float(c[4])) for c in ir._history(proxy[0], rng, "15m", prepost=True)["candles"] if c[4] and c[0] >= t0 - 1800]
    except Exception:  # noqa: BLE001
        px = []
    BG, FG, DIM, ORA, GRN, RED, BLU = "#0b0b0d", "#ececf1", "#7d7d88", "#ffa028", "#2be07e", "#ff4a4a", "#4aa8ff"
    up = s["p1"] >= s["p0"]
    col = GRN if up else RED
    fig = plt.figure(figsize=(10.8, 6.4), dpi=110, facecolor=BG)
    ax = fig.add_axes([0.06, 0.36, 0.86, 0.5], facecolor=BG)
    av = fig.add_axes([0.06, 0.09, 0.86, 0.2], facecolor=BG, sharex=ax)
    xs = [dt.datetime.fromtimestamp(t, IL) for t, _ in hist]
    ys = [p for _, p in hist]
    ax.step(xs, ys, where="post", color=col, lw=2.2, zorder=3)
    ax.fill_between(xs, ys, 0, step="post", color=col, alpha=0.12, zorder=2)
    win = 86400 if "24" in s["window"] else 3600
    ws = dt.datetime.fromtimestamp(max(now - win, t0), IL)
    ax.axvspan(ws, xs[-1], color=ORA, alpha=0.13, zorder=1)
    ax.text(ws, 101, f" SHOCK · {s['window']} ", color=BG, fontsize=8.5, fontweight="bold", va="bottom", bbox=dict(boxstyle="round,pad=0.2", fc=ORA, ec="none"))
    ax.scatter([xs[-1]], [ys[-1]], color=col, s=40, zorder=4)
    ax.set_ylim(0, 108)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    if px:
        pxs = [dt.datetime.fromtimestamp(t, IL) for t, _ in px]
        base = next((v for t, v in px if t >= now - win), px[0][1])
        pv = [(v / base - 1) * 100 for _, v in px]
        av.plot(pxs, pv, color=BLU, lw=1.5)
        av.axhline(0, color=DIM, lw=0.6, alpha=0.6)
        av.axvspan(ws, xs[-1], color=ORA, alpha=0.13)
        chg = (px[-1][1] / base - 1) * 100
        av.text(0.005, 0.92, f"{proxy[1]} ({proxy[0]})  {chg:+.2f}% since the shock window began", transform=av.transAxes, color=BLU, fontsize=9, va="top", fontweight="bold")
        av.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.1f}%"))
    else:
        av.text(0.5, 0.5, "market data unavailable", transform=av.transAxes, color=DIM, ha="center")
    for a in (ax, av):
        a.tick_params(colors=DIM, labelsize=8)
        a.yaxis.tick_right()
        for sp in a.spines.values():
            sp.set_color("#26262c")
        a.grid(color="#1b1b21", lw=0.6)
    av.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M", tz=IL))
    plt.setp(ax.get_xticklabels(), visible=False)
    ax.set_xlim(xs[0], xs[-1] + (xs[-1] - xs[0]) * 0.02)
    q, t = str(s["question"] or ""), str(s["title"] or "")
    fig.text(0.06, 0.935, (q if q and q != t else t)[:60], color=FG, fontsize=17, fontweight="bold")
    if q and q != t:
        fig.text(0.06, 0.895, t[:110], color=DIM, fontsize=9.5)
    fig.text(0.92, 0.935, f"{s['p0']:.0f}% → {s['p1']:.0f}%", color=col, fontsize=19, fontweight="bold", ha="right")
    fig.text(0.92, 0.895, f"{'▲' if up else '▼'}{abs(s['p1'] - s['p0']):.0f}pt in {s['window']}  ·  24h vol ${s['vol24'] / 1e3:,.0f}k", color=DIM, fontsize=9, ha="right")
    fig.text(0.06, 0.02, "◆ JORDAN TERMINAL · POLYMARKET SHOCK · odds = Polymarket price of this outcome, Israel time", color=DIM, fontsize=8)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def tick(push: bool = True) -> dict[str, int]:
    """Snapshot, detect shocks, push cards. Called from the intel loop every 5 minutes."""
    from app.notify import telegram_bot

    ensure()
    now = int(time.time())
    try:
        snap = snapshot()
    except Exception:  # noqa: BLE001
        logger.exception("polymarket snapshot failed")
        return {"markets": 0, "shocks": 0}
    with db.get_conn() as conn:
        if conn.execute("SELECT COUNT(*) FROM pm_ticks").fetchone()[0] == 0:
            push = False  # very first run: record what is already moving without pushing a burst of stale alerts
        conn.executemany("INSERT INTO pm_ticks (ts, market_id, prob) VALUES (?,?,?)", [(now, m["market_id"], m["prob"]) for m in snap])
        conn.execute("DELETE FROM pm_ticks WHERE ts < ?", (now - 48 * 3600,))
        recent_push = conn.execute("SELECT COUNT(*) FROM pm_shocks WHERE pushed=1 AND ts > ?", (now - 3600,)).fetchone()[0]
    found = 0
    cands: dict[str, dict[str, Any]] = {}
    for m in snap:
        if m["vol24"] < MIN_VOL:
            continue
        with db.get_conn() as conn:
            old = conn.execute("SELECT prob FROM pm_ticks WHERE market_id=? AND ts BETWEEN ? AND ? ORDER BY ts LIMIT 1", (m["market_id"], now - 4200, now - 3000)).fetchone()
            last = conn.execute("SELECT MAX(ts) FROM pm_shocks WHERE market_id=?", (m["market_id"],)).fetchone()[0]
        if last and now - last < COOLDOWN:
            continue
        p1 = m["prob"]
        if old is not None and abs(p1 - old[0]) >= HOUR_PT:
            p0, window = old[0], "~1h"
        elif m["h1"] is not None and abs(m["h1"]) >= HOUR_PT:
            p0, window = p1 - m["h1"], "1h"
        elif m["d1"] is not None and abs(m["d1"]) >= DAY_PT:
            p0, window = p1 - m["d1"], "24h"
        else:
            continue
        c = {**m, "p0": round(p0, 1), "p1": p1, "window": window}
        if m["slug"] not in cands or abs(p1 - p0) > abs(cands[m["slug"]]["p1"] - cands[m["slug"]]["p0"]):
            cands[m["slug"]] = c
    for s in cands.values():
        m = s
        with db.get_conn() as conn:
            last_ev = conn.execute("SELECT MAX(ts) FROM pm_shocks WHERE slug=?", (s["slug"],)).fetchone()[0]
        if last_ev and now - last_ev < COOLDOWN:
            continue
        news = _news_for(m["title"] or "", m["question"] or "")
        s["news"] = news
        p1, window = s["p1"], s["window"]
        do_push = push and recent_push < MAX_PER_HOUR
        with db.get_conn() as conn:
            conn.execute("INSERT INTO pm_shocks (ts, market_id, slug, title, question, p0, p1, win, vol24, news, pushed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (now, m["market_id"], m["slug"], m["title"], m["question"], s["p0"], p1, window, m["vol24"], json.dumps(news), int(do_push)))
        found += 1
        if do_push:
            html, b = card(s)
            png = None
            try:
                from app.intel import impact

                png = chart_png(s, impact.understand(f"{s['title']} {s['question']}", None)["themes"])
            except Exception:  # noqa: BLE001
                logger.exception("polymarket shock chart failed")
            if not (png and len(html) <= 1024 and telegram_bot.send_photo_rich(png, html, b)):
                telegram_bot.send_rich(html, b)
            recent_push += 1
    return {"markets": len(snap), "shocks": found}


def recent(hours: float = 24) -> list[dict[str, Any]]:
    ensure()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM pm_shocks WHERE ts > ? ORDER BY ts DESC LIMIT 50", (int(time.time() - hours * 3600),)).fetchall()]
    for r in rows:
        r["news"] = json.loads(r["news"] or "[]")
        r["window"] = r.pop("win", "")
    return rows
