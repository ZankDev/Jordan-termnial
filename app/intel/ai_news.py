"""AI reading of the news: instead of keyword rules, a language model reads each story and decides
  1. triage (small fast model, headlines in batches): can this move a tradable price within hours/days? type, score 0-100, one-line reason.
  2. analysis (large model, only stories that pass): what happened, the cause -> effect chain, and which assets move which way and why,
     chosen from a fixed catalogue of measurable assets (plus tickers named in the story) so every call can be checked against prices.
The prediction is then scored at +15 min / +60 min by newsimpact.verdict (did the named assets move the predicted way?).
Budget (Groq free tier: 1000 requests/day and 8k tokens/min per model): triage 20 headlines per call; at most ANALYSE_PER_DAY deep analyses.
Falls back to the keyword rules whenever no model answers.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from app import db
from app.intel import llm

logger = logging.getLogger(__name__)
TRIAGE_MODELS = ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
ANALYSE_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
TRIAGE_BATCH = 20
ANALYSE_MIN_SCORE = 60
ANALYSE_PER_DAY = 80
MAX_AGE = 2 * 3600  # never spend the budget on old backlog

# measurable assets the model may name (all checked on Yahoo); grouped so the model sees the map of the market
CATALOGUE: dict[str, dict[str, str]] = {
    "Index futures & rates": {"ES=F": "S&P 500 futures", "NQ=F": "Nasdaq 100 futures", "ZN=F": "US 10y note futures", "DX-Y.NYB": "US dollar index", "^VIX": "VIX"},
    "Israel": {"ILS=X": "USD/ILS (up = shekel weaker)", "TA35.TA": "Tel Aviv 35", "^TA125.TA": "Tel Aviv 125", "EIS": "Israel ETF (US)", "ELAL.TA": "El Al",
               "TEVA": "Teva", "CHKP": "Check Point"},
    "Israeli defence": {"ESLT": "Elbit Systems (US)", "ESLT.TA": "Elbit Systems (TASE)", "NXSN.TA": "Next Vision", "ARYT.TA": "Aryt Industries",
                        "BSEN.TA": "Bet Shemesh Engines", "TATT.TA": "TAT Technologies"},
    "US defence": {"LMT": "Lockheed Martin", "NOC": "Northrop Grumman", "RTX": "RTX (Raytheon)", "GD": "General Dynamics", "LHX": "L3Harris",
                   "KTOS": "Kratos (drones)", "PLTR": "Palantir", "ITA": "US aerospace & defence ETF", "SHLD": "Global defence ETF"},
    "Uranium & nuclear": {"CCJ": "Cameco", "UEC": "Uranium Energy", "NXE": "NexGen", "URA": "Uranium ETF", "LEU": "Centrus (enrichment)", "BWXT": "BWX Technologies",
                          "SMR": "NuScale", "OKLO": "Oklo"},
    "Energy": {"CL=F": "WTI crude", "BZ=F": "Brent crude", "NG=F": "US natural gas", "XLE": "US energy ETF", "XOM": "Exxon", "CVX": "Chevron", "OXY": "Occidental"},
    "Shipping & travel": {"FRO": "Frontline (tankers)", "STNG": "Scorpio Tankers", "INSW": "International Seaways", "ZIM": "ZIM", "DAL": "Delta", "UAL": "United", "JETS": "Airlines ETF"},
    "Safe havens & metals": {"GC=F": "Gold", "SI=F": "Silver", "GDX": "Gold miners", "TLT": "US long bonds"},
    "Tech & crypto": {"NVDA": "Nvidia", "SMH": "Semiconductors ETF", "CRWD": "CrowdStrike", "PANW": "Palo Alto", "BTC-USD": "Bitcoin", "ETH-USD": "Ether", "COIN": "Coinbase", "MSTR": "Strategy"},
    "Agriculture & China": {"ZW=F": "Wheat", "ZC=F": "Corn", "ZS=F": "Soybeans", "FXI": "China large caps", "KWEB": "China internet", "CNY=X": "USD/CNY"},
}
NAMES = {s: n for g in CATALOGUE.values() for s, n in g.items()}
TYPES = ["fed", "inflation_data", "jobs_data", "us_data", "agriculture", "earnings", "mna", "regulatory", "analyst", "deescalation", "war_escalation", "trade_policy",
         "energy", "crypto", "israel_macro", "politics", "disaster", "flows", "trump_policy", "company", "other", "noise"]

MATERIALITY = (
    "MATERIALITY LADDER (markets price the CHANGE in risk, not the existence of conflict; during an ongoing war, tension is the baseline and already priced):\n"
    "- noise (score 0-10): single tactical incidents inside an ongoing conflict: one drone/aircraft infiltration or siren in a border community, an interception, "
    "an IDF strike in Gaza/Lebanon/Syria, a militant killed, routine Russia-Ukraine strikes, a travel advisory, threats and rhetoric, repeated reports of an event already known.\n"
    "- local (10-25): a larger but contained incident with no infrastructure or supply impact.\n"
    "- regional (25-60): strikes on capitals or major civilian infrastructure, mass casualties, a clear widening (new country, new front), assassination of a top leader.\n"
    "- systemic (60-100): energy/shipping supply hit (oil facilities, tankers, Hormuz/Red Sea/Suez), nuclear sites, war between states starting or ending, "
    "ceasefire made or broken, central-bank surprise, major sanctions/tariffs, a market-wide shock.\n"
    "Backtests: only systemic shocks reliably move indices, the VIX, the shekel or defence stocks; routine sirens and tactical strikes are inside the risk premium.\n"
)

TRIAGE_SYSTEM = (
    "You are the news desk of a trading firm. For each headline decide if it can move the price of a tradable asset (index futures, rates, FX, oil, gold, "
    "crypto, sector stocks or a named company) noticeably within hours or days. Headlines may be Hebrew or English.\n"
    "NOT market-moving: local crime, police, accidents, court cases about individuals, real estate listings, consumer and lifestyle stories, sports, celebrity, "
    "opinion, routine domestic politics, old news, prices of single apartments or cars. Israeli security news matters only when it is a real military or "
    "strategic development (attack by or on Iran/Hezbollah/Houthis, a large escalation, a ceasefire), not a local incident.\n"
    "Market-moving examples: central-bank decisions, major economic data surprises, war escalation between states, attacks on energy infrastructure or shipping, "
    "sanctions and tariffs, OPEC decisions, big company earnings/guidance/M&A/FDA, presidential policy statements on trade or the Fed.\n"
    + MATERIALITY +
    "type: attacks, missiles and military action between states or militias = war_escalation (even if oil is affected); ceasefires/deals = deescalation.\n"
    f"type must be one of: {', '.join(TYPES)}.\n"
    'co = listed companies that are the subject of the headline or directly hit by it (max 2; skip if none): n = company name, '
    'x = home exchange code (NYSE NASDAQ TSX BMV B3 BYMA LSE XETRA EPA AMS BRU MIL BME SIX STO CPH HEL OSL WSE BIST TASE TADAWUL DFM ADX QSE JSE EGX '
    'TSE SSE SZSE HKEX KRX KOSDAQ TWSE NSE BSE SGX BURSA IDX SET PSE HOSE ASX NZX), t = home ticker as listed there (e.g. 7203, SIE, 2222, PETR4, 005930, TEVA). '
    'Company news can matter even when mm is false (earnings, guidance, contracts, M&A, lawsuits, FDA, management changes).\n'
    'Answer JSON only: {"items":[{"id":<id>,"mm":true|false,"score":0-100,"type":"...","why":"max 12 words","co":[{"n":"...","x":"...","t":"..."}]}]} with one item per headline. '
    "score = probability that at least one related asset makes an unusual move (more than 2x its normal move) within 60 minutes. Be strict: most headlines score below 20."
)

ANALYSE_SYSTEM = (
    "You are a senior cross-asset strategist. A news story just broke. Explain how markets should react, step by step, like a professional desk note.\n"
    "Rules: think about the ORDER of events (what happened, who is affected, which flows follow). Name only assets from the catalogue below, or a ticker "
    "explicitly named in the story. Give the direction (up/down) and strength (1 small, 2 clear, 3 large) for each, with a short reason. "
    "Include second-order effects (e.g. Iranian attack on Israel -> oil up on supply risk, Israeli defence stocks up on demand (always consider ESLT/ESLT.TA, NXSN.TA, ARYT.TA when Israel is attacked or at war), US defence primes up (LMT, RTX, NOC), "
    "uranium & nuclear names up whenever nuclear sites, nuclear programmes or nuclear threats are mentioned (e.g. Dimona, Natanz, Fordow, Bushehr), shekel weaker, Tel Aviv down, airlines down, gold up). Do not list assets with no real link.\n"
    + MATERIALITY +
    "Classify the event on the ladder (materiality) and whether it is new, a continuation of a known pattern, or a repeat of an event already reported (novelty). "
    "The current-regime numbers and the alerts already sent are given in the message: if this is the same event as one of them, set same_as to its number. "
    "Do NOT apply a generic risk-off template to a local or noise event: then market_moving is false and affected is empty.\n"
    "If the story is not truly market-moving, say so with market_moving false and an empty list.\n"
    "Also judge if it is likely already priced (old news, expected, repeated).\n"
    'Answer JSON only: {"market_moving":true|false,"materiality":"noise|local|regional|systemic","novelty":"new|continuation|repeat","same_as":null|<number>,"event":"one sentence, what happened","chain":["cause -> effect", "..."] (2-5 steps),'
    '"affected":[{"symbol":"...","dir":"up|down","strength":1-3,"why":"max 10 words"}] (max 10, strongest first),'
    '"priced_in":"short note","vs_history":"one sentence: how this compares with the past events given (if any)","confidence":0.0-1.0,"watch":"what would confirm or cancel this"}\n\nCATALOGUE:\n'
    + "\n".join(f"{g}: " + ", ".join(f"{s} ({n})" for s, n in m.items()) for g, m in CATALOGUE.items())
)


def ensure() -> None:
    from app.intel import sources

    sources.ensure()
    with db.read_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(news_stories)").fetchall()}
    if "ai" not in cols:
        with db.get_conn() as conn:
            conn.execute("ALTER TABLE news_stories ADD COLUMN ai TEXT")


def enabled() -> bool:
    return llm.config() is not None


def _call(models: list[str], system: str, user: str, max_tokens: int, reasoning: str = "low") -> tuple[dict[str, Any] | None, str | None]:
    for m in models:
        out = llm.chat_json(system, user, max_tokens=max_tokens, model=m, reasoning=reasoning, retries=2)
        if out:
            return out, m
    return None, None


def triage(stories: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """{story id: {mm, score, type, why, model}} for a batch of headlines."""
    if not stories:
        return {}
    lines = [f"[{s['id']}] ({', '.join(json.loads(s.get('kinds') or '[]'))}) {' '.join(str(s['title']).split())[:220]}" for s in stories]
    out, model = _call(TRIAGE_MODELS, TRIAGE_SYSTEM, "Headlines:\n" + "\n".join(lines), max_tokens=3500)
    res: dict[int, dict[str, Any]] = {}
    for it in (out or {}).get("items", []) if isinstance(out, dict) else []:
        try:
            sid = int(it["id"])
            score = max(0, min(100, int(float(it.get("score", 0)))))
        except (KeyError, TypeError, ValueError):
            continue
        t = it.get("type") if it.get("type") in TYPES else "other"
        co = [c for c in (it.get("co") or []) if isinstance(c, dict) and c.get("n")][:2]
        res[sid] = {"mm": bool(it.get("mm")) and score >= 15, "score": score, "type": t, "why": str(it.get("why", ""))[:120], "model": model,
                    "co": [{"n": str(c["n"])[:80], "x": str(c.get("x") or "")[:10], "t": str(c.get("t") or "")[:16]} for c in co]}
    return res


def analyse(story: dict[str, Any], texts: list[str], tickers: list[str]) -> dict[str, Any] | None:
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(story["ts"]))
    user = f"Time: {when}\nHeadline: {story['title']}\n" + ("Other reports:\n- " + "\n- ".join(t[:400] for t in texts[:3]) + "\n" if texts else "")
    if tickers:
        user += "Tickers named in the story: " + ", ".join(tickers) + "\n"
    comps = (story.get("_tri") or {}).get("companies") or []
    if comps:
        user += "Listed companies in the story (you may name these symbols too): " + "; ".join(
            f"{c['name']} = {c['symbol']}" + (f" (US: {c['us']})" if c.get("us") else "") for c in comps) + "\n"
    analog_ids: list[int] = []
    try:  # real precedent: similar past events with the MEASURED first-session moves, plus the average playbook for this type
        from app.intel import history

        ntype = (story.get("_tri") or {}).get("type")
        an_ev = history.analogues(story["title"] + " " + str((story.get("_tri") or {}).get("why", "")), ntype, k=5)
        if an_ev:
            analog_ids = [e["id"] for e in an_ev]
            user += "\nSimilar past events (measured first-session moves):\n" + "\n".join("- " + history.brief(e, n=7) for e in an_ev) + "\n"
        if ntype:
            pb = history.playbook(ntype)
            if pb["assets"]:
                user += (f"Average reaction over {pb['events']} past {ntype} events: "
                         + ", ".join(f"{history.name(r['symbol'])} {r['avg_d0']:+.1f}% (same sign {r['same_sign'] * 100:.0f}%)" for r in pb["assets"][:10]) + "\n")
        user += "Use this history: say where this event differs from the precedents (scale, surprise, already priced).\n"
    except Exception:  # noqa: BLE001
        logger.exception("history context failed")
    user += _context((story.get("_tri") or {}).get("type"), story.get("ts"))
    out, model = _call(ANALYSE_MODELS, ANALYSE_SYSTEM, user, max_tokens=4000, reasoning="medium")
    if not isinstance(out, dict):
        return None
    comps = (story.get("_tri") or {}).get("companies") or []
    allowed = set(NAMES) | {t.upper() for t in tickers} | {c["symbol"] for c in comps} | {c["us"] for c in comps if c.get("us")}
    aff = []
    for a in out.get("affected") or []:
        try:
            sym = str(a["symbol"]).upper().strip()
        except (KeyError, TypeError):
            continue
        if sym not in allowed or a.get("dir") not in ("up", "down") or any(x["symbol"] == sym for x in aff):
            continue
        aff.append({"symbol": sym, "dir": a["dir"], "strength": max(1, min(3, int(a.get("strength") or 1))), "why": str(a.get("why", ""))[:90]})
    try:
        conf = max(0.0, min(1.0, float(out.get("confidence") or 0)))
    except (TypeError, ValueError):
        conf = 0.0
    return {"market_moving": bool(out.get("market_moving")) and bool(aff), "event": str(out.get("event", ""))[:240],
            "chain": [str(x)[:160] for x in (out.get("chain") or [])][:5], "affected": aff[:10], "priced_in": str(out.get("priced_in", ""))[:160],
            "confidence": round(conf, 2), "watch": str(out.get("watch", ""))[:160], "model": model, "analogues": analog_ids,
            "materiality": out.get("materiality") if out.get("materiality") in ("noise", "local", "regional", "systemic") else "local",
            "novelty": out.get("novelty") if out.get("novelty") in ("new", "continuation", "repeat") else "continuation",
            "same_as": out.get("same_as") if isinstance(out.get("same_as"), int) else None,
            "vs_history": str(out.get("vs_history", ""))[:200]}


def _resolve_companies(batch: list[dict[str, Any]]) -> None:
    """Each company the model named -> its listed stock (home exchange + US listing), worldwide. Stored in triage['companies']."""
    from concurrent.futures import ThreadPoolExecutor

    from app.intel import company

    jobs = [(s, c) for s in batch for c in (s["_tri"].get("co") or []) if s["_tri"]["score"] >= 15]
    if not jobs:
        return
    with ThreadPoolExecutor(4) as ex:
        found = list(ex.map(lambda sc: company.resolve(sc[1]["n"], sc[1]["x"], sc[1]["t"]), jobs))
    for (s, _), f in zip(jobs, found):
        if f and not any(x["symbol"] == f["symbol"] for x in s["_tri"].setdefault("companies", [])):
            s["_tri"]["companies"].append(f)


def _context(ntype: str | None, before: int | None = None) -> str:
    """Current regime (how many stories of this type in 7 days and how many really moved a price) + alerts already sent in 12 h."""
    now = int(time.time())
    out = ""
    try:
        with db.read_conn() as conn:
            if ntype:
                n7, mv = conn.execute("SELECT COUNT(*), SUM(CASE WHEN verdict IN ('MOVED','ABSORBED') THEN 1 ELSE 0 END) FROM news_stories WHERE ntype=? AND ts > ? "
                                      "AND verdict IS NOT NULL AND verdict != 'NO DATA'", (ntype, now - 7 * 86400)).fetchone()
                if n7:
                    out += (f"\nCurrent regime: {n7} measured {ntype} stories in the last 7 days; {mv or 0} ({(mv or 0) / n7 * 100:.0f}%) moved any price unusually. "
                            "A high count means this kind of news is the baseline, not a surprise.\n")
            al = conn.execute("SELECT title, ai FROM news_stories WHERE alerted >= 1 AND ts > ? AND ts < ? AND ai LIKE '%\"analysis\": {%' ORDER BY ts DESC LIMIT 12",
                              (now - 12 * 3600, before or now + 1)).fetchall()
        if al:
            out += "Alerts already sent in the last 12 h:\n" + "\n".join(
                f"{i}. {(json.loads(r[1] or '{}').get('analysis') or {}).get('event') or r[0][:140]}" for i, r in enumerate(al, 1)) + "\n"
    except Exception:  # noqa: BLE001
        logger.exception("regime context failed")
    return out


def alert_ok(an: dict[str, Any], ntype: str | None) -> tuple[bool, str]:
    """Gate for the instant alert: materiality, novelty, duplicates, and this type's measured alert precision so far."""
    if not an.get("market_moving") or an.get("confidence", 0) < 0.5:
        return False, "not market-moving"
    if an.get("same_as") is not None or an.get("novelty") == "repeat":
        return False, "same event already alerted"
    mat = an.get("materiality")
    if mat in ("noise", "local"):
        return False, f"materiality {mat}"
    if mat == "regional" and an.get("novelty") != "new":
        return False, "regional continuation"
    if mat != "systemic" and ntype:
        p = precision(ntype)
        if p["n"] >= 8 and p["rate"] < 0.25:
            return False, f"{ntype} alerts moved prices only {p['rate'] * 100:.0f}% of {p['n']} times: systemic events only"
    return True, "ok"


def precision(ntype: str, days: int = 30) -> dict[str, Any]:
    """Of the stories of this type the AI called market-moving, how many really MOVED a price (measured)."""
    with db.read_conn() as conn:
        rows = conn.execute("SELECT verdict FROM news_stories WHERE ntype=? AND ts > ? AND verdict IS NOT NULL AND verdict != 'NO DATA' "
                            "AND ai LIKE '%\"market_moving\": true%'", (ntype, int(time.time()) - days * 86400)).fetchall()
    n = len(rows)
    hit = sum(1 for r in rows if r[0] in ("MOVED", "ABSORBED"))
    return {"n": n, "hits": hit, "rate": hit / n if n else 0.0}


def _analysed_today() -> int:
    with db.read_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM news_stories WHERE ai LIKE '%\"analysis\": {%' AND ts > ?", (int(time.time()) - 86400,)).fetchone()[0]


def run(max_batches: int = 3) -> dict[str, int]:
    """Triage fresh un-read stories, then analyse the ones that pass. Writes news_stories.ai and, when the AI answered, ntype/expected."""
    if not enabled():
        return {"triaged": 0, "analysed": 0}
    ensure()
    now = int(time.time())
    done = {"triaged": 0, "analysed": 0}
    with db.read_conn() as conn:
        fresh = [dict(r) for r in conn.execute("SELECT id, ts, title, kinds, tickers FROM news_stories WHERE ai IS NULL AND ntype IS NOT NULL AND ts > ? "
                                               "ORDER BY ts DESC LIMIT ?", (now - MAX_AGE, TRIAGE_BATCH * max_batches)).fetchall()]
    upd: list[tuple] = []
    for i in range(0, len(fresh), TRIAGE_BATCH):
        batch = fresh[i:i + TRIAGE_BATCH]
        res = triage(batch)
        if not res:
            break  # no model answered: leave them for the next cycle (keyword rules still run)
        for s in batch:
            r = res.get(s["id"]) or {"mm": False, "score": 0, "type": "noise", "why": "not returned by the model", "model": None}
            s["_tri"] = r
        _resolve_companies(batch)
        for s in batch:
            r = s["_tri"]
            ntype = r["type"] if r["mm"] else ("company" if r["type"] in ("other", "noise") else r["type"]) if r.get("companies") and r["score"] >= 20 else "noise"
            upd.append((json.dumps({"triage": r}), ntype, r["score"] / 100, s["id"]))
        done["triaged"] += len(batch)
    if upd:
        with db.get_conn() as conn:
            conn.executemany("UPDATE news_stories SET ai=?, ntype=?, expected=? WHERE id=?", upd)
    # a headline that names a listed company: watch that stock for a small-cap explosion on the news (app/intel/movers.py checks size and volume)
    try:
        from app.intel import movers

        for s in fresh:
            r = s.get("_tri") or {}
            cos = [c.get("us") or c.get("symbol") for c in (r.get("companies") or [])]
            cos = [c for c in cos if c and "." not in c and "=" not in c and "^" not in c]
            if cos and r.get("score", 0) >= 20 and now - int(s["ts"]) < 3 * 3600:
                movers.watch(f"co:{s['id']}", int(s["ts"]), s["title"], "company", symbols=cos)
    except Exception:  # noqa: BLE001
        logger.exception("movers company watch failed")
    cand = sorted([s for s in fresh if s.get("_tri", {}).get("mm") and s["_tri"]["score"] >= ANALYSE_MIN_SCORE], key=lambda s: -s["_tri"]["score"])
    budget = ANALYSE_PER_DAY - _analysed_today()
    for s in cand[:max(0, min(4, budget))]:
        with db.read_conn() as conn:
            texts = [r[0] for r in conn.execute("SELECT text FROM news_items WHERE story_id=? ORDER BY ts LIMIT 4", (s["id"],)).fetchall()]
        a = analyse(s, [t for t in texts if t and t[:80] not in s["title"]], json.loads(s.get("tickers") or "[]"))
        if not a:
            continue
        exp = s["_tri"]["score"] / 100 if a["market_moving"] else min(0.1, s["_tri"]["score"] / 100)
        with db.get_conn() as conn:
            conn.execute("UPDATE news_stories SET ai=?, expected=? WHERE id=?", (json.dumps({"triage": s["_tri"], "analysis": a}), exp, s["id"]))
        done["analysed"] += 1
    return done


def of(story: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(story.get("ai") or "{}") or {}
    except ValueError:
        return {}


def check(analysis: dict[str, Any], m: dict[str, Any]) -> dict[str, Any] | None:
    """Score the AI call against the measured move: an asset is a hit when it moved the predicted way by at least 1.5x its normal 15-min move."""
    rows = []
    for a in analysis.get("affected") or []:
        v = (m.get("assets") or {}).get(a["symbol"]) or {}
        z, r = v.get("z15"), v.get("r15")
        if z is None:
            continue
        hit = (z >= 1.5) if a["dir"] == "up" else (z <= -1.5)
        wrong = (z <= -1.5) if a["dir"] == "up" else (z >= 1.5)
        rows.append({"symbol": a["symbol"], "dir": a["dir"], "r15": r, "z15": z, "hit": hit, "wrong": wrong})
    if not rows:
        return None
    return {"rows": rows, "hits": sum(r["hit"] for r in rows), "wrong": sum(r["wrong"] for r in rows), "n": len(rows)}


def scorecard(days: int = 30) -> dict[str, Any]:
    """How good are the AI calls so far: share of predicted assets that moved the predicted way, and share that moved against it."""
    with db.read_conn() as conn:
        rows = conn.execute("SELECT measured FROM news_stories WHERE ai LIKE '%\"analysis\": {%' AND measured IS NOT NULL AND ts > ?",
                            (int(time.time()) - days * 86400,)).fetchall()
    n = hits = wrong = stories = 0
    for r in rows:
        c = (json.loads(r[0] or "{}") or {}).get("ai_check")
        if c:
            stories += 1
            n += c["n"]
            hits += c["hits"]
            wrong += c["wrong"]
    return {"stories": stories, "calls": n, "hit_rate": round(hits / n, 3) if n else None, "wrong_rate": round(wrong / n, 3) if n else None}
