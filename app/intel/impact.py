"""Event impact: did the market actually react to an alert, which Polymarket markets price the same story, and what past events of the same type did.

How it decides what is relevant (and why the first version was not good enough):
* The story is mapped to a TOPIC (Fed, inflation, jobs, Iran/Israel, Russia/Ukraine, China/tariffs, oil, crypto, market stress) from its title. A topic carries the
  Polymarket queries, the Polymarket TAGS that must be present (so "Hawks" can never pull in a basketball game), the stock themes it plausibly moves, and the
  playbook it is compared with. A story with no topic gets no Polymarket section and no playbook, rather than a guess.
* Moves are judged against noise: excess return vs SPY divided by the symbol's own volatility scaled to the time elapsed (z). |z| >= 2 is "unusual"; below that
  it is shown as no reaction. Themes are scored as baskets (median of the members), which is far steadier than single tickers.
* The playbook is measured from real prices on dated past events of the same type, split by direction for macro news (hawkish/hot vs dovish/cool), with the
  sample size next to every number. Each alert is compared with its playbook: moved as history suggests, the opposite, or not yet.
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import requests

from app import db

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
Z_UNUSUAL = 2.0
ABS_THEMES = {"BONDS", "DOLLAR"}  # rate/currency proxies are read as absolute moves; "vs SPY" would turn a bond sell-off into a gain on a bad equity day
SHORT = {"OIL & ENERGY": "Oil & energy", "DEFENCE": "Defence", "GOLD & MINERS": "Gold & miners", "URANIUM & NUCLEAR": "Uranium", "CYBER": "Cyber", "ISRAEL": "Israel", "TANKERS & SHIPPING": "Tankers", "AIRLINES & TRAVEL": "Airlines", "BONDS": "Bonds (abs)", "BANKS": "Banks", "HOMEBUILDERS": "Homebuilders", "GROWTH TECH": "Growth tech", "CRYPTO": "Crypto", "CHINA": "China", "AGRICULTURE": "Agriculture", "DOLLAR": "Dollar (abs)"}

THEMES: dict[str, list[str]] = {
    "OIL & ENERGY": ["XOM", "CVX", "OXY", "COP", "XLE", "APA", "DVN"],
    "DEFENCE": ["LMT", "NOC", "RTX", "GD", "LHX", "ITA", "PLTR", "KTOS"],
    "GOLD & MINERS": ["GLD", "GDX", "NEM", "AEM", "GOLD", "SLV"],
    "URANIUM & NUCLEAR": ["CCJ", "UEC", "UUUU", "NXE", "DNN", "URA", "URNM", "LEU", "SMR", "OKLO"],
    "CYBER": ["PANW", "CRWD", "FTNT", "ZS"],
    "ISRAEL": ["EIS", "CHKP", "NICE", "ESLT", "TEVA", "WIX", "CYBR"],
    "TANKERS & SHIPPING": ["FRO", "STNG", "INSW", "ZIM"],
    "AIRLINES & TRAVEL": ["DAL", "UAL", "AAL", "LUV", "CCL", "RCL"],
    "BONDS": ["TLT", "IEF", "SHY"],
    "BANKS": ["XLF", "KRE", "JPM", "BAC"],
    "HOMEBUILDERS": ["XHB", "ITB", "DHI", "LEN"],
    "GROWTH TECH": ["QQQ", "ARKK", "NVDA", "SMH", "TSLA"],
    "CRYPTO": ["BTC-USD", "COIN", "MSTR", "IBIT"],
    "CHINA": ["FXI", "KWEB", "BABA", "PDD"],
    "AGRICULTURE": ["DBA", "SOYB", "ADM", "DE"],
    "DOLLAR": ["UUP"],
}

# topic -> how to recognise it, what to search on Polymarket, which Polymarket tags make a market relevant, which themes it moves, which playbook applies
TOPICS: dict[str, dict[str, Any]] = {
    "FED & RATES": {"rx": r"\bfed\b|fomc|powell|warsh|rate (hike|cut|decision)|hawkish|dovish|interest rate",
                    "q": ["Fed decision", "Fed rate cuts", "Fed rate hike"], "tags": {"Fed", "Fed Rates", "fomc", "Global Rates", "Economic Policy"},
                    "themes": ["BONDS", "BANKS", "HOMEBUILDERS", "GROWTH TECH", "GOLD & MINERS", "CRYPTO", "DOLLAR"], "playbook": "macro"},
    "BANK OF ISRAEL": {"rx": r"bank of israel|בנק ישראל|ריבית בנק|אמיר ירון|\bboi\b",
                       "q": ["Bank of Israel"], "tags": {"Israel", "Global Rates", "Economic Policy", "Bank of Israel"},
                       "themes": ["ISRAEL", "DOLLAR"], "playbook": None},
    "INFLATION": {"rx": r"\bcpi\b|\bppi\b|\bpce\b|inflation|consumer prices|אינפלציה|מדד המחירים",
                  "q": ["CPI", "inflation", "Fed decision"], "tags": {"CPI Release", "Inflation", "Macro Indicators", "Fed Rates"},
                  "themes": ["BONDS", "HOMEBUILDERS", "GROWTH TECH", "GOLD & MINERS", "BANKS", "DOLLAR"], "playbook": "macro"},
    "JOBS": {"rx": r"payroll|jobs report|nonfarm|unemployment|jobless",
             "q": ["unemployment", "jobs report", "Fed decision"], "tags": {"Macro Indicators", "Economy", "Jobs", "Fed Rates"},
             "themes": ["BONDS", "BANKS", "GROWTH TECH", "DOLLAR"], "playbook": "macro"},
    "US DATA": {"rx": r"retail sales|\bgdp\b|\bism\b|\bpmi\b|consumer (sentiment|confidence)|durable goods",
                "q": ["Fed decision", "recession"], "tags": {"Fed Rates", "Economy", "Macro Indicators"},
                "themes": ["BONDS", "BANKS", "GROWTH TECH", "DOLLAR"], "playbook": "macro"},
    "IRAN / ISRAEL": {"rx": r"iran|israel|hezbollah|houthi|yemen|saudi|red sea|syria|gaza|tehran|hormuz|lebanon|idf|missile|sirens|איראן|ישראל|חיזבאללה|טילים|אזעקות|תקיפה|צה\"ל",
                      "q": ["Iran", "Israel Iran", "Israel military action", "Hezbollah", "Strait of Hormuz"], "tags": {"Iran", "Israel", "Middle East", "Geopolitics", "Gaza", "Lebanon"},
                      "themes": ["OIL & ENERGY", "DEFENCE", "GOLD & MINERS", "URANIUM & NUCLEAR", "CYBER", "ISRAEL", "TANKERS & SHIPPING", "AIRLINES & TRAVEL"], "playbook": "war"},
    "RUSSIA / UKRAINE": {"rx": r"russia|ukraine|putin|kremlin|zelensky|nato",
                         "q": ["Russia Ukraine", "ceasefire Ukraine", "Putin"], "tags": {"Ukraine", "Russia", "Geopolitics", "NATO"},
                         "themes": ["OIL & ENERGY", "DEFENCE", "GOLD & MINERS", "AGRICULTURE", "URANIUM & NUCLEAR"], "playbook": "war"},
    "CHINA / TARIFFS": {"rx": r"china|beijing|taiwan|xi jinping|tariff|trade war|export control",
                        "q": ["China tariffs", "Taiwan", "trade deal China"], "tags": {"China", "Tariffs", "Trade War", "Taiwan", "Trade"},
                        "themes": ["CHINA", "GROWTH TECH", "AGRICULTURE", "DOLLAR"], "playbook": None},
    "OIL": {"rx": r"\boil\b|opec|crude|brent|refinery|pipeline|נפט",
            "q": ["oil price", "crude oil", "OPEC"], "tags": {"Oil", "Commodities", "OPEC", "Energy"},
            "themes": ["OIL & ENERGY", "TANKERS & SHIPPING", "AIRLINES & TRAVEL"], "playbook": None},
    "CRYPTO": {"rx": r"bitcoin|crypto|ethereum|\bbtc\b|stablecoin",
               "q": ["Bitcoin price", "crypto"], "tags": {"Crypto", "Bitcoin", "Crypto Prices"},
               "themes": ["CRYPTO"], "playbook": None},
    "MARKET STRESS": {"rx": r"crash|plunge|selloff|sell-off|circuit breaker|bank run|default|recession|vix|קריסה|מפולת",
                      "q": ["recession", "S&P 500", "stock market crash"], "tags": {"Economy", "Stocks", "Recession", "Finance", "Macro Indicators"},
                      "themes": ["BONDS", "GOLD & MINERS", "BANKS", "GROWTH TECH"], "playbook": None},
}
FOREIGN = r"japan|\bboj\b|\buk\b|u\.k\.|britain|england|\bboe\b|euro|\becb\b|germany|france|italy|spain|canada|australia|\brba\b|new zealand|china|india|brazil|argentina|chile|colombia|turkey|mexico|korea|swiss|switzerland|\bsnb\b|norway|sweden|poland|russia|nigeria|south africa|israel"
BAD_TAGS = {"Sports", "Esports", "Basketball", "Soccer", "NBA", "NFL", "Tennis", "Culture", "Pop Culture", "Mentions", "Movies", "Music", "Awards", "Tweet Markets"}
HAWK = r"hawkish|hot|above (forecast|expectation|estimate)|beats?|accelerat|rate hike|hikes|higher than expected|surge|sticky|עלייה"
DOVE = r"dovish|cool|below (forecast|expectation|estimate)|miss|slow(s|ed|ing)|rate cut|cuts|lower than expected|eases|ירידה"

# Dated shocks that seed the playbooks; reactions are measured from real prices (first session that traded the news vs the previous close).
SEED_EVENTS: list[tuple[str, str, str]] = [
    ("2020-01-03", "war", "US kills Iranian general Soleimani"),
    ("2022-02-24", "war", "Russia invades Ukraine"),
    ("2023-10-09", "war", "Hamas attack on Israel (first session after)"),
    ("2024-04-15", "war", "Iran drone and missile attack on Israel (first session after)"),
    ("2024-10-01", "war", "Iran fires missiles at Israel"),
    ("2025-06-13", "war", "Israel strikes Iran"),
    ("2025-06-23", "war", "US strikes Iranian nuclear sites (first session after)"),
    ("2022-08-26", "macro_hawk", "Powell Jackson Hole: 'pain' speech"),
    ("2022-09-13", "macro_hawk", "Hot August CPI"),
    ("2024-12-18", "macro_hawk", "Fed cuts but signals fewer cuts"),
    ("2023-02-03", "macro_hawk", "Blowout January payrolls"),
    ("2022-11-10", "macro_dove", "Cool October CPI"),
    ("2023-11-14", "macro_dove", "Cool October CPI"),
    ("2023-12-13", "macro_dove", "Fed pivots to cuts (dot plot)"),
    ("2024-08-02", "macro_dove", "Weak July payrolls"),
]
SCHEMA = ("CREATE TABLE IF NOT EXISTS intel_reactions (id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL, kind TEXT, label TEXT, day TEXT, symbol TEXT, theme TEXT, "
          "r1 REAL, r5 REAL, x1 REAL, x5 REAL, UNIQUE(event_key, symbol))")
_hist: dict[str, tuple[float, Any]] = {}


def ensure() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)


# ---------------------------------------------------------------- understanding the story
def understand(title: str, kind: str | None = None) -> dict[str, Any]:
    """Topic(s), direction for macro news, the themes to watch, and which playbook applies."""
    t = (title or "").lower()
    topics = [name for name, d in TOPICS.items() if re.search(d["rx"], t)]
    if "BANK OF ISRAEL" in topics:  # "Israel" in a rate decision is not a security event, and the Fed is a different central bank
        topics = [x for x in topics if x not in ("IRAN / ISRAEL", "FED & RATES")]
    themes: list[str] = []
    for name in topics:
        themes += [x for x in TOPICS[name]["themes"] if x not in themes]
    pb = next((TOPICS[n]["playbook"] for n in topics if TOPICS[n]["playbook"]), None)
    if pb is None and kind == "war":
        pb = "war"
    direction = None
    if pb == "macro":
        h, d = bool(re.search(HAWK, t)), bool(re.search(DOVE, t))
        direction = "hawkish" if h and not d else "dovish" if d and not h else "mixed"
        pb = {"hawkish": "macro_hawk", "dovish": "macro_dove"}.get(direction)
    return {"topics": topics, "themes": themes, "playbook": pb, "direction": direction, "title_l": t}


def universe() -> list[str]:
    seen: list[str] = []
    for v in THEMES.values():
        seen += [s for s in v if s not in seen]
    return seen


def _theme_of(sym: str) -> str | None:
    return next((k for k, v in THEMES.items() if sym in v), None)


# ---------------------------------------------------------------- prices
def _cached(key: str, ttl: float, fn):
    hit = _hist.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:  # noqa: BLE001 - one bad symbol never breaks a scan
        val = None
    _hist[key] = (time.time(), val)
    return val


def _daily(sym: str):
    from app.api import invest_routes as ir

    def go():
        h = ir._history(sym, "30y", "1d")
        pts = [(time.strftime("%Y-%m-%d", time.gmtime(c[0])), float(c[4])) for c in h["candles"] if c[4]]
        return [p[0] for p in pts], np.array([p[1] for p in pts])
    return _cached("d:" + sym, 6 * 3600, go)


def _intraday(sym: str):
    from app.api import invest_routes as ir

    def go():
        h = ir._history(sym, "5d", "5m", prepost=True)
        return [(c[0], float(c[4])) for c in h["candles"] if c[4]]
    return _cached("i:" + sym, 120, go)


def _vol20(closes: np.ndarray) -> float:
    r = np.diff(np.log(closes[-21:]))
    return float(r.std()) if len(r) > 5 else 0.0


def _since(bars, ts: int):
    base = [i for i, b in enumerate(bars) if b[0] <= ts]
    if not base or bars[-1][0] <= ts:
        return None
    i = base[-1]
    return bars[-1][1] / bars[i][1] - 1, len(bars) - 1 - i, bars[i][0], bars[-1][0]


def reaction(ts: int, info: dict[str, Any]) -> dict[str, Any]:
    """Measured reaction since `ts`: SPY, every theme basket, and single symbols that moved unusually (anywhere in the universe)."""
    uni = universe()
    with ThreadPoolExecutor(8) as ex:
        intr = dict(zip(uni + ["SPY"], ex.map(_intraday, uni + ["SPY"])))
        dly = dict(zip(uni + ["SPY"], ex.map(_daily, uni + ["SPY"])))
    spy = _since(intr.get("SPY") or [], ts)
    if spy is None:
        return {"ok": False, "note": "no new bars since the alert yet (market closed)"}
    frac = min(1.0, max(0.05, spy[1] / 78))  # share of a regular session elapsed (78 five-minute bars)
    sd = dly.get("SPY")
    spy_z = spy[0] / (_vol20(sd[1]) * np.sqrt(frac)) if sd and _vol20(sd[1]) else 0.0
    rows = []
    for s in uni:
        r = _since(intr.get(s) or [], ts)
        if r is None:
            continue
        d = dly.get(s)
        sig = _vol20(d[1]) if d else 0.0
        x = r[0] if _theme_of(s) in ABS_THEMES else r[0] - spy[0]
        z = x / (sig * np.sqrt(frac)) if sig else 0.0
        rows.append({"symbol": s, "theme": _theme_of(s), "ret": round(r[0] * 100, 2), "excess": round(x * 100, 2), "z": round(float(z), 1)})
    baskets = []
    for th, members in THEMES.items():
        m = [r for r in rows if r["symbol"] in members]
        if len(m) >= 2 or (m and len(members) == 1):
            baskets.append({"theme": th, "excess": round(float(np.median([r["excess"] for r in m])), 2), "z": round(float(np.median([r["z"] for r in m])), 1),
                            "n": len(m), "watched": th in info["themes"]})
    baskets.sort(key=lambda b: (-b["watched"], -abs(b["z"])))
    unusual = sorted([r for r in rows if abs(r["z"]) >= Z_UNUSUAL], key=lambda r: -abs(r["z"]))
    for r in unusual:
        r["watched"] = r["theme"] in info["themes"]
    macro = {}
    for sym, lab in (("^VIX", "VIX"), ("CL=F", "Oil"), ("GC=F", "Gold")):
        m = _since(_intraday(sym) or [], ts)
        if m is not None:
            macro[lab] = round(m[0] * 100, 1)
    reacted = abs(spy_z) >= Z_UNUSUAL or any(abs(b["z"]) >= 1.5 and b["watched"] for b in baskets) or sum(1 for r in unusual if r["watched"]) >= 2
    return {"ok": True, "spy": round(spy[0] * 100, 2), "spy_z": round(float(spy_z), 1), "hours": round((spy[3] - spy[2]) / 3600, 1), "baskets": baskets,
            "unusual": unusual[:10], "reacted": bool(reacted), "macro": macro}


# ---------------------------------------------------------------- Polymarket
def _pm_search(q: str) -> list[dict[str, Any]]:
    try:
        r = requests.get("https://gamma-api.polymarket.com/public-search", params={"q": q, "limit_per_type": 10}, headers=UA, timeout=15)
        r.raise_for_status()
        return r.json().get("events", []) or []
    except Exception:  # noqa: BLE001
        return []


def polymarket_related(info: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    """Markets on the same topic: found with the topic's own queries and kept only if Polymarket tagged them with one of the topic's tags (never sports/culture)."""
    want: set[str] = set()
    queries: list[str] = []
    for name in info["topics"]:
        want |= {x.lower() for x in TOPICS[name]["tags"]}
        queries += TOPICS[name]["q"]
    if not queries:
        return []
    bad = {x.lower() for x in BAD_TAGS}
    seen: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(_pm_search, queries))
    for evs in results:
        for ev in evs:
            tags = {str(t.get("label", "")).lower() for t in ev.get("tags") or []}
            if ev.get("closed") or tags & bad or not tags & want or ev.get("slug") in seen:
                continue
            if info["playbook"] in ("macro_hawk", "macro_dove", None) and info.get("direction") is not None:
                foreign = re.findall(FOREIGN, str(ev.get("title", "")).lower())
                if foreign and not any(f in info.get("title_l", "") for f in foreign):  # a Japanese CPI market says nothing about a US CPI print
                    continue
            mk = []
            for m in ev.get("markets") or []:
                try:
                    if m.get("closed"):
                        continue
                    p = m.get("outcomePrices")
                    p = json.loads(p) if isinstance(p, str) else p
                    mk.append({"question": m.get("groupItemTitle") or m.get("question"), "prob": round(float(p[0]) * 100, 1),
                               "change1d": None if m.get("oneDayPriceChange") is None else round(float(m["oneDayPriceChange"]) * 100, 1)})
                except Exception:  # noqa: BLE001
                    continue
            mk = [m for m in mk if 0.5 < m["prob"] < 99.5] or mk  # settled-looking outcomes say nothing
            if not mk:
                continue
            mk.sort(key=lambda x: -(abs(x["change1d"] or 0) * 3 + x["prob"]))
            vol = float(ev.get("volume24hr") or 0)
            seen[ev["slug"]] = {"title": ev.get("title"), "url": f"https://polymarket.com/event/{ev['slug']}", "volume24h": vol, "markets": mk[:3],
                                "tags": sorted(tags & want), "moved": max((abs(m["change1d"] or 0) for m in mk), default=0)}
    out = [e for e in seen.values() if e["volume24h"] >= 1000]
    return sorted(out, key=lambda e: -(e["volume24h"] * (1 + e["moved"] / 5)))[:limit]


# ---------------------------------------------------------------- learning (playbooks)
def _reaction_day(sym: str, day: str):
    d = _daily(sym)
    if not d:
        return None
    dates, c = d
    i = next((k for k, x in enumerate(dates) if x >= day), None)
    if i is None or i < 1:
        return None
    r1 = c[i] / c[i - 1] - 1
    r5 = c[i + 4] / c[i - 1] - 1 if i + 4 < len(c) else None
    return float(r1), None if r5 is None else float(r5)


def record(event_key: str, kind: str, label: str, day: str) -> int:
    ensure()
    uni = universe()
    with ThreadPoolExecutor(8) as ex:
        res = dict(zip(uni, ex.map(lambda s: _reaction_day(s, day), uni)))
    spy = _reaction_day("SPY", day)
    if spy is None:
        return 0
    n = 0
    with db.get_conn() as conn:
        for s, r in res.items():
            if r is None:
                continue
            x5 = None if (r[1] is None or spy[1] is None) else r[1] - spy[1]
            conn.execute("INSERT OR IGNORE INTO intel_reactions (event_key, kind, label, day, symbol, theme, r1, r5, x1, x5) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (event_key, kind, label, day, s, _theme_of(s), r[0], r[1], r[0] - spy[0], x5))
            n += 1
    return n


def learn() -> dict[str, int]:
    """Seed the playbooks, then add every pushed alert at least a day old whose playbook is known."""
    ensure()
    with db.get_conn() as conn:
        have = {r[0] for r in conn.execute("SELECT DISTINCT event_key FROM intel_reactions")}
    done = 0
    for day, kind, label in SEED_EVENTS:
        if f"seed:{day}:{kind}" not in have:
            with db.get_conn() as conn:
                conn.execute("DELETE FROM intel_reactions WHERE event_key=?", (f"seed:{day}",))  # older key format
            done += record(f"seed:{day}:{kind}", kind, label, day)
    from app.intel import events

    for e in events.recent(hours=24 * 30, min_sev=3):
        pb = understand(e["title"], e["kind"])["playbook"]
        if pb and e.get("pushed") and e["ts"] < time.time() - 86400 * 1.5 and f"ev:{e['id']}" not in have:
            done += record(f"ev:{e['id']}", pb, e["title"], time.strftime("%Y-%m-%d", time.gmtime(e["ts"])))
    return {"measured": done}


def playbook(kind: str | None) -> dict[str, Any]:
    if not kind:
        return {"kind": None, "events": [], "themes": [], "symbols": []}
    ensure()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT event_key, label, day, symbol, theme, r1, x1, x5 FROM intel_reactions WHERE kind=?", (kind,)).fetchall()]
    for r in rows:
        if r["theme"] in ABS_THEMES:
            r["x1"] = r["r1"]
    evs = {}
    by_sym: dict[str, list[float]] = {}
    by_theme: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        evs[r["event_key"]] = (r["day"], r["label"])
        by_sym.setdefault(r["symbol"], []).append(r["x1"])
        by_theme.setdefault(r["theme"] or "?", {}).setdefault(r["event_key"], []).append(r["x1"])
    themes = []
    for th, per_ev in by_theme.items():
        vals = np.array([np.median(v) for v in per_ev.values()])  # one number per event: the basket median
        themes.append({"theme": th, "n": len(vals), "avg": round(float(vals.mean()) * 100, 2), "hit": round(float((vals > 0).mean()) * 100)})
    themes.sort(key=lambda x: -x["avg"])
    syms = [{"symbol": s, "theme": _theme_of(s), "n": len(v), "avg_x1": round(float(np.mean(v)) * 100, 2), "hit1": round(float((np.array(v) > 0).mean()) * 100)} for s, v in by_sym.items()]
    syms.sort(key=lambda x: -x["avg_x1"])
    return {"kind": kind, "events": [{"day": d, "label": l} for d, l in sorted(set(evs.values()))], "themes": themes, "symbols": syms,
            "note": "Excess return vs SPY on the first session that traded the news. Small samples: a tendency, not a guarantee."}


def _compare(baskets: list[dict[str, Any]], pb: dict[str, Any], watched: list[str]) -> list[dict[str, Any]]:
    """For each theme with a clear historical tendency (|avg| >= 1% and hit >= 70% or <= 30%), did today's basket follow it?"""
    now = {b["theme"]: b for b in baskets}
    out = []
    for t in pb.get("themes", []):
        if t["n"] < 3 or abs(t["avg"]) < 1 or 30 < t["hit"] < 70 or t["theme"] not in now or t["theme"] not in watched:
            continue
        b = now[t["theme"]]
        if abs(b["z"]) < 1.5:
            verdict = "not yet"
        elif np.sign(b["excess"]) == np.sign(t["avg"]):
            verdict = "as history" + (" (already priced)" if abs(b["excess"]) >= abs(t["avg"]) * 0.8 else "")
        else:
            verdict = "opposite to history"
        out.append({"theme": t["theme"], "history": t["avg"], "hit": t["hit"], "n": t["n"], "now": b["excess"], "z": b["z"], "verdict": verdict})
    return out


SECTOR_THEME = {"Energy": "OIL & ENERGY", "Technology": "GROWTH TECH", "Communication Services": "GROWTH TECH", "Financial Services": "BANKS",
                "Basic Materials": "GOLD & MINERS", "Real Estate": "HOMEBUILDERS", "Consumer Cyclical": None, "Industrials": None, "Healthcare": None, "Utilities": None}


def _fund_theme(sym: str) -> str | None:
    th = _theme_of(sym)
    if th:
        return th
    from app.api import invest_routes as ir

    def go():
        return ((ir._security(sym) or {}).get("profile") or {}).get("sector")
    return SECTOR_THEME.get(_cached("sector:" + sym, 86400, go) or "")


def exposure(info: dict[str, Any], pb: dict[str, Any] | None = None, ts: int | None = None) -> dict[str, Any] | None:
    """The fund's positions seen through this story: value in the themes it moves, move since the alert, and what history suggests (day-1, vs SPY)."""
    from app.fund import book

    try:
        pos = book.positions()
    except Exception:  # noqa: BLE001
        return None
    if not pos:
        return None
    pb_sym = {s["symbol"]: s for s in (pb or {}).get("symbols", []) if s["n"] >= 3}
    pb_th = {t["theme"]: t for t in (pb or {}).get("themes", []) if t["n"] >= 3}
    rows, total = [], 0.0
    for p in pos:
        d = _daily(p["symbol"])
        price = float(d[1][-1]) if d else p["avg_cost"]
        value = p["qty"] * price
        total += value
        th = _fund_theme(p["symbol"])
        hist = pb_sym[p["symbol"]]["avg_x1"] if p["symbol"] in pb_sym else (pb_th[th]["avg"] if th in pb_th else None)
        now = None
        if ts:
            r = _since(_intraday(p["symbol"]) or [], ts)
            now = round(r[0] * 100, 2) if r else None
        touched = th in info["themes"] or (hist is not None and abs(hist) >= 1)
        rows.append({"symbol": p["symbol"], "theme": th, "value": round(value), "touched": touched, "now": now, "hist": hist,
                     "now_usd": None if now is None else round(value * now / 100), "hist_usd": None if hist is None else round(value * hist / 100)})
    hit = [r for r in rows if r["touched"]]
    hit.sort(key=lambda r: -abs(r["hist_usd"] or r["now_usd"] or r["value"] / 1e6))
    return {"rows": hit, "total": round(total), "exposed": round(sum(r["value"] for r in hit)), "now_usd": round(sum(r["now_usd"] or 0 for r in hit)),
            "hist_usd": round(sum(r["hist_usd"] or 0 for r in hit)), "n_positions": len(rows)}


def analyse(ts: int, title: str, kind: str | None = None) -> dict[str, Any]:
    info = understand(title, kind)
    react = reaction(ts, info)
    pb = playbook(info["playbook"])
    return {"title": title, "kind": kind, "ts": ts, "understanding": info, "reaction": react, "polymarket": polymarket_related(info), "playbook": pb,
            "vs_history": _compare(react.get("baskets", []), pb, info["themes"]) if react.get("ok") else [], "exposure": exposure(info, pb, ts)}


def worth_sending(res: dict[str, Any]) -> bool:
    """Telegram only when the market actually reacted; a story the market ignored stays on the terminal page (EVENT IMPACT) and is not pushed."""
    r = res["reaction"]
    return bool(r.get("ok") and r.get("reacted"))


def _bar(z: float) -> str:
    n = int(min(5, round(abs(z))))
    return ("▰" * n + "▱" * (5 - n)) if n else "▱▱▱▱▱"


def _sg(v: float, d: int = 1) -> str:
    return f"{v:+.{d}f}%"


def _bottom_line(res: dict[str, Any]) -> str:
    """One plain-English sentence: what the market did with this story and what to watch next."""
    r, vh = res["reaction"], res["vs_history"]
    if not r.get("ok"):
        return "Market closed since the alert: nothing priced yet."
    follow = [SHORT.get(v["theme"], v["theme"].title()) for v in vh if v["verdict"].startswith("as history")]
    against = [SHORT.get(v["theme"], v["theme"].title()) for v in vh if v["verdict"].startswith("opposite")]
    pending = [v for v in vh if v["verdict"] == "not yet"]
    moved = [SHORT.get(b["theme"], b["theme"].title()) for b in r["baskets"] if b["watched"] and abs(b["z"]) >= 1.5]
    parts = []
    if r["reacted"]:
        parts.append("Market is pricing it" + (f": {', '.join(follow[:3])} moving like past events." if follow else (f": {', '.join(moved[:3])} moving." if moved else ".")))
    else:
        parts.append("Market is ignoring it so far.")
    if against:
        parts.append(f"{', '.join(against[:2])} going the opposite way to history.")
    if pending:
        p = sorted(pending, key=lambda v: -abs(v["history"]))[:2]
        parts.append("Not moved yet but usually does: " + ", ".join(f"{SHORT.get(v['theme'], v['theme'].title())} ({_sg(v['history'])} in {v['hit'] if v['history'] > 0 else 100 - v['hit']}% of cases)" for v in p) + ".")
    return " ".join(parts)


def card(res: dict[str, Any], link: str | None = None) -> tuple[str, list[list[dict]]]:
    from html import escape

    r, info, pb = res["reaction"], res["understanding"], res["playbook"]
    topic = " · ".join(info["topics"]) or "UNCLASSIFIED"
    if info.get("direction"):
        topic += f" · {info['direction'].upper()}"
    L = [f"🔎 <b>IMPACT · {escape(topic)}</b>   <code>⏱ {r.get('hours', '?')}h</code>", f"<i>{escape(res['title'])}</i>", ""]
    if r.get("ok"):
        status = "🟥 <b>MARKET IS REACTING</b>" if r["reacted"] else "⬜ <b>NO REAL REACTION</b>"
        extra = "".join(f"   {k} {_sg(v)}" for k, v in (r.get("macro") or {}).items())
        L += [status, f"<code>SPY {_sg(r['spy'], 2)} (z {r['spy_z']:+.1f}){extra}</code>", ""]
    L += ["📌 <b>Bottom line</b>", escape(_bottom_line(res)), ""]

    vh = {v["theme"]: v for v in res["vs_history"]}
    if r.get("ok"):
        watched = [b for b in r["baskets"] if b["watched"]]
        rows = []
        for b in watched[:8]:
            v = vh.get(b["theme"])
            tag = ""
            if v:
                tag = {"as": "✓ hist", "op": "✗ hist", "no": "… hist"}[v["verdict"][:2]] + f" {_sg(v['history'])}"
            rows.append(f"{SHORT.get(b['theme'], b['theme'].title())[:13]:<13s} {_bar(b['z'])} {_sg(b['excess']):>6s}  {tag}")
        if rows:
            L += ["📊 <b>Themes vs SPY</b>  <i>(bar = how unusual the move is)</i>", "<pre>" + escape("\n".join(rows)) + "</pre>"]
        hits = [u for u in r["unusual"] if u["watched"]][:5]
        if hits:
            L.append("🎯 <b>Stand-out stocks</b>")
            L += [f"<code>{u['symbol']:<6s}{_sg(u['excess']):>7s}  z{u['z']:+.1f}</code>  {escape(SHORT.get(u['theme'] or '', (u['theme'] or '').title()))}" for u in hits]
        other = [u for u in r["unusual"] if not u["watched"]][:3]
        if other:
            L.append("<i>Also moving, likely other news: " + ", ".join(f"{u['symbol']} {_sg(u['excess'])}" for u in other) + "</i>")
        L.append("")

    pending = [v for v in res["vs_history"] if v["verdict"] == "not yet"]
    if pending and pb.get("symbols"):
        L.append(f"⏳ <b>Historically strongest names</b>  <i>({len(pb['events'])} past {escape(pb['kind'].replace('_', ' '))} events)</i>")
        for v in sorted(pending, key=lambda v: -abs(v["history"]))[:3]:
            same = [s for s in pb["symbols"] if s["theme"] == v["theme"] and s["n"] >= 3 and np.sign(s["avg_x1"]) == np.sign(v["history"])]
            same.sort(key=lambda s: -abs(s["avg_x1"]))
            if same:
                L.append(f"• {escape(SHORT.get(v['theme'], v['theme'].title()))}: " + ", ".join(f"<b>{s['symbol']}</b> {_sg(s['avg_x1'])}" for s in same[:3]))
        L.append("")

    ex = res.get("exposure")
    if ex and ex["rows"]:
        L.append(f"💼 <b>Your fund</b>  <i>${ex['exposed']:,} of ${ex['total']:,} in what this story moves</i>")
        for x in ex["rows"][:4]:
            now = f"{_sg(x['now'])} (${x['now_usd']:+,})" if x["now"] is not None else "—"
            hist = f"hist {_sg(x['hist'])}" if x["hist"] is not None else ""
            L.append(f"<code>{x['symbol']:<6s}${x['value']:>9,}  now {now}  {hist}</code>")
        if ex["hist_usd"]:
            L.append(f"<i>History suggests {'+' if ex['hist_usd'] >= 0 else '-'}${abs(ex['hist_usd']):,} on day 1 vs SPY for these positions.</i>")
        L.append("")
    if res["polymarket"]:
        L.append("🎲 <b>Polymarket</b>")
        for e in res["polymarket"][:3]:
            m = e["markets"][0]
            ch = m.get("change1d")
            arrow = "" if not ch or abs(ch) < 1 else (f"  ▲{ch:.0f}pt" if ch > 0 else f"  ▼{-ch:.0f}pt")
            q = f" — {escape(str(m['question'])[:34])}" if m.get("question") and m["question"] != e["title"] else ""
            L.append(f"• {escape(str(e['title'])[:55])}{q}: <b>{m['prob']:.0f}%</b>{arrow}")
        L.append("")
    L.append("<i>NOSTRO Intel · measured moves, small samples, not advice</i>")

    buttons: list[dict] = []
    if link and str(link).startswith("http"):
        buttons.append({"text": "📰 Story", "url": link})
    if res["polymarket"]:
        buttons.append({"text": "🎲 Polymarket", "url": res["polymarket"][0]["url"]})
    return "\n".join(L), ([buttons] if buttons else [])
