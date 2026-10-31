"""News impact engine: what type of news this is, and did the market actually move from the moment it broke.

1. TYPE: a rule taxonomy (English + Hebrew keywords) -> one of ~20 news types, plus a direction where the type has one.
2. ASSETS: each type has the assets it can move (index futures, oil, gold, the dollar, 10y notes, bitcoin, the shekel), plus any $TICKER named.
3. REACTION from the story time (earliest source): returns over the 15 minutes BEFORE (leak / already priced), and +5 / +15 / +60 minutes after,
   on 1-minute bars inside Yahoo's 7-day window, 5-minute bars up to 60 days. Each return is turned into z = return / typical move of that asset over
   the same horizon at that hour of the day (robust MAD estimate from the asset's own recent bars), so "moved" means unusual for that asset and hour.
4. VERDICT: MOVED (primary |z15| >= 2.5, or two assets >= 2), PRICED BEFORE (the move came in the 15 min before), ABSORBED (moved, then fully reversed
   within 60 min), NO REACTION. With a plain explanation listing the assets that moved.
5. LEARNING: per type and per type x source kind, P(MOVED) and the typical |z|, used as the EXPECTED impact of a new story before the reaction exists.
   Small samples are shown with n; until a type has 15 measured stories its prior (below) is blended in.
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from app import db
from app.intel import sources

logger = logging.getLogger(__name__)

# name, regex (lower-case text), assets, prior P(MOVED)
TYPES: list[tuple[str, str, list[str], float]] = [
    ("noise", r"opinion|podcast|explainer|analysis:|column:|week ahead|what to know|live updates|newsletter|quiz|recap|how to |watch:|פרשנות|טור דעה|הסבר", ["ES=F"], 0.02),
    ("fed", r"\bfed\b|fomc|powell|warsh|federal reserve|rate (cut|hike|decision)|basis points|\bbps\b|fed's|dot plot", ["ES=F", "NQ=F", "ZN=F", "DX-Y.NYB", "GC=F"], 0.25),
    ("inflation_data", r"\bcpi\b|\bppi\b|\bpce\b|inflation (rate|rises|falls|eases|accelerat)|core prices|אינפלציה|מדד המחירים", ["ES=F", "NQ=F", "ZN=F", "DX-Y.NYB", "GC=F"], 0.45),
    ("jobs_data", r"payroll|nonfarm|non-farm|jobless claims|unemployment rate|\badp\b|jolts", ["ES=F", "NQ=F", "ZN=F", "DX-Y.NYB"], 0.4),
    ("us_data", r"retail sales|\bgdp\b|\bism\b|\bpmi\b|consumer (sentiment|confidence)|durable goods|housing starts|trade balance|factory orders|industrial production",
     ["ES=F", "ZN=F", "DX-Y.NYB"], 0.25),
    ("agriculture", r"\bcorn\b|wheat|soybean|\bsoy\b|\busda\b|grain|cattle|hogs|cocoa|coffee|sugar", ["ZC=F", "ZW=F", "ZS=F"], 0.2),
    ("earnings", r"earnings|\beps\b|revenue|guidance|outlook|beats|misses|quarterly (profit|results)|\bq[1-4]\b|דוחות|רווח רבעוני", ["ES=F"], 0.3),
    ("mna", r"acquire|acquisition|merger|takeover|buyout|to buy .* for \$|deal to buy|מיזוג|רכישה", ["ES=F"], 0.3),
    ("regulatory", r"\bfda\b|approval|antitrust|\bdoj\b|\bftc\b|\bsec (charges|sues|probe)|lawsuit|investigation into|recall", ["ES=F"], 0.15),
    ("analyst", r"upgrade|downgrade|price target|initiat(es|ed) .* (buy|sell|overweight|underweight|neutral)|overweight|underweight", ["ES=F"], 0.1),
    ("deescalation", r"ceasefire|cease-fire|truce|peace (talks|deal|plan)|deal reached|hostage(s)? (release|deal)|over soon|de-escalat|withdraw|הפסקת אש|הסכם (שלום|הפסקת|נורמליזציה)|עסקת (חטופים|החטופים)|שחרור חטופים",
     ["CL=F", "GC=F", "ES=F", "ILS=X"], 0.2),
    ("war_escalation", r"strikes?(?! (a |an )?(deal|agreement|pact))|missile|attack|sirens|explosion|drone|invad|airstrike|shelling|killed|bomb|hormuz|tanker|warship|mobiliz|retaliat|escalat|nuclear|"
     r"טילים|תקיפה|אזעק|פיגוע|חיסול|יירוט|רקטות|כטב\"ם|פיצוץ", ["CL=F", "GC=F", "ES=F", "ILS=X"], 0.15),
    ("trade_policy", r"tariff|sanction|export control|trade (deal|war|talks)|embargo|import tax|duties|מכסים|סנקציות", ["ES=F", "NQ=F", "DX-Y.NYB", "CNY=X"], 0.25),
    ("energy", r"\bopec|crude|\boil\b|brent|\bwti\b|diesel|gasoline|natural gas|\blng\b|\blpg\b|refiner|pipeline|(?<![א-ת])[הבו]?נפט(?![א-ת])|גז טבעי|מאגר הגז|לוויתן", ["CL=F", "NG=F", "ES=F"], 0.25),
    ("crypto", r"bitcoin|\bbtc\b|ethereum|\beth\b|crypto|stablecoin|solana|coinbase|binance", ["BTC-USD", "ETH-USD"], 0.15),
    ("israel_macro", r"bank of israel|בנק ישראל|shekel (falls|rises|weakens|strengthens|slides|jumps)|(?<![א-ת])השקל (נחלש|מתחזק|התחזק|נחלש|צונח|זינק)|(?<![05d0-05ea])הדולר (עולה|יורד|נחלש|מתחזק|זינק|צונח|קופץ)|שער הדולר|שער החליפין|ת\"א ?(35|125|90)|tase|"
     r"בורסת תל אביב|(?<![א-ת])הנגיד|אמיר ירון|ריבית בנק ישראל|הריבית במשק|דירוג האשראי של ישראל|moody's.*israel|s&p.*israel|israel.*(rating|credit)", ["ILS=X"], 0.2),
    ("politics", r"election|\bpoll\b|senate|congress|shutdown|impeach|appoint|supreme court|knesset|בחירות|כנסת|קואליציה", ["ES=F", "DX-Y.NYB"], 0.05),
    ("disaster", r"earthquake|hurricane|wildfire|outage|cyberattack|cyber attack|blackout|flood|רעידת אדמה", ["ES=F", "CL=F"], 0.05),
    ("flows", r"buyback|block trade|\bstake\b|13f|inflows|outflows|short seller|trading halt|halted|circuit breaker", ["ES=F"], 0.15),
]
TRUMP_ASSETS = ["ES=F", "NQ=F", "DX-Y.NYB", "CL=F"]
LABEL = {"ES=F": "S&P fut", "NQ=F": "Nasdaq fut", "ZN=F": "10y note", "DX-Y.NYB": "Dollar", "GC=F": "Gold", "CL=F": "Oil", "NG=F": "Nat gas", "ILS=X": "USD/ILS", "ZC=F": "Corn", "ZW=F": "Wheat", "ZS=F": "Soybeans",
         "CNY=X": "USD/CNY", "BTC-USD": "Bitcoin", "ETH-USD": "Ether"}
UP = r"rise|rises|rose|jump|surge|soar|beat|above|higher|hike|hawkish|strong|gain|rall|record|accelerat|upgrade|approv"
DOWN = r"fall|falls|fell|drop|plunge|slump|miss|below|lower|cut|dovish|weak|loss|decline|slow|downgrade|reject|halt"
Z_MOVED, Z_TWO = 2.5, 2.0
_bars: dict[str, tuple[float, Any]] = {}


# ---------------------------------------------------------------- classify
# local crime, accidents, real estate and consumer stories: never market-moving, even when they mention a price in shekels or a gun
LOCAL = (r"נעצר|חשוד|מצוד|שוטר|המשטרה|הפרת סדר|דקירה|רצח|נרצח|תאונ|נפצע קל|בית המשפט|כתב אישום|גניבה|שוד|ירי לעבר|נורה|קטטה|"
         r"דירה|דירות|מגרש|נדל\"ן|בית פרטי|מכונית|רכב חדש|מתכון|טיסה|חופשה|"
         r"arrested|suspect|police said|stabbing|car crash|real estate listing|recipe")
NOT_LOCAL = r"פיגוע|מחבל|טיל|רקט|כטב\"ם|צה\"ל|חיזבאללה|חמאס|איראן|חות'ים|terror|missile|rocket|idf|hezbollah|hamas|iran"


def classify(text: str, kinds: list[str] | None = None) -> dict[str, Any]:
    t = (text or "").lower()
    serious = bool(re.search(NOT_LOCAL + r"|hormuz|irgc|הורמוז", t))
    ntype = next((name for name, rx, _, _ in TYPES if (name != "noise" or not serious) and re.search(rx, t)), None)
    if ntype not in ("fed", "inflation_data", "jobs_data", "us_data") and re.search(LOCAL, t) and not serious:
        ntype = "noise"
    if ntype == "deescalation" and re.search(r"reject|collapse|breaks? down|violat|stall|fails?|no deal|דחה|קרס|הפר", t):
        ntype = "war_escalation"  # a ceasefire that is rejected or breaks is escalation
    if ntype is None and (t.startswith("trump") or "truth" in (kinds or [])):
        ntype = "trump_policy"
    tickers = sorted(set(sources.TICKER.findall(text or "")))
    if ntype is None:
        ntype = "company" if tickers else "other"
    assets = list(next((a for name, _, a, _ in TYPES if name == ntype), TRUMP_ASSETS if ntype == "trump_policy" else ["ES=F"]))
    assets = tickers[:3] + [a for a in assets if a not in tickers]
    up, dn = bool(re.search(UP, t)), bool(re.search(DOWN, t))
    return {"ntype": ntype, "assets": assets[:6], "direction": "up" if up and not dn else "down" if dn and not up else None, "tickers": tickers}


# ---------------------------------------------------------------- prices
def _series(sym: str, fine: bool) -> list[tuple[int, float]] | None:
    """1-minute bars for the last 7 days (fine) or 5-minute bars for 60 days, pre/post market included. Cached."""
    from app.api import invest_routes as ir

    key = f"{sym}:{'1m' if fine else '5m'}"
    hit = _bars.get(key)
    if hit and time.time() - hit[0] < (90 if fine else 600):
        return hit[1]
    try:
        h = ir._history(sym, "7d" if fine else "60d", "1m" if fine else "5m", prepost=True)
        val = [(int(c[0]), float(c[4])) for c in h["candles"] if c[4]]
    except Exception:  # noqa: BLE001
        val = None
    _bars[key] = (time.time(), val)
    return val


def _at(bars: list[tuple[int, float]], ts: int, after: bool = False) -> tuple[int, float] | None:
    """Last bar at or before ts (or first bar at or after ts when after=True)."""
    lo, hi = 0, len(bars)
    while lo < hi:
        mid = (lo + hi) // 2
        if bars[mid][0] <= ts:
            lo = mid + 1
        else:
            hi = mid
    if after:
        return bars[lo] if lo < len(bars) else None
    return bars[lo - 1] if lo > 0 else None


_typ_cache: dict[tuple[int, int, int], float] = {}


def _typical(bars: list[tuple[int, float]], horizon: int, hour: int, step: int) -> float:
    """Robust typical |return| over `horizon` seconds for this asset at this UTC hour (MAD x 1.4826), falling back to all hours. Memoised per series."""
    key = (id(bars), horizon, hour)
    if key in _typ_cache:
        return _typ_cache[key]
    if len(_typ_cache) > 5000:
        _typ_cache.clear()
    k = max(1, horizon // step)
    arr = np.asarray(bars, dtype=float)
    if len(arr) <= k + 30:
        return 0.0
    px = arr[:, 1]
    r = px[k:] / px[:-k] - 1
    hrs = (arr[:-k, 0] // 3600 % 24).astype(int)
    sel = r[hrs == hour] if (hrs == hour).sum() > 40 else r
    mad = float(np.median(np.abs(sel - np.median(sel)))) * 1.4826
    out = mad if mad > 0 else float(np.std(sel))
    _typ_cache[key] = out
    return out


def measure(ts: int, assets: list[str]) -> dict[str, Any]:
    """Returns and z-scores per asset around the story time. `ready` is False until +15 min of bars exist."""
    fine = time.time() - ts < 6.5 * 86400
    step = 60 if fine else 300
    out: dict[str, Any] = {}
    with ThreadPoolExecutor(6) as ex:
        series = dict(zip(assets, ex.map(lambda s: _series(s, fine), assets)))
    for sym in assets:
        bars = series.get(sym)
        if not bars:
            continue
        base = _at(bars, ts)
        if base is None:
            continue
        closed = ts - base[0] > 20 * 60  # no trade within 20 min before the story: market was closed
        if closed:
            nxt = _at(bars, ts, after=True)
            if nxt is None:
                continue
            base = (nxt[0], base[1])  # reaction measured from the next print vs the last pre-story price (includes the gap)
        hour = time.gmtime(base[0]).tm_hour
        row: dict[str, Any] = {"closed": closed}
        pre = _at(bars, ts - 15 * 60)
        if pre and not closed:
            tp = _typical(bars, 900, hour, step)
            r = base[1] / pre[1] - 1
            row["pre"], row["zpre"] = round(r * 100, 3), round(r / tp, 1) if tp else None
        for name, sec in (("r5", 300), ("r15", 900), ("r60", 3600)):
            end = _at(bars, base[0] + sec)
            if end is None or bars[-1][0] < base[0] + sec - step:
                continue
            tp = _typical(bars, sec, hour, step)
            r = end[1] / base[1] - 1
            row[name], row["z" + name[1:]] = round(r * 100, 3), (round(r / tp, 1) if tp else None)
        out[sym] = row
    prim = assets[0] if assets else None
    ready = bool(prim and prim in out and "r15" in out[prim]) or any("r15" in v for v in out.values())
    return {"assets": out, "primary": prim, "ready": ready, "res": "1m" if fine else "5m"}


def verdict(m: dict[str, Any]) -> tuple[str, str]:
    a, prim = m["assets"], m["primary"]
    if not a:
        return "NO DATA", "No price data for the assets this story could move."
    z15 = {s: v.get("z15") for s, v in a.items() if v.get("z15") is not None}
    big = sorted([s for s, z in z15.items() if abs(z) >= Z_TWO], key=lambda s: -abs(z15[s]))
    pz = z15.get(prim)
    closed = any(v.get("closed") for v in a.values())
    pre_big = [s for s, v in a.items() if v.get("zpre") is not None and abs(v["zpre"]) >= Z_MOVED and abs(z15.get(s) or 0) < Z_TWO]

    def fmt(s):
        v = a[s]
        return f"{lab(s)} {v['r15']:+.2f}% (z {v['z15']:+.1f})"
    if (pz is not None and abs(pz) >= Z_MOVED) or len(big) >= 2:
        movers = big or [prim]
        absorbed = all(a[s].get("r60") is not None and abs(a[s]["r60"]) < abs(a[s]["r15"]) * 0.2 for s in movers)
        quiet = [lab(s) for s, z in z15.items() if abs(z) < 1 and s not in movers]
        txt = "Moved within 15 min: " + ", ".join(fmt(s) for s in movers[:4]) + (f"; {', '.join(quiet[:2])} ignored it" if quiet else "") + "."
        if closed:
            txt += " (market was closed at the time: measured from the next print, gap included)"
        if absorbed:
            return "ABSORBED", txt + " Fully reversed within 60 min."
        return "MOVED", txt
    if pre_big:
        s = pre_big[0]
        return "PRICED BEFORE", f"{lab(s)} moved {a[s]['pre']:+.2f}% (z {a[s]['zpre']:+.1f}) in the 15 min BEFORE the story, then nothing: it was already known or leaked."
    parts = [f"{lab(s)} {a[s]['r15']:+.2f}%" for s in list(z15)[:3]]
    return "NO REACTION", "Nothing unusual within 15 min (" + ", ".join(parts) + ")."


# ---------------------------------------------------------------- learning
def stats(min_n: int = 1) -> dict[str, Any]:
    """P(MOVED) and typical |z15| per type and per type x source kind, from measured stories."""
    sources.ensure()
    with db.read_conn() as conn:
        rows = conn.execute("SELECT ntype, kinds, verdict, measured FROM news_stories WHERE verdict IS NOT NULL AND verdict != 'NO DATA' AND ts > ?",
                            (int(time.time() - 120 * 86400),)).fetchall()
    by: dict[str, list[tuple[bool, float]]] = {}
    for r in rows:
        moved = r["verdict"] in ("MOVED", "ABSORBED")
        try:
            m = json.loads(r["measured"] or "{}")
            pz = abs((m.get("assets", {}).get(m.get("primary"), {}) or {}).get("z15") or 0)
        except ValueError:
            pz = 0.0
        for k in [r["ntype"]] + [f"{r['ntype']}|{kd}" for kd in json.loads(r["kinds"] or "[]")]:
            by.setdefault(k, []).append((moved, pz))
    out = {}
    for k, v in by.items():
        if len(v) >= min_n:
            out[k] = {"n": len(v), "p_moved": round(sum(1 for m, _ in v if m) / len(v), 3), "med_z": round(float(np.median([z for _, z in v])), 2)}
    return out


def expected(ntype: str, kinds: list[str], st: dict[str, Any] | None = None) -> float:
    """Expected P(MOVED) for a new story: learned rate blended with the type prior until 15 stories exist (more weight to the fastest source kind)."""
    st = st if st is not None else stats()
    prior = next((p for name, _, _, p in TYPES if name == ntype), 0.15 if ntype == "trump_policy" else 0.05)
    best = None
    for k in [f"{ntype}|{kd}" for kd in kinds] + [ntype]:
        if k in st and st[k]["n"] >= 5:
            best = st[k]
            break
    if not best:
        return prior
    w = min(1.0, best["n"] / 15)
    return round(w * best["p_moved"] + (1 - w) * prior, 3)


# ---------------------------------------------------------------- pipeline
def process(stories: list[int] | None = None, limit: int = 400) -> dict[str, int]:
    """Classify new stories, measure stories that are 16+ minutes old, finalise at 61+ minutes. Returns counts."""
    sources.ensure()
    now = int(time.time())
    st = stats()
    done = {"classified": 0, "measured": 0}
    cls_rows: list[tuple] = []
    with db.read_conn() as conn:
        new = [dict(r) for r in conn.execute("SELECT id, ts, title, kinds FROM news_stories WHERE ntype IS NULL ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()]
    for s in new:
        c = classify(s["title"], json.loads(s["kinds"] or "[]"))
        exp = expected(c["ntype"], json.loads(s["kinds"] or "[]"), st)
        cls_rows.append((c["ntype"], c["direction"], exp, s["id"]))
    if cls_rows:  # one lock acquisition for the whole batch (the shared DB lock is busy on the live server)
        with db.get_conn() as conn:
            conn.executemany("UPDATE news_stories SET ntype=?, direction=?, expected=?, status='classified' WHERE id=?", cls_rows)
        done["classified"] = len(cls_rows)
    try:  # the language model re-reads fresh stories: overrides the keyword type/expected and names the assets to watch
        from app.intel import ai_news
        done.update(ai_news.run())
    except Exception:  # noqa: BLE001
        logger.exception("ai news reading failed (keyword rules still apply)")
    upd: list[tuple] = []
    nodata: list[tuple] = []
    with db.read_conn() as conn:
        due = [dict(r) for r in conn.execute(
            "SELECT id, ts, title, kinds, ntype, n_sources, status, ai FROM news_stories WHERE ntype IS NOT NULL AND ntype NOT IN ('noise') AND "
            "((status='classified' AND ts < ?) OR (status='measured' AND ts < ?)) AND ts > ? ORDER BY ts DESC LIMIT ?",
            (now - 16 * 60, now - 61 * 60, now - 59 * 86400, min(limit, 120))).fetchall()]
    for s in due:
        c = classify(s["title"], json.loads(s["kinds"] or "[]"))
        an = _ai(s).get("analysis") or {}
        comps = (_ai(s).get("triage") or {}).get("companies") or []
        assets = [x for c in comps for x in (c["symbol"], c.get("us")) if x][:4]  # the companies in the story first (home listing + US listing)
        assets += [a["symbol"] for a in an.get("affected", []) if a["symbol"] not in assets][:6]
        assets += [a for a in c["assets"] if a not in assets][:max(2, 8 - len(assets))]
        m = measure(s["ts"], assets)
        if comps and "r15" not in (m["assets"].get(comps[0]["symbol"]) or {}) and now - s["ts"] < 24 * 3600:
            continue  # wait for the home market to open (a Tokyo stock on news during New York hours), up to a day
        if not m["ready"]:
            if now - s["ts"] > 3 * 86400:
                nodata.append((s["id"],))
            continue
        v, why = verdict(m)
        with db.read_conn() as conn:
            others = conn.execute("SELECT COUNT(*) FROM news_stories WHERE id != ? AND ts BETWEEN ? AND ? AND ntype NOT IN ('noise', 'other') AND n_sources >= 1",
                                  (s["id"], s["ts"] - 300, s["ts"] + 300)).fetchone()[0]
        if an.get("affected"):
            from app.intel import ai_news
            chk = ai_news.check(an, m)
            if chk:
                m["ai_check"] = chk
                why += " AI call: " + "; ".join(f"{lab(r['symbol'])} {'↑' if r['dir'] == 'up' else '↓'} {r['r15']:+.2f}% "
                                                f"{'✔' if r['hit'] else '✖' if r['wrong'] else '·'}" for r in chk["rows"][:5]) + "."
        if others and v in ("MOVED", "ABSORBED"):
            why += f" Note: {others} other market-relevant stor{'y' if others == 1 else 'ies'} within 5 min, so the move may not be from this one."
        final = s["status"] == "measured" or now - s["ts"] >= 61 * 60
        upd.append((json.dumps(m), v, why, "done" if final else "measured", now, s["id"]))
    if upd or nodata:
        with db.get_conn() as conn:
            conn.executemany("UPDATE news_stories SET measured=?, verdict=?, explain=?, status=?, updated=? WHERE id=?", upd)
            conn.executemany("UPDATE news_stories SET status='done', verdict='NO DATA' WHERE id=?", nodata)
        done["measured"] = len(upd)
    return done


def lab(sym: str) -> str:
    """Display name for an asset: fixed labels, then company names resolved from the news."""
    if sym in LABEL:
        return LABEL[sym]
    try:
        from app.intel import company

        return company.names().get(sym, sym)
    except Exception:  # noqa: BLE001
        return sym


def _ai(s: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(s.get("ai") or "{}") or {}
    except (ValueError, TypeError):
        return {}


def story(sid: int) -> dict[str, Any] | None:
    sources.ensure()
    with db.read_conn() as conn:
        r = conn.execute("SELECT * FROM news_stories WHERE id=?", (sid,)).fetchone()
        items = [dict(x) for x in conn.execute("SELECT ts, source, kind, text, link FROM news_items WHERE story_id=? ORDER BY ts", (sid,)).fetchall()]
    if not r:
        return None
    d = dict(r)
    for k in ("sources", "kinds", "tickers"):
        d[k] = json.loads(d[k] or "[]")
    d["measured"] = json.loads(d["measured"]) if d.get("measured") else None
    d.pop("words", None)
    d["items"] = items
    return d


# ---------------------------------------------------------------- alerts (two steps) and follow-up
FAST_KINDS = {"squawk", "truth", "israel", "geo"}
ALERTS_PER_HOUR = 4
TYPE_LABEL = {"fed": "FED / RATES", "inflation_data": "INFLATION DATA", "jobs_data": "JOBS DATA", "us_data": "US DATA", "deescalation": "DE-ESCALATION",
              "war_escalation": "WAR / ESCALATION", "trade_policy": "TARIFFS / SANCTIONS", "energy": "ENERGY", "earnings": "EARNINGS", "mna": "M&A",
              "regulatory": "REGULATORY", "agriculture": "AGRICULTURE", "analyst": "ANALYST", "crypto": "CRYPTO", "israel_macro": "ISRAEL MACRO", "politics": "POLITICS", "disaster": "DISASTER",
              "flows": "FLOWS", "trump_policy": "TRUMP", "company": "COMPANY", "other": "OTHER", "noise": "NOISE"}


def covered(title: str, hours: float = 2) -> bool:
    """True if a similar story was already alerted recently (keeps the keyword alerts in events.py from repeating it)."""
    w = sources.words(title)
    if len(w) < 3:
        return False
    sources.ensure()
    with db.read_conn() as conn:
        rows = conn.execute("SELECT title FROM news_stories WHERE alerted >= 1 AND ts > ?", (int(time.time() - hours * 3600),)).fetchall()
    for r in rows:
        rw = sources.words(r["title"])
        if rw and len(w & rw) / min(len(w), len(rw)) >= 0.5:
            return True
    return False


def _sent_last_hour() -> int:
    v = json.loads(db.meta_get("newsimpact_pushes") or "[]")
    return sum(1 for t in v if t > time.time() - 3600)


def _mark_push() -> None:
    v = [t for t in json.loads(db.meta_get("newsimpact_pushes") or "[]") if t > time.time() - 3600] + [time.time()]
    db.meta_set("newsimpact_pushes", json.dumps(v))


def _instant_card(s: dict[str, Any], st: dict[str, Any]) -> tuple[str, list[list[dict]]]:
    from html import escape

    from app.intel import impact

    kinds = json.loads(s["kinds"] or "[]") if isinstance(s["kinds"], str) else s["kinds"]
    srcs = json.loads(s["sources"] or "[]") if isinstance(s["sources"], str) else s["sources"]
    an = _ai(s).get("analysis")
    if an:
        return _ai_card(s, an, srcs)
    c = classify(s["title"], kinds)
    hist = st.get(s["ntype"])
    L = [f"⚡ <b>LIKELY MARKET-MOVING · {TYPE_LABEL.get(s['ntype'], s['ntype'].upper())}</b>", "", f"<b>{escape(s['title'][:280])}</b>",
         f"<i>{escape(', '.join(x.split(':', 1)[-1] for x in srcs[:4]))} · {time.strftime('%H:%M', time.localtime(s['ts']))}</i>", ""]
    if hist and hist["n"] >= 5:
        L.append(f"📚 This type moved markets in <b>{hist['p_moved'] * 100:.0f}%</b> of {hist['n']} past stories (expected now {s['expected'] * 100:.0f}%)")
    else:
        L.append(f"📚 Expected chance of a real move: <b>{s['expected'] * 100:.0f}%</b> (prior; few past stories of this type)")
    L.append("👀 Watching: " + escape(", ".join(LABEL.get(a, a) for a in c["assets"][:5])) + " · verdict in 15 min")
    try:
        info = impact.understand(s["title"], None)
        pm = impact.polymarket_related(info, limit=2)
        if pm:
            L += ["", "🎲 <b>Polymarket</b>"] + [f"• {escape(str(e['title'])[:60])}: <b>{e['markets'][0]['prob']:.0f}%</b>" for e in pm]
        ex = impact.exposure(info, impact.playbook(info["playbook"]))
        if ex and ex["rows"]:
            L += ["", f"💼 Fund exposure: ${ex['exposed']:,} ({escape(', '.join(r['symbol'] for r in ex['rows'][:4]))})"]
    except Exception:  # noqa: BLE001
        logger.exception("instant card extras failed")
    L += ["", "<i>NOSTRO News Engine · fast source, not yet confirmed by price</i>"]
    links = [it for it in (story(s["id"]) or {}).get("items", []) if str(it.get("link", "")).startswith("http")]
    return "\n".join(L), ([[{"text": "📰 Source", "url": links[0]["link"]}]] if links else [])


def _ai_card(s: dict[str, Any], an: dict[str, Any], srcs: list[str]) -> tuple[str, list[list[dict]]]:
    from html import escape

    from app.intel import ai_news, impact

    tri = _ai(s).get("triage") or {}
    L = [f"⚡ <b>MARKET-MOVING · {TYPE_LABEL.get(s['ntype'], str(s['ntype']).upper())}</b>   <code>AI {tri.get('score', 0)}/100</code>", "",
         f"<b>{escape(s['title'][:280])}</b>", f"<i>{escape(', '.join(x.split(':', 1)[-1] for x in srcs[:4]))} · {time.strftime('%H:%M', time.localtime(s['ts']))}</i>", ""]
    if an.get("event"):
        L.append(f"🧠 {escape(an['event'])}")
    if an.get("chain"):
        L += ["", "<b>Chain</b>"] + [f"{i}. {escape(x)}" for i, x in enumerate(an["chain"], 1)]
    if an.get("affected"):
        rows = [f"{('▲' if a['dir'] == 'up' else '▼')}{'●' * a['strength']:<3s} {a['symbol']:<9s} {ai_news.NAMES.get(a['symbol'], '')[:22]}" for a in an["affected"]]
        L += ["", "<b>Expected moves</b>", "<pre>" + escape("\n".join(rows)) + "</pre>"]
        L += [f"• <b>{escape(a['symbol'])}</b>: {escape(a['why'])}" for a in an["affected"][:4] if a.get("why")]
    try:  # measured precedent
        from app.intel import history

        evs = {e["id"]: e for e in history.events()}
        past = [evs[i] for i in an.get("analogues", []) if i in evs][:3]
        if past:
            syms = [a["symbol"] for a in an.get("affected", [])]
            L += ["", "<b>📜 Past similar events (real first-day moves)</b>"] + [f"• {escape(history.brief(e, syms + ['^GSPC', 'CL=F'], 4))}" for e in past]
            if an.get("vs_history"):
                L.append(f"<i>{escape(an['vs_history'])}</i>")
    except Exception:  # noqa: BLE001
        logger.exception("history lines failed")
    if an.get("priced_in"):
        L += ["", f"⏱ {escape(an['priced_in'])}"]
    if an.get("watch"):
        L.append(f"👀 {escape(an['watch'])}")
    try:
        info = impact.understand(s["title"], None)
        pm = impact.polymarket_related(info, limit=2)
        if pm:
            L += ["", "🎲 <b>Polymarket</b>"] + [f"• {escape(str(e['title'])[:60])}: <b>{e['markets'][0]['prob']:.0f}%</b>" for e in pm]
    except Exception:  # noqa: BLE001
        logger.exception("ai card extras failed")
    sc = ai_news.scorecard()
    track = f" · AI track record {sc['hit_rate'] * 100:.0f}% right on {sc['calls']} calls" if sc.get("calls", 0) >= 20 else " · AI track record still building"
    L += ["", f"<i>NOSTRO News Engine · confidence {an.get('confidence', 0) * 100:.0f}% · checked against prices in 15 min{track}</i>"]
    links = [it for it in (story(s["id"]) or {}).get("items", []) if str(it.get("link", "")).startswith("http")]
    return "\n".join(L), ([[{"text": "📰 Source", "url": links[0]["link"]}]] if links else [])


def _verdict_card(s: dict[str, Any]) -> tuple[str, list[list[dict]]]:
    from html import escape

    icon = {"MOVED": "📈", "ABSORBED": "↩️", "PRICED BEFORE": "⏪", "NO REACTION": "⬜"}.get(s["verdict"], "•")
    m = json.loads(s["measured"]) if isinstance(s["measured"], str) else s["measured"]
    rows = []
    for sym, v in list((m or {}).get("assets", {}).items())[:5]:
        if v.get("r15") is None:
            continue
        rows.append(f"{lab(sym)[:10]:<10s} 15m {v['r15']:+6.2f}% z{v.get('z15') or 0:+5.1f}" + (f"  60m {v['r60']:+6.2f}%" if v.get("r60") is not None else ""))
    L = [f"{icon} <b>VERDICT · {escape(s['verdict'])}</b>   <code>{TYPE_LABEL.get(s['ntype'], '')}</code>", f"<i>{escape(s['title'][:200])}</i>", "",
         escape(s["explain"] or "")]
    if rows:
        L += ["", "<pre>" + escape("\n".join(rows)) + "</pre>"]
    L.append("<i>NOSTRO News Engine · z = move vs that asset's normal move at that hour</i>")
    return "\n".join(L), []


def alerts() -> dict[str, int]:
    """Step 1: instant card for a likely market-moving story from a fast source. Step 2: the measured verdict 15 min later.
    A story nobody flagged that still moved the market hard (primary |z15| >= 3.5) gets a verdict card too."""
    from app.notify import telegram_bot

    sources.ensure()
    now = int(time.time())
    st = stats()
    sent = {"instant": 0, "verdict": 0}
    with db.read_conn() as conn:
        fresh = [dict(r) for r in conn.execute("SELECT * FROM news_stories WHERE alerted=0 AND status='classified' AND ts > ? AND ntype NOT IN ('noise', 'other') "
                                               "AND expected >= 0.3 ORDER BY expected DESC LIMIT 5", (now - 600,)).fetchall()]
        judged = [dict(r) for r in conn.execute("SELECT * FROM news_stories WHERE verdict IS NOT NULL AND ts > ? AND "
                                                "(alerted=1 OR (alerted IN (0, -1) AND verdict IN ('MOVED', 'ABSORBED')))", (now - 3 * 3600,)).fetchall()]
    from app.intel import ai_news
    if ai_news.enabled():
        with db.read_conn() as conn:
            fresh = [dict(r) for r in conn.execute("SELECT * FROM news_stories WHERE alerted=0 AND ts > ? AND ai LIKE '%\"analysis\": {%' ORDER BY expected DESC LIMIT 5",
                                                   (now - 900,)).fetchall()]
        keep = []
        for s in fresh:
            ok, why = ai_news.alert_ok(_ai(s).get("analysis") or {}, s.get("ntype"))
            if ok:
                keep.append(s)
            else:
                logger.info("instant alert held back (%s): %s", why, s["title"][:80])
                with db.get_conn() as conn:
                    conn.execute("UPDATE news_stories SET alerted=-1 WHERE id=?", (s["id"],))  # judged not alert-worthy (still measured)
        fresh = keep
    for s in fresh:
        if not ai_news.enabled() and not set(json.loads(s["kinds"] or "[]")) & FAST_KINDS or _sent_last_hour() >= ALERTS_PER_HOUR or covered(s["title"]):
            continue
        html, b = _instant_card(s, st)
        if telegram_bot.send_rich(html, b):
            _mark_push()
        try:  # small caps that break out on this story
            from app.intel import impact, movers

            assets = [a.get("symbol") if isinstance(a, dict) else a for a in ((_ai(s).get("analysis") or {}).get("assets") or [])]
            movers.watch(f"ns:{s['id']}", int(s["ts"]), s["title"], s.get("ntype"), [t for t in (impact._theme_of(x) for x in assets if x) if t])
        except Exception:  # noqa: BLE001
            logger.exception("movers watch failed")
        with db.get_conn() as conn:
            conn.execute("UPDATE news_stories SET alerted=1 WHERE id=?", (s["id"],))
        sent["instant"] += 1
    for s in judged:
        if s["alerted"] <= 0:
            m = json.loads(s["measured"] or "{}")
            pz = abs(((m.get("assets") or {}).get(m.get("primary")) or {}).get("z15") or 0)
            if pz < 3.5 or s["ntype"] in ("other", "noise") or _sent_last_hour() >= ALERTS_PER_HOUR:
                continue
        html, b = _verdict_card(s)
        if telegram_bot.send_rich(html, b) and s["alerted"] <= 0:
            _mark_push()
        with db.get_conn() as conn:
            conn.execute("UPDATE news_stories SET alerted=2 WHERE id=?", (s["id"],))
        sent["verdict"] += 1
    return sent


def followup() -> int:
    """+1 day and +5 days after a story that MOVED: did the move on the primary asset continue or fade? Stored in measured['d1'] / ['d5']."""
    sources.ensure()
    now = int(time.time())
    n = 0
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, ts, measured FROM news_stories WHERE verdict IN ('MOVED', 'ABSORBED') AND ts BETWEEN ? AND ?",
                                              (now - 8 * 86400, now - 86400)).fetchall()]
    for r in rows:
        m = json.loads(r["measured"] or "{}")
        prim = m.get("primary")
        if not prim or m.get("d5") is not None:
            continue
        bars = _series(prim, False)
        base = _at(bars, r["ts"]) if bars else None
        if not base:
            continue
        changed = False
        for key, sec in (("d1", 86400), ("d5", 5 * 86400)):
            if m.get(key) is None and now - r["ts"] >= sec + 3600:
                end = _at(bars, r["ts"] + sec)
                if end:
                    m[key] = round((end[1] / base[1] - 1) * 100, 3)
                    changed = True
        if changed:
            r15 = ((m.get("assets") or {}).get(prim) or {}).get("r15") or 0
            last = m.get("d5", m.get("d1"))
            m["outcome"] = "continued" if last is not None and np.sign(last) == np.sign(r15) and abs(last) > abs(r15) else "faded"
            with db.get_conn() as conn:
                conn.execute("UPDATE news_stories SET measured=? WHERE id=?", (json.dumps(m), r["id"]))
            n += 1
    return n


def ledger(hours: float = 48, only_relevant: bool = True) -> list[dict[str, Any]]:
    rows = sources.recent_stories(hours, limit=400)
    if only_relevant:
        rows = [r for r in rows if r["ntype"] not in (None, "noise", "other")
                and (r["verdict"] in ("MOVED", "ABSORBED", "PRICED BEFORE") or (r["expected"] or 0) >= 0.3 or r["n_sources"] >= 3 or r["alerted"])]
    return rows
