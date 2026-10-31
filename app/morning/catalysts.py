"""CATALYST TRACKER — the six event types that most reliably move a stock hard, scanned across the WHOLE market (not
just the 300-name screener universe) and folded into the morning brief as its own section, "TODAY'S CATALYSTS".

  1. EARNINGS         Nasdaq's public earnings calendar (market_routes._earn_day) for today + yesterday, matched
                       against the realised pre/after-market move via Yahoo quotes and the EPS surprise vs consensus.
  2. FDA / CLINICAL    Yahoo News search (invest_routes._yahoo_news), which tags each headline with relatedTickers —
                       no manual company-name-to-ticker guessing needed. Classified up/down by approval vs rejection
                       wording.
  3. M&A               Same source, classified target vs acquirer by active/passive phrasing in the headline
                       ("X to acquire Y" vs "Y to be acquired by X"). A best-effort read of the headline text alone —
                       flagged as "likely" since the exact roles are not always resolvable from the title.
  4. MANAGEMENT        CEO/CFO resignations, firings and new appointments.
  5. MACRO             The existing high/medium-impact economic calendar (market_routes /calendar): not per-stock, but
                       flagged with which kind of names typically move most on that release.
  6. LEGAL / SCANDAL    Lawsuits, investigations, short-seller reports, data breaches.

Every hit carries a REFERENCE range, not a per-stock forecast: how far this category has historically moved a stock,
bucketed by market cap where that matters (mega/large-cap earnings move less than a small-cap biotech beat). The "why"
line always states what was actually found — the headline or the calendar entry — never a guess dressed up as fact.

Best-effort by design: FDA/M&A/management/legal have no free structured calendar, so they are news-search based and
will miss stories no outlet covered, mis-tag an unusual headline, or occasionally include a stale republished story.
Earnings and macro are structured data and are reliable.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

# reference move ranges, from how each category has historically played out — shown to the user as context, never as
# a promise. (low, high) in percent; direction is decided per hit (up / down / either).
RANGES = {
    "earnings_mega": (5, 15), "earnings_small": (15, 40), "earnings_biotech": (30, 80),
    "fda": (20, 100), "mna_target": (20, 50), "mna_acquirer": (2, 8),
    "management": (5, 25), "macro_index": (1, 3), "macro_sector": (5, 10), "legal": (10, 40),
}
CATEGORY_LABEL = {"earnings": "EARNINGS", "fda": "FDA / CLINICAL TRIAL", "mna": "M&A", "management": "MANAGEMENT CHANGE",
                  "legal": "LEGAL / REGULATORY", "macro": "MACRO RELEASE"}
BIOTECH_HINTS = ("biotech", "pharmaceutical", "drug", "therapeutic")

NEWS_QUERIES = {
    "fda": ["FDA approval stock", "FDA rejects stock", "FDA complete response letter", "clinical trial results stock", "Phase 3 trial stock"],
    "mna": ["agrees to acquire", "to acquire for", "definitive merger agreement", "tender offer for shares"],
    "management": ["steps down as CEO", "resigns as CEO stock", "names new CEO", "appoints CEO", "CFO resigns"],
    "legal": ["SEC investigation stock", "class action lawsuit stock", "short seller report", "data breach stock", "DOJ investigation stock"],
}
FDA_UP = re.compile(r"\b(approv|clear(?:s|ed)|grant(?:s|ed)|positive|success(?:ful)?|breakthrough|meets? (?:primary|endpoint))\b", re.I)
FDA_DOWN = re.compile(r"\b(reject|rejection|CRL|complete response letter|fail(?:s|ed)?|discontinu|halt(?:s|ed)?|miss(?:es|ed)? (?:primary|endpoint))\b", re.I)
MNA_PASSIVE = re.compile(r"\bto be acquired by\b|\bagrees to be acquired\b|\bwill be acquired by\b|\bbeing acquired by\b", re.I)
MNA_VERB = re.compile(r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\btender offer\b|\bbuyout\b", re.I)
LEGAL_HINT = re.compile(r"\blawsuit|investigat|fraud|breach|hack(?:ed|er)?|cyberattack|recall|subpoena|indict|short.seller|class action", re.I)
MGMT_HINT = re.compile(r"\bCEO\b|\bCFO\b|\bCOO\b|steps? down|resign|appoint|names? (?:a )?(?:new )?(?:chief|CEO|CFO|COO)|interim (?:CEO|CFO)|chief executive", re.I)
FDA_HINT = re.compile(r"\bFDA\b|clinical trial|\bphase (?:1|2|3|I|II|III)\b|PDUFA|breakthrough therapy|complete response letter|\bNDA\b|\bBLA\b", re.I)
# a search hit that doesn't actually contain the category's own keywords is Google's loose relevance ranking pulling
# in something tangential (e.g. a generic "security" product headline surfacing under a "data breach" query) — drop it
CATEGORY_HINT = {"fda": FDA_HINT, "mna": MNA_VERB, "management": MGMT_HINT, "legal": LEGAL_HINT}


def _brief_deps():
    from app.api import invest_routes as inv
    from app.api import market_routes as mr
    from app.morning import brief

    return inv, mr, brief


# ------------------------------------------------------------------ headline -> ticker (no per-headline API call)
# Yahoo's own news-search endpoint (invest_routes._yahoo_news) only returns results for a name/ticker it already
# recognises as the query itself (it is a company-search box, not a general news search) — a generic phrase like
# "agrees to acquire" returns nothing from it. Google News RSS (invest_routes._google_news) IS a general search and
# returns real results for these phrases, but tags no ticker, so headlines are matched against the SEC's full company
# list (invest_routes._sec_map, all US filers) by name instead of hitting an API per headline.
_SUFFIX = re.compile(r"\b(inc|corp|corporation|ltd|plc|co|company|holdings|holding|group|sa|nv|ag|the)\b\.?", re.I)
# single-word company names that collide with a common English word (Match Group -> "match", Target Corp -> "target",
# Strategy Inc -> "strategy"...) are dropped outright: even the capitalization check below lets sentence-initial and
# other incidental capitals through often enough that these specific well-known collisions are worth excluding by name.
STOP_NAMES = {"first", "one", "new", "national", "united", "general", "american", "global", "capital", "match",
              "target", "strategy", "block", "cvs", "gap", "chart", "core", "mind", "cash", "vera", "root", "enact",
              "track", "live", "docs", "hold", "fair", "share", "boost", "surge", "rally", "sharp", "prime"}


def _norm_name(name: str) -> str:
    return re.sub(r"[^\w\s]", " ", _SUFFIX.sub(" ", name.lower())).strip()
    # e.g. "Caterpillar Inc." -> "caterpillar"; "Advanced Micro Devices, Inc." -> "advanced micro devices"


def _name_index() -> dict[str, list[tuple[str, str]]]:
    """first significant word (lowercase) -> [(normalized full name, ticker), ...], built from the SEC's full US
    company list. An inverted index so matching a headline is a handful of dict lookups, not 10,000+ substring scans."""
    inv, _, _ = _brief_deps()
    idx: dict[str, list[tuple[str, str]]] = {}
    for ticker, (_cik, title) in inv._sec_map().items():
        norm = _norm_name(title)
        if len(norm) < 5 or norm in STOP_NAMES or not re.fullmatch(r"[A-Z]{1,5}(?:[.\-][A-Z]{1,2})?", ticker):
            continue
        first = norm.split(" ", 1)[0]
        if len(first) < 4 and " " not in norm:  # a single short word alone is too generic to match on
            continue
        idx.setdefault(first, []).append((norm, ticker))
    return idx


def _match_tickers(title: str, idx: dict[str, list[tuple[str, str]]], limit: int = 3) -> list[tuple[str, str, int]]:
    """(ticker, matched name, position in the title) for every company name found, longest match first at each spot,
    skipping a headline that loosely matches many names at once (a sign of a bad/too-generic match)."""
    low = " " + re.sub(r"[^\w\s]", " ", title.lower()) + " "
    words = set(re.findall(r"[a-z]{3,}", low))
    hits: list[tuple[str, str, int]] = []
    seen_span: set[int] = set()
    candidates: list[tuple[str, str]] = []
    for w in words:
        candidates.extend(idx.get(w, ()))
    for norm, ticker in sorted(candidates, key=lambda x: -len(x[0])):
        pos = low.find(f" {norm} ")
        if pos < 0 or pos in seen_span:
            continue
        if " " not in norm:
            # a single-word company name is the riskiest case (it can collide with an ordinary English word): require
            # some length, AND that the word is capitalized in the original title — imperfect, since headlines are
            # often Title-Cased throughout (capitalizing "Track" in "...Stock On Track To...") rather than only
            # proper nouns, but it still screens out plain lowercase mid-sentence uses.
            if len(norm) < 6:
                continue
            cap = norm[0].upper() + norm[1:]
            if not re.search(r"(?<![A-Za-z])" + re.escape(cap) + r"(?![A-Za-z])", title):
                continue
        hits.append((ticker, norm, pos))
        seen_span.add(pos)
    hits.sort(key=lambda h: h[2])
    return hits[:limit] if len(hits) <= limit + 2 else []  # too many hits at once: unreliable, drop the whole headline


def _mcap_bucket(mcap: float | None, sector: str | None = None) -> str:
    is_bio = bool(sector) and any(h in sector.lower() for h in BIOTECH_HINTS)
    if is_bio and (mcap or 0) < 10e9:
        return "earnings_biotech"
    if (mcap or 0) >= 10e9:
        return "earnings_mega"
    return "earnings_small"


def _range_str(key: str, extra_ceiling: str | None = None) -> str:
    lo, hi = RANGES[key]
    return f"{lo}%–{hi}%{extra_ceiling or ''}"


# ------------------------------------------------------------------ 1. earnings (structured: Nasdaq calendar)
def earnings_catalysts(hours: float = 20.0) -> list[dict[str, Any]]:
    inv, mr, brief = _brief_deps()
    now = dt.datetime.now(NY)
    today, yday = now.date().isoformat(), (now.date() - dt.timedelta(days=1)).isoformat()
    try:
        rows_today = mr._earn_day(today)
    except Exception:  # noqa: BLE001
        rows_today = []
    try:
        rows_yday = mr._earn_day(yday) if now.hour < 13 else []  # yesterday's AMC only still relevant before midday
    except Exception:  # noqa: BLE001
        rows_yday = []
    bmo_today = [r for r in rows_today if r.get("when") == "BMO" and r.get("symbol")]
    amc_today = [r for r in rows_today if r.get("when") == "AMC" and r.get("symbol")]
    amc_yday = [r for r in rows_yday if r.get("when") == "AMC" and r.get("symbol")]
    reacted = bmo_today + amc_yday  # these have already had a chance to move
    symbols = list({r["symbol"] for r in reacted + amc_today})
    if not symbols:
        return []
    quotes = brief._quotes(symbols)
    out = []
    for r in reacted:
        q = quotes.get(r["symbol"], {})
        move = q.get("preMarketChangePercent")
        src = "pre-market"
        if move is None:
            move = q.get("regularMarketChangePercent")
            src = "today's session"
        if move is None:
            continue
        surprise = None
        if r.get("eps") is not None and r.get("eps_est"):
            surprise = round(100 * (r["eps"] - r["eps_est"]) / abs(r["eps_est"]), 1)
        bucket = _mcap_bucket(r.get("mcap"))
        why = f"reported {'this morning (BMO)' if r in bmo_today else 'after yesterday close (AMC)'}"
        if surprise is not None:
            why += f", EPS {'beat' if surprise >= 0 else 'missed'} consensus by {abs(surprise):.0f}%"
        elif r.get("eps_est") is not None:
            why += f", consensus EPS ${r['eps_est']:.2f}"
        out.append({"symbol": r["symbol"], "name": r.get("name"), "category": "earnings", "sub": bucket,
                    "direction": "up" if move >= 0 else "down", "observed_move_pct": round(move, 1), "observed_src": src,
                    "eps_surprise_pct": surprise, "mcap": r.get("mcap"), "typical_range": _range_str(bucket, "+" if bucket == "earnings_biotech" else ""),
                    "why": why, "confidence": "high"})
    for r in amc_today:
        bucket = _mcap_bucket(r.get("mcap"))
        out.append({"symbol": r["symbol"], "name": r.get("name"), "category": "earnings", "sub": bucket, "direction": "pending",
                    "observed_move_pct": None, "observed_src": None,
                    "eps_surprise_pct": None, "mcap": r.get("mcap"), "typical_range": _range_str(bucket, "+" if bucket == "earnings_biotech" else ""),
                    "why": f"reports after today's close (AMC) — consensus EPS {'$' + format(r['eps_est'], '.2f') if r.get('eps_est') else 'n/a'}; moves at tomorrow's open",
                    "confidence": "high"})
    out.sort(key=lambda x: (-abs(x["observed_move_pct"] or 0), -(x["mcap"] or 0)))
    return out


# ------------------------------------------------------------------ 2/3/4/6. news-search categories (FDA, M&A, management, legal)
def _scan_query(inv: Any, category: str, q: str, cutoff: float) -> list[dict[str, Any]]:
    # Google News RSS can rate-limit / return an empty batch when hit with many concurrent requests from one IP at
    # once (this module fires ~18 queries); one retry after a short backoff recovers almost all of those.
    for attempt in range(2):
        try:
            items = inv._google_news(f"{q} when:2d")  # bias Google's own ranking to recent items before the ts filter below
            if items:
                return [{**it, "category": category} for it in items if (it.get("ts") or 0) >= cutoff]
        except Exception:  # noqa: BLE001
            pass
        if attempt == 0:
            time.sleep(1.5)
    return []


def news_catalysts(hours: float = 20.0) -> dict[str, list[dict[str, Any]]]:
    inv, mr, _ = _brief_deps()
    cutoff = time.time() - hours * 3600
    jobs = [(cat, q) for cat, queries in NEWS_QUERIES.items() for q in queries]
    with ThreadPoolExecutor(min(5, len(jobs))) as pool:  # stay well under Google News RSS's per-IP burst limit
        results = list(pool.map(lambda j: _scan_query(inv, j[0], j[1], cutoff), jobs))
    idx = _name_index()
    by_cat: dict[str, dict[str, dict[str, Any]]] = {"fda": {}, "mna": {}, "management": {}, "legal": {}}
    roles: dict[str, str] = {}  # symbol -> "target" | "acquirer (likely)", set only by the M&A ordering logic below
    seen_titles: set[str] = set()
    for items in results:
        for it in items:
            cat, title = it["category"], it["title"]
            key = re.sub(r"\W+", "", title.lower())[:60]
            if not title or key in seen_titles or not CATEGORY_HINT[cat].search(title):
                continue
            seen_titles.add(key)
            matches = _match_tickers(title, idx)
            if cat == "mna" and len(matches) >= 2:
                # order in the headline decides the subject; active voice ("X to acquire Y") -> subject is the
                # acquirer, passive ("Y to be acquired by X") -> subject (still mentioned first, in "Y ... by X") is
                # the target. Best-effort: relies on the headline naming the subject company first, which is the
                # normal style for financial press ("X to Acquire Y for $..."), but is not guaranteed.
                passive = bool(MNA_PASSIVE.search(title))
                subj_role, other_role = ("target", "acquirer (likely)") if passive else ("acquirer (likely)", "target")
                for i, (sym, _name, _pos) in enumerate(matches[:2]):
                    roles[sym] = subj_role if i == 0 else other_role
            for sym, name, _pos in matches:
                bucket = by_cat[cat].setdefault(sym, {"symbol": sym, "category": cat, "company": name, "headlines": []})
                if len(bucket["headlines"]) < 3:
                    bucket["headlines"].append({"title": title, "link": it["link"], "ts": it["ts"], "source": it.get("source")})
    out: dict[str, list[dict[str, Any]]] = {}
    for cat, syms in by_cat.items():
        rows = []
        for sym, b in syms.items():
            title = b["headlines"][0]["title"]
            rows.append(_classify(cat, sym, title, b["headlines"], roles.get(sym)))
        rows.sort(key=lambda r: -r["headlines_n"])
        out[cat] = rows
    return out


def _classify(cat: str, sym: str, title: str, headlines: list[dict[str, Any]], role: str | None = None) -> dict[str, Any]:
    row = {"symbol": sym, "category": cat, "headlines": headlines, "headlines_n": len(headlines), "confidence": "medium"}
    if cat == "fda":
        up, down = bool(FDA_UP.search(title)), bool(FDA_DOWN.search(title))
        row.update(direction="up" if up and not down else "down" if down and not up else "either",
                   typical_range=_range_str("fda", "+"), why=f'FDA/clinical headline: "{title[:140]}"')
    elif cat == "mna":
        role = role or ("target" if MNA_PASSIVE.search(title) else "acquirer (likely)" if MNA_VERB.search(title) else "party")
        rng = "mna_target" if "target" in role else "mna_acquirer"
        row.update(direction="up" if "target" in role else "down", role=role, typical_range=_range_str(rng),
                   confidence="medium" if role != "party" else "low", why=f'M&A headline ({role}): "{title[:140]}"')
    elif cat == "management":
        row.update(direction="either", typical_range=_range_str("management"), why=f'Management change headline: "{title[:140]}"')
    elif cat == "legal":
        row.update(direction="down", typical_range=_range_str("legal"), why=f'Legal/regulatory headline: "{title[:140]}"')
    return row


# ------------------------------------------------------------------ 5. macro (structured: existing econ calendar)
def macro_catalysts() -> list[dict[str, Any]]:
    _, mr, _ = _brief_deps()
    try:
        cal = mr.api_calendar(back=0, ahead=1)
    except Exception:  # noqa: BLE001
        return []
    now = dt.datetime.now(NY)
    today_ny = now.date()
    out = []
    for e in cal.get("events", []):
        if e.get("impact") not in ("High", "Medium"):
            continue
        ts = dt.datetime.fromtimestamp(e["ts"], NY)
        if ts.date() != today_ny:
            continue
        out.append({"category": "macro", "title": e["title"], "country": e.get("country"), "impact": e["impact"],
                    "time": ts.strftime("%H:%M"), "forecast": e.get("forecast"), "previous": e.get("previous"),
                    "typical_range": _range_str("macro_index") + " index-wide, " + _range_str("macro_sector") + " on rate-sensitive names",
                    "why": f"{e.get('country')} {e['title']} at {ts.strftime('%H:%M')} NY — {e['impact'].lower()} impact release"})
    out.sort(key=lambda x: x["time"])
    return out


# ------------------------------------------------------------------ combine
def scan(hours: float = 20.0, top_n: int = 8) -> dict[str, Any]:
    earn = earnings_catalysts(hours)
    news = news_catalysts(hours)
    macro = macro_catalysts()
    combined = list(earn)
    for cat in ("fda", "mna", "legal", "management"):
        for r in news.get(cat, []):
            combined.append({**r, "sub": None, "observed_move_pct": None, "mcap": None})
    # rank: already-observed moves first (earnings), then by category severity (fda/mna most extreme historically),
    # then by how many independent headlines corroborate it
    severity = {"earnings": 0, "fda": 1, "mna": 2, "legal": 3, "management": 4}
    combined.sort(key=lambda r: (r.get("observed_move_pct") is None, -abs(r.get("observed_move_pct") or 0),
                                 severity.get(r["category"], 9), -(r.get("headlines_n") or 0)))
    seen, top = set(), []
    for r in combined:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        top.append(r)
        if len(top) >= top_n:
            break
    return {"earnings": earn, "fda": news.get("fda", []), "mna": news.get("mna", []), "management": news.get("management", []),
            "legal": news.get("legal", []), "macro": macro, "top": top, "scanned_at": time.time()}
