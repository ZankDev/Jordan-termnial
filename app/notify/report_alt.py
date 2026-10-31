"""Extra data sources for the /report PDF, all free and key-less, each bounded by short timeouts and returning None when the source is
down or has nothing for this company (so one bad source only drops its own section):

  FINRA bi-monthly short interest · CBOE delayed option chain (gamma exposure, skew, term structure) · ClinicalTrials.gov pipeline ·
  NHTSA vehicle recalls · SEC filing-language monitor (full-text search) · SEC XBRL rank against every filer · Wikipedia attention ·
  StockTwits sentiment · Hacker News · GDELT news tone · USAspending government contracts · IMF growth outlook.

Fetchers take (inv, sym, sec) or the company name; the section renderers further down take the report context X."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JordanTerminal research nostro@example.com"}
SEC_UA = {"User-Agent": "NOSTRO Terminal personal-research tool nostro@example.com", "Accept-Encoding": "gzip, deflate"}
CACHE = Path(__file__).resolve().parents[2] / "data_cache"
_SUFFIX = re.compile(r"[,.]?\s+(inc|corp|corporation|ltd|plc|co|company|holdings|group|sa|nv|ag|limited|incorporated|class [a-c])\.?$", re.I)


def clean_name(name: str | None) -> str:
    n = (name or "").strip()
    for _ in range(3):
        n = _SUFFIX.sub("", n).strip(" ,.")
    return n


# ------------------------------------------------------------------ FINRA short interest
def _si_candidates(n_months: int = 6) -> list[dt.date]:
    out: list[dt.date] = []
    t = dt.date.today()
    y, m = t.year, t.month
    for _ in range(n_months + 1):
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        for d in (dt.date(y, m, 15), last):
            while d.weekday() >= 5:
                d -= dt.timedelta(days=1)
            if d <= t - dt.timedelta(days=8):
                out.append(d)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(set(out))[-n_months * 2:]


def _si_file(d: dt.date) -> Path | None:
    """Settlement-date file, kept on disk once downloaded. FINRA settles on the 15th / last business day, moved back for holidays."""
    folder = CACHE / "finra_si"
    folder.mkdir(parents=True, exist_ok=True)
    for back in range(0, 3):
        dd = d - dt.timedelta(days=back)
        p = folder / f"{dd:%Y%m%d}.csv"
        if p.exists() and p.stat().st_size > 1000:
            return p
        try:
            r = requests.get(f"https://cdn.finra.org/equity/otcmarket/biweekly/shrt{dd:%Y%m%d}.csv", headers=UA, timeout=25)
            if r.status_code == 200 and len(r.content) > 1000:
                p.write_bytes(r.content)
                return p
        except Exception:  # noqa: BLE001
            return None
    return None


def short_interest(sym: str) -> list[dict[str, Any]] | None:
    """Last ~12 FINRA short-interest settlements for the ticker (position, change, average volume, days to cover)."""
    if "-" in sym and not sym.endswith(".B"):
        return None
    key = sym.upper().replace("-", ".")
    with ThreadPoolExecutor(max_workers=6) as ex:
        files = list(ex.map(_si_file, _si_candidates()))
    rows: list[dict[str, Any]] = []
    needle = f'"{key}"|'
    for p in files:
        if not p:
            continue
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    ln = line.replace('"', "")  # older files are unquoted, the newest are quoted
                    if f"|{key}|" in ln[:40]:
                        x = next(csv.reader([ln], delimiter="|"))
                        rows.append({"date": x[13], "short": float(x[5]), "prev": float(x[6]), "adv": float(x[8] or 0), "dtc": float(x[9] or 0), "chg": float(x[11] or 0)})
                        break
        except Exception:  # noqa: BLE001
            continue
    _ = needle
    rows = sorted({r["date"]: r for r in rows}.values(), key=lambda r: r["date"])
    return rows if len(rows) >= 2 else None


# ------------------------------------------------------------------ CBOE option chain
def cboe_options(sym: str, spot_hint: float | None = None) -> dict[str, Any] | None:
    """Full delayed chain from CBOE with greeks: expiry term structure, skew, expected move, and dealer gamma exposure by strike."""
    if "-" in sym:
        return None
    d = None
    for attempt in range(2):  # the CDN occasionally answers late or with a reset under load: one retry
        try:
            r = requests.get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym.upper()}.json", headers=UA, timeout=30)
            r.raise_for_status()
            d = r.json()["data"]
            break
        except Exception:  # noqa: BLE001
            time.sleep(1.5)
    if d is None:
        return None
    spot = float(d.get("current_price") or spot_hint or 0)
    if not spot:
        return None
    today = dt.date.today()
    opts = []
    for o in d.get("options") or []:
        m = re.match(r"^(.*?)(\d{6})([CP])(\d{8})$", o.get("option", ""))
        if not m:
            continue
        try:
            exp = dt.datetime.strptime(m.group(2), "%y%m%d").date()
        except ValueError:
            continue
        dte = (exp - today).days
        if dte < 0:
            continue
        opts.append({"exp": exp, "dte": dte, "cp": m.group(3), "k": int(m.group(4)) / 1000.0, "iv": float(o.get("iv") or 0), "oi": float(o.get("open_interest") or 0),
                     "vol": float(o.get("volume") or 0), "delta": float(o.get("delta") or 0), "gamma": float(o.get("gamma") or 0), "bid": float(o.get("bid") or 0), "ask": float(o.get("ask") or 0)})
    if not opts:
        return None
    exps = sorted({o["exp"] for o in opts})
    terms = []
    for e in exps[:10]:
        os_ = [o for o in opts if o["exp"] == e]
        calls, puts = [o for o in os_ if o["cp"] == "C"], [o for o in os_ if o["cp"] == "P"]
        near = sorted((o for o in os_ if o["iv"] > 0), key=lambda o: abs(o["k"] - spot))[:4]
        atm_iv = statistics.mean(o["iv"] for o in near) * 100 if near else None
        c25 = min((o for o in calls if o["iv"] > 0 and o["delta"] > 0), key=lambda o: abs(o["delta"] - 0.25), default=None)
        p25 = min((o for o in puts if o["iv"] > 0 and o["delta"] < 0), key=lambda o: abs(o["delta"] + 0.25), default=None)
        skew = (p25["iv"] - c25["iv"]) * 100 if c25 and p25 and abs(abs(c25["delta"]) - 0.25) < 0.12 and abs(abs(p25["delta"]) - 0.25) < 0.12 else None
        k0 = min({o["k"] for o in os_}, key=lambda k: abs(k - spot))
        sc = next((o for o in calls if o["k"] == k0), None)
        sp = next((o for o in puts if o["k"] == k0), None)
        move = ((sc["bid"] + sc["ask"]) / 2 + (sp["bid"] + sp["ask"]) / 2) / spot * 100 if sc and sp and sc["ask"] and sp["ask"] else None
        coi, poi = sum(o["oi"] for o in calls), sum(o["oi"] for o in puts)
        cv, pv = sum(o["vol"] for o in calls), sum(o["vol"] for o in puts)
        # max pain from open interest at this expiry
        ks = sorted({o["k"] for o in os_ if o["oi"] > 0})
        pain = min(ks, key=lambda S: sum(o["oi"] * (max(0.0, S - o["k"]) if o["cp"] == "C" else max(0.0, o["k"] - S)) for o in os_)) if ks else None
        terms.append({"exp": e.isoformat(), "dte": (e - today).days, "atm_iv": atm_iv, "skew": skew, "move": move, "call_oi": coi, "put_oi": poi, "pc_oi": poi / coi if coi else None,
                      "call_vol": cv, "put_vol": pv, "pc_vol": pv / cv if cv else None, "max_pain": pain})
    # dealer gamma exposure per strike: dollar gamma for a 1% move, calls positive, puts negative (the usual convention: dealers short the customers' options)
    gex: dict[float, float] = {}
    for o in opts:
        if o["dte"] > 60 or o["gamma"] <= 0 or o["oi"] <= 0 or not (0.75 * spot <= o["k"] <= 1.25 * spot):
            continue
        g = o["gamma"] * o["oi"] * 100 * spot * spot * 0.01 * (1 if o["cp"] == "C" else -1)
        gex[o["k"]] = gex.get(o["k"], 0.0) + g
    strikes = sorted(gex)
    flip = None
    run = 0.0
    prev = None
    for k in strikes:
        run += gex[k]
        if prev is not None and (run > 0) != (prev > 0):
            flip = k
        prev = run
    tot_c, tot_p = sum(o["oi"] for o in opts if o["cp"] == "C"), sum(o["oi"] for o in opts if o["cp"] == "P")
    vc, vp = sum(o["vol"] for o in opts if o["cp"] == "C"), sum(o["vol"] for o in opts if o["cp"] == "P")
    return {"spot": spot, "terms": terms, "gex": [(k, gex[k]) for k in strikes], "net_gex": sum(gex.values()), "flip": flip,
            "pc_oi": tot_p / tot_c if tot_c else None, "pc_vol": vp / vc if vc else None, "n": len(opts), "iv30": d.get("iv30"), "iv30_change": d.get("iv30_change")}


# ------------------------------------------------------------------ ClinicalTrials.gov
def clinical_trials(name: str) -> dict[str, Any] | None:
    n = clean_name(name)
    if len(n) < 3:
        return None
    fields = "NCTId,BriefTitle,OverallStatus,Phase,Condition,PrimaryCompletionDate,CompletionDate,EnrollmentCount,LeadSponsorName,LeadSponsorClass,StartDate"
    studies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("query.lead", "query.term", "query.spons"):  # sponsors are often registered under a subsidiary name (ModernaTX), so cast three nets
        try:
            r = requests.get("https://clinicaltrials.gov/api/v2/studies", params={key: n, "pageSize": 500, "format": "json", "fields": fields}, headers=UA, timeout=25)
            r.raise_for_status()
            for s_ in r.json().get("studies") or []:
                nid = ((s_.get("protocolSection") or {}).get("identificationModule") or {}).get("nctId")
                if nid and nid not in seen:
                    seen.add(nid)
                    studies.append(s_)
        except Exception:  # noqa: BLE001
            continue
    tok = n.split()[0].lower()
    rows = []
    for s in studies:
        p = s.get("protocolSection") or {}
        lead = ((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {})
        if tok not in (lead.get("name") or "").lower() or lead.get("class") != "INDUSTRY":
            continue
        st = p.get("statusModule") or {}
        rows.append({"id": (p.get("identificationModule") or {}).get("nctId"), "title": (p.get("identificationModule") or {}).get("briefTitle") or "", "status": st.get("overallStatus") or "",
                     "phase": "/".join(x.replace("PHASE", "P").replace("EARLY_P1", "EP1").replace("NA", "N/A") for x in ((p.get("designModule") or {}).get("phases") or [])) or "n/a",
                     "cond": ", ".join(((p.get("conditionsModule") or {}).get("conditions") or [])[:2]), "pcd": (st.get("primaryCompletionDateStruct") or {}).get("date"),
                     "n": ((p.get("designModule") or {}).get("enrollmentInfo") or {}).get("count"), "start": (st.get("startDateStruct") or {}).get("date")})
    if not rows:
        return None
    return {"studies": rows, "total": len(rows), "truncated": False}


# ------------------------------------------------------------------ NHTSA recalls
AUTO_MAP = {"TSLA": ("tesla", ["Model 3", "Model Y", "Model S", "Model X", "Cybertruck"]), "F": ("ford", ["F-150", "Mustang", "Explorer", "Bronco", "Escape", "Maverick", "Mustang Mach-E"]),
            "GM": ("chevrolet", ["Silverado 1500", "Equinox", "Traverse", "Tahoe", "Blazer", "Bolt EV"]), "RIVN": ("rivian", ["R1T", "R1S", "EDV"]), "LCID": ("lucid", ["Air", "Gravity"]),
            "TM": ("toyota", ["Camry", "RAV4", "Corolla", "Tacoma", "Highlander", "Tundra"]), "HMC": ("honda", ["Civic", "CR-V", "Accord", "Pilot"])}


def nhtsa_recalls(sym: str) -> dict[str, Any] | None:
    spec = AUTO_MAP.get(sym.upper())
    if not spec:
        return None
    make, models = spec
    year = dt.date.today().year
    jobs = [(m, y) for m in models for y in range(year - 3, year + 1)]

    def one(a: tuple[str, int]) -> list[dict[str, Any]]:
        try:
            r = requests.get("https://api.nhtsa.gov/recalls/recallsByVehicle", params={"make": make, "model": a[0], "modelYear": a[1]}, headers=UA, timeout=15)
            return [dict(x, _model=a[0]) for x in (r.json().get("results") or [])] if r.status_code == 200 else []
        except Exception:  # noqa: BLE001
            return []

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = [x for part in ex.map(one, jobs) for x in part]
    by: dict[str, dict[str, Any]] = {}
    for x in res:
        c = x.get("NHTSACampaignNumber")
        if not c:
            continue
        e = by.setdefault(c, {"id": c, "date": x.get("ReportReceivedDate"), "component": x.get("Component") or "", "summary": x.get("Summary") or "", "units": x.get("PotentialNumberofUnitsAffected") or 0,
                              "models": set(), "park": x.get("ParkIt"), "oversight": x.get("OverTheAirUpdate")})
        e["models"].add(x["_model"])
    out = []
    for e in by.values():
        try:
            d = dt.datetime.strptime(e["date"], "%d/%m/%Y").date()
        except Exception:  # noqa: BLE001
            continue
        out.append({**e, "d": d.isoformat(), "models": sorted(e["models"])})
    out.sort(key=lambda r: r["d"], reverse=True)
    return {"make": make, "recalls": out} if out else None


# ------------------------------------------------------------------ SEC filing-language monitor (full-text search)
LANG_TERMS = ["going concern", "material weakness", "supply chain", "tariff", "restructuring", "impairment", "litigation", "investigation", "cybersecurity", "artificial intelligence", "recall",
              "reduction in force", "China"]


def sec_language(cik: int) -> dict[str, Any] | None:
    today = dt.date.today()
    w1 = (today - dt.timedelta(days=365), today)
    w0 = (today - dt.timedelta(days=730), today - dt.timedelta(days=366))

    def count(a: tuple[str, tuple[dt.date, dt.date]]) -> int | None:
        term, (s, e) = a
        try:
            r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"q": f'"{term}"', "ciks": f"{cik:010d}", "forms": "10-K,10-Q,8-K", "dateRange": "custom", "startdt": s.isoformat(), "enddt": e.isoformat()},
                             headers=SEC_UA, timeout=10)
            time.sleep(0.12)
            return int(r.json()["hits"]["total"]["value"]) if r.status_code == 200 else None
        except Exception:  # noqa: BLE001
            return None

    jobs = [(t, w) for t in LANG_TERMS for w in (w1, w0)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(count, jobs))
    rows = []
    for i, t in enumerate(LANG_TERMS):
        a, b = res[2 * i], res[2 * i + 1]
        if a is not None and b is not None:
            rows.append({"term": t, "recent": a, "prior": b})
    return {"rows": rows} if any(r["recent"] or r["prior"] for r in rows) else None


# ------------------------------------------------------------------ SEC XBRL frames: rank against every filer
FRAME_TAGS = {"rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"], "ni": ["NetIncomeLoss"], "gp": ["GrossProfit"], "oi": ["OperatingIncomeLoss"],
              "assets": ["Assets"], "equity": ["StockholdersEquity"], "ocf": ["NetCashProvidedByUsedInOperatingActivities"], "debt": ["LongTermDebt", "LongTermDebtNoncurrent"]}


def _frame(tag: str, period: str) -> dict[int, float]:
    folder = CACHE / "sec_frames"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{tag}_{period}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 7 * 86400:
        return {int(k): v for k, v in json.loads(p.read_text()).items()}
    r = requests.get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/{period}.json", headers=SEC_UA, timeout=40)
    if r.status_code != 200:
        return {}
    data: dict[int, float] = {}
    for x in r.json().get("data") or []:
        data[int(x["cik"])] = float(x["val"])
    p.write_text(json.dumps(data))
    return data


def xbrl_rank(cik: int) -> dict[str, Any] | None:
    year = dt.date.today().year - 1
    for period in (f"CY{year}", f"CY{year - 1}"):
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {k: [ex.submit(_frame, t, period) for t in tags] for k, tags in FRAME_TAGS.items()}
            fr: dict[str, dict[int, float]] = {}
            for k, fl in futs.items():
                merged: dict[int, float] = {}
                for f in fl:
                    try:
                        for c, v in f.result().items():
                            merged.setdefault(c, v)
                    except Exception:  # noqa: BLE001
                        pass
                fr[k] = merged
        if cik in fr["rev"] and cik in fr["ni"] or cik in fr["assets"]:
            break
    else:
        return None
    rev, ni, gp, oi, assets, equity, ocf, debt = (fr[k] for k in ("rev", "ni", "gp", "oi", "assets", "equity", "ocf", "debt"))
    universe = [c for c, v in rev.items() if v >= 5e7]
    if cik not in rev or len(universe) < 200:
        return None
    me = rev[cik]
    band = [c for c in universe if 0.3 * me <= rev[c] <= 3 * me]
    metrics = [("Revenue (scale)", lambda c: rev.get(c), "usd", True), ("Net margin", lambda c: ni[c] / rev[c] if c in ni else None, "pct", True), ("Gross margin", lambda c: gp[c] / rev[c] if c in gp else None, "pct", True),
               ("Operating margin", lambda c: oi[c] / rev[c] if c in oi else None, "pct", True), ("Operating cash flow margin", lambda c: ocf[c] / rev[c] if c in ocf else None, "pct", True),
               ("Return on equity", lambda c: ni[c] / equity[c] if c in ni and equity.get(c, 0) > 0 else None, "pct", True), ("Return on assets", lambda c: ni[c] / assets[c] if c in ni and assets.get(c, 0) > 0 else None, "pct", True),
               ("Debt / equity", lambda c: debt[c] / equity[c] if c in debt and equity.get(c, 0) > 0 else None, "x", False), ("Revenue per $ of assets", lambda c: rev[c] / assets[c] if assets.get(c, 0) > 0 else None, "x", True)]
    out = []
    for name, f, kind, higher_better in metrics:
        v = f(cik)
        if v is None:
            continue
        pa = [x for x in (f(c) for c in universe) if x is not None]
        pb = [x for x in (f(c) for c in band) if x is not None]
        if len(pa) < 100:
            continue
        pct_all = sum(1 for x in pa if x < v) / len(pa) * 100
        pct_band = sum(1 for x in pb if x < v) / len(pb) * 100 if len(pb) >= 20 else None
        out.append({"metric": name, "value": v, "kind": kind, "pct_all": pct_all, "pct_band": pct_band, "n_all": len(pa), "n_band": len(pb), "higher_better": higher_better,
                    "median_all": statistics.median(pa), "median_band": statistics.median(pb) if len(pb) >= 20 else None})
    return {"period": period, "universe": len(universe), "band": len(band), "metrics": out} if out else None


# ------------------------------------------------------------------ attention and sentiment
def wiki_attention(name: str) -> dict[str, Any] | None:
    n = clean_name(name)
    try:
        s = requests.get("https://en.wikipedia.org/w/api.php", params={"action": "opensearch", "search": n, "limit": 1, "format": "json"}, headers=UA, timeout=10).json()
        title = (s[1] or [None])[0]
        if not title:
            return None
        end = dt.date.today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=119)
        art = requests.utils.quote(title.replace(" ", "_"), safe="")
        j = requests.get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{art}/daily/{start:%Y%m%d}/{end:%Y%m%d}", headers=UA, timeout=15).json()
        pts = [(it["timestamp"][:8], it["views"]) for it in j.get("items") or []]
    except Exception:  # noqa: BLE001
        return None
    if len(pts) < 30:
        return None
    v = [p[1] for p in pts]
    base = statistics.median(v[:-7]) or 1
    spikes = sorted(((p[1] / base, p[0]) for p in pts if p[1] > 2.5 * base), reverse=True)[:4]
    return {"title": title, "points": pts, "last7": statistics.mean(v[-7:]), "median": base, "ratio": statistics.mean(v[-7:]) / base, "spikes": [(d, round(r, 1)) for r, d in spikes]}


def stocktwits(sym: str) -> dict[str, Any] | None:
    try:
        r = requests.get(f"https://api.stocktwits.com/api/2/streams/symbol/{sym.upper().replace('-', '.')}.json", headers=UA, timeout=12)
        if r.status_code != 200:
            return None
        j = r.json()
    except Exception:  # noqa: BLE001
        return None
    msgs = j.get("messages") or []
    bull = sum(1 for m in msgs if ((m.get("entities") or {}).get("sentiment") or {}).get("basic") == "Bullish")
    bear = sum(1 for m in msgs if ((m.get("entities") or {}).get("sentiment") or {}).get("basic") == "Bearish")
    ts = []
    for m in msgs:
        try:
            ts.append(dt.datetime.strptime(m["created_at"], "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:  # noqa: BLE001
            pass
    span_h = (max(ts) - min(ts)).total_seconds() / 3600 if len(ts) > 1 else None
    return {"n": len(msgs), "bull": bull, "bear": bear, "watchers": (j.get("symbol") or {}).get("watchlist_count"), "per_hour": len(ts) / span_h if span_h else None,
            "title": (j.get("symbol") or {}).get("title")}


def hackernews(name: str) -> dict[str, Any] | None:
    n = clean_name(name)
    if len(n) < 4:
        return None
    since = int(time.time()) - 45 * 86400
    try:
        j = requests.get("https://hn.algolia.com/api/v1/search", params={"query": f'"{n}"', "tags": "story", "numericFilters": f"created_at_i>{since}", "hitsPerPage": 40}, headers=UA, timeout=12).json()
    except Exception:  # noqa: BLE001
        return None
    hits = [h for h in j.get("hits") or [] if n.split()[0].lower() in (h.get("title") or "").lower()]
    if len(hits) < 3:
        return None
    hits.sort(key=lambda h: -(h.get("points") or 0))
    return {"count": len(hits), "points": sum(h.get("points") or 0 for h in hits), "comments": sum(h.get("num_comments") or 0 for h in hits),
            "top": [{"title": h["title"], "points": h.get("points") or 0, "comments": h.get("num_comments") or 0, "date": (h.get("created_at") or "")[:10], "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"} for h in hits[:6]]}


def gdelt_tone(name: str) -> dict[str, Any] | None:
    n = clean_name(name)
    if len(n) < 3:
        return None

    def get(mode: str) -> list[tuple[str, float]] | None:
        try:
            r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params={"query": f'"{n}" sourcelang:english', "mode": mode, "format": "json", "timespan": "30d"}, headers=UA, timeout=14)
            if r.status_code != 200:  # 429 = rate limited: skip this run rather than wait
                return None
            tl = (r.json().get("timeline") or [{}])[0].get("data") or []
            return [(x["date"][:8], float(x["value"])) for x in tl]
        except Exception:  # noqa: BLE001
            return None

    tone = get("timelinetone")
    vol = None
    if not tone or len(tone) < 10:
        return None
    t = [x[1] for x in tone]
    return {"tone": tone, "vol": vol or [], "tone7": statistics.mean(t[-7:]), "tone30": statistics.mean(t), "worst": min(tone, key=lambda x: x[1]), "best": max(tone, key=lambda x: x[1])}


# ------------------------------------------------------------------ government contracts and macro outlook
def usaspending(name: str) -> dict[str, Any] | None:
    n = clean_name(name)
    if len(n) < 4:
        return None
    end = dt.date.today()
    f = {"recipient_search_text": [n], "award_type_codes": ["A", "B", "C", "D"], "time_period": [{"start_date": (end - dt.timedelta(days=365)).isoformat(), "end_date": end.isoformat()}]}
    try:
        r = requests.post("https://api.usaspending.gov/api/v2/search/spending_by_award/", json={"filters": f, "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Start Date", "End Date", "Description"],
                          "limit": 8, "sort": "Award Amount", "order": "desc"}, headers=UA, timeout=25)
        r.raise_for_status()
        res = r.json().get("results") or []
        cat = requests.post("https://api.usaspending.gov/api/v2/search/spending_by_category/awarding_agency/", json={"filters": f, "limit": 6}, headers=UA, timeout=25).json().get("results") or []
    except Exception:  # noqa: BLE001
        return None
    tok = n.split()[0].lower()
    res = [x for x in res if tok in (x.get("Recipient Name") or "").lower()]
    if not res:
        return None
    return {"awards": res, "agencies": [{"name": c.get("name"), "amount": c.get("amount")} for c in cat if c.get("amount")], "total_top": sum(x.get("Award Amount") or 0 for x in res)}


def imf_outlook() -> list[dict[str, Any]] | None:
    countries = [("USA", "United States"), ("CHN", "China"), ("DEU", "Germany"), ("JPN", "Japan"), ("GBR", "United Kingdom"), ("IND", "India"), ("ISR", "Israel")]
    out = []
    try:
        g = requests.get("https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH", headers=UA, timeout=20).json()["values"]["NGDP_RPCH"]
        i = requests.get("https://www.imf.org/external/datamapper/api/v1/PCPIPCH", headers=UA, timeout=20).json()["values"]["PCPIPCH"]
    except Exception:  # noqa: BLE001
        return None
    y = dt.date.today().year
    for code, label in countries:
        if code in g:
            out.append({"country": label, "growth": {str(k): g[code].get(str(k)) for k in (y - 1, y, y + 1, y + 2)}, "infl": {str(k): (i.get(code) or {}).get(str(k)) for k in (y - 1, y, y + 1, y + 2)}})
    return out or None


# ====================================================================== report sections
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Paragraph  # noqa: E402

from app.notify.report_sections import Ctx  # noqa: E402
from app.notify.report_ui import BODY, _big, _dtable, _kv, _p, _pct_plain, _price, _s, _style_ax, _usd  # noqa: E402


def _dparse(s: str) -> dt.date | None:
    for f in ("%Y-%m-%d", "%Y-%m", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, f).date()
        except Exception:  # noqa: BLE001
            continue
    return None


def short_int(X: Ctx) -> None:
    rows = X.D.get("finra_si")
    if not rows:
        return
    X.title("SHORT INTEREST HISTORY (FINRA)")
    last, first = rows[-1], rows[0]
    float_sh = None
    try:
        float_sh = float(((X.D.get("keystats") or {}).get("defaultKeyStatistics") or {}).get("floatShares", {}).get("raw") or 0) or None
    except Exception:  # noqa: BLE001
        float_sh = None
    kv = [("Latest settlement", last["date"]), ("Shares sold short", _big(last["short"])), ("Change vs previous report", f"{_p(last['chg'], 1)} ({_big(last['short'] - last['prev'])})"),
          ("Days to cover", f"{last['dtc']:.1f}"), ("Average daily volume", _big(last["adv"])),
          (f"Change since {first['date']}", _p((last["short"] / first["short"] - 1) * 100, 0) if first["short"] else "n/a")]
    if float_sh:
        kv.append(("Short as % of float", _pct_plain(last["short"] / float_sh, 100)))
    X.add(_kv(kv))
    X.sp(4)
    fig, ax = plt.subplots(figsize=(7.4, 2.6))
    ds = [_dparse(r["date"]) for r in rows]
    ax.bar(range(len(rows)), [r["short"] / 1e6 for r in rows], color="#d9830c", alpha=0.75)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([d.strftime("%d %b") if d else "" for d in ds], fontsize=6.5)
    ax.set_ylabel("shares short (millions)", fontsize=7)
    ax2 = ax.twinx()
    ax2.plot(range(len(rows)), [r["dtc"] for r in rows], color="#1d4ed8", lw=1.4, marker="o", ms=3)
    ax2.set_ylabel("days to cover", fontsize=7, color="#1d4ed8")
    ax.set_title(f"{X.sym}: FINRA short interest by settlement date (bars) and days to cover (line)", fontsize=8.5, loc="left")
    _style_ax(ax)
    X.fig(fig)
    X.note("FINRA publishes short interest twice a month for every exchange-listed stock, about 8 days after the settlement date. Shorts are the borrowed shares not yet bought back: a high or rising figure is bearish positioning, but a very high figure with a rising price is also how short squeezes start.")
    X.data("Short interest", ["Settlement", "Shares short", "Previous", "Change %", "Average daily volume", "Days to cover"], [[r["date"], r["short"], r["prev"], r["chg"], r["adv"], r["dtc"]] for r in rows])
    X.kpi("Short interest", _big(last["short"]))
    X.kpi("Days to cover", f"{last['dtc']:.1f}", "bad" if last["dtc"] > 7 else None)
    if last["dtc"] > 7:
        X.flag("bad", f"Days to cover {last['dtc']:.1f}: it would take over a week of average volume for shorts to exit", "Ownership")
    if len(rows) >= 4 and last["short"] > 1.4 * rows[-4]["short"]:
        X.flag("bad", f"Short interest up {(last['short'] / rows[-4]['short'] - 1) * 100:.0f}% over three reports", "Ownership")
    if len(rows) >= 4 and last["short"] < 0.7 * rows[-4]["short"]:
        X.flag("info", f"Shorts covering: short interest down {(1 - last['short'] / rows[-4]['short']) * 100:.0f}% over three reports", "Ownership")
    X.source("FINRA short interest", f"cdn.finra.org bi-monthly files, latest {last['date']}")


def options_cboe(X: Ctx) -> None:
    o = X.D.get("cboe")
    if not o:
        return
    X.title("OPTIONS DEEP DIVE: GAMMA, SKEW, EXPECTED MOVE (CBOE)")
    spot, terms = o["spot"], o["terms"]
    gx = o["net_gex"]
    kv = [("Spot", _price(spot)), ("Put / call open interest", f"{o['pc_oi']:.2f}" if o.get("pc_oi") else "n/a"), ("Put / call volume today", f"{o['pc_vol']:.2f}" if o.get("pc_vol") else "n/a"),
          ("Net dealer gamma (per 1% move)", _usd(gx)), ("Gamma flip level", _price(o["flip"]) if o.get("flip") else "none in range"), ("Contracts in chain", f"{o['n']:,}")]
    if o.get("iv30"):
        kv.append(("30-day implied volatility", f"{o['iv30']:.1f}%"))
    X.add(_kv(kv))
    X.sp(5)
    t = [["Expiry", "Days", "ATM IV", "Skew (25d put - call)", "Expected move", "Put/call OI", "Put/call volume", "Max pain"]]
    for e in terms[:8]:
        t.append([_s(e["exp"]), str(e["dte"]), f"{e['atm_iv']:.0f}%" if e.get("atm_iv") else "-", f"{e['skew']:+.1f} pts" if e.get("skew") is not None else "-", f"+/-{e['move']:.1f}%" if e.get("move") else "-",
                  f"{e['pc_oi']:.2f}" if e.get("pc_oi") else "-", f"{e['pc_vol']:.2f}" if e.get("pc_vol") else "-", _price(e["max_pain"]) if e.get("max_pain") else "-"])
    X.add(_dtable(t, [24 * mm, 12 * mm, 18 * mm, 32 * mm, 26 * mm, 22 * mm, 24 * mm, 20 * mm], esc=False))
    g = o["gex"]
    if len(g) >= 6:
        X.sp(5)
        fig, ax = plt.subplots(figsize=(7.4, 2.8))
        ks = [k for k, _v in g]
        vs = [v / 1e6 for _k, v in g]
        w = (max(ks) - min(ks)) / len(ks) * 0.8
        ax.bar(ks, vs, width=w, color=["#15803d" if v >= 0 else "#b91c1c" for v in vs])
        ax.axvline(spot, color="#1d4ed8", lw=1.2, ls="--", label=f"spot {_price(spot)}")
        if o.get("flip"):
            ax.axvline(o["flip"], color="#d9830c", lw=1.2, ls=":", label=f"gamma flip {_price(o['flip'])}")
        ax.set_ylabel("dealer gamma, $M per 1% move", fontsize=7)
        ax.set_title(f"{X.sym}: estimated dealer gamma exposure by strike (options expiring within 60 days)", fontsize=8.5, loc="left")
        ax.legend(fontsize=7, frameon=False)
        _style_ax(ax)
        X.fig(fig)
    X.note("CBOE delayed chain (about 15 minutes). Expected move is the cost of the at-the-money straddle as a share of the price. Skew is how much more the market pays for downside protection than for upside calls at the same distance. Gamma exposure assumes market makers are short the options customers bought (calls positive, puts negative): positive total gamma tends to dampen price moves, negative gamma amplifies them, and the flip level is where that changes. It is an estimate from open interest, not a reading of actual dealer books.")
    X.data("CBOE options by expiry", ["Expiry", "Days", "ATM IV %", "Skew pts", "Expected move %", "Put/call OI", "Put/call vol", "Max pain"],
           [[e["exp"], e["dte"], e["atm_iv"], e["skew"], e["move"], e["pc_oi"], e["pc_vol"], e["max_pain"]] for e in terms])
    X.data("Dealer gamma by strike", ["Strike", "Dollar gamma per 1% move"], [[k, v] for k, v in g])
    near = next((e for e in terms if e["dte"] >= 5 and e.get("move")), None)
    if near:
        X.kpi(f"Expected move ({near['dte']}d)", f"+/-{near['move']:.1f}%")
    X.kpi("Net gamma", _usd(gx), "bad" if gx < 0 else None)
    if gx < 0:
        X.flag("info", "Dealers are net short gamma: price moves are more likely to be amplified than dampened", "Risk")
    if near and near.get("skew") is not None and near["skew"] < -8:
        X.flag("bad", f"Steep put skew ({near['skew']:.0f} pts): the market is paying up for downside protection", "Sentiment")
    X.source("CBOE options chain", "cdn.cboe.com delayed quotes, live at build time")


def _bold(text: str) -> Paragraph:
    return Paragraph(f"<b>{_s(text)}</b>", BODY)


def trials(X: Ctx) -> None:
    d = X.D.get("trials")
    if not d:
        return
    st = d["studies"]
    X.title("CLINICAL TRIAL PIPELINE (CLINICALTRIALS.GOV)")
    today = dt.date.today()
    active = [s for s in st if s["status"] in ("RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING")]
    by_phase: dict[str, int] = {}
    for s in active:
        by_phase[s["phase"]] = by_phase.get(s["phase"], 0) + 1
    by_status: dict[str, int] = {}
    for s in st:
        k = s["status"].replace("_", " ").title()
        by_status[k] = by_status.get(k, 0) + 1
    kv = [("Industry-sponsored studies found", str(d["total"])), ("Active (recruiting or running)", str(len(active))),
          ("Active by phase", ", ".join(f"{k}: {v}" for k, v in sorted(by_phase.items(), key=lambda x: -x[1])[:5]) or "n/a"),
          ("All studies by status", ", ".join(f"{k} {v}" for k, v in sorted(by_status.items(), key=lambda x: -x[1])[:4]))]
    X.add(_kv(kv))
    X.sp(5)
    soon = sorted(((_dparse(s["pcd"] or ""), s) for s in active if s.get("pcd")), key=lambda x: x[0] or dt.date.max)
    soon = [(dd, s) for dd, s in soon if dd and today - dt.timedelta(days=30) <= dd <= today + dt.timedelta(days=548)][:10]
    if soon:
        X.add(_bold("Primary completion dates in the next 18 months (the data readouts that can move the stock)"))
        X.sp(2)
        t = [["Primary completion", "Trial", "Phase", "Condition", "Status", "Patients"]]
        for dd, s in soon:
            t.append([_s(dd.strftime("%b %Y")), _s(s["title"][:70]), _s(s["phase"]), _s(s["cond"][:34]), _s(s["status"].replace("_", " ").title()[:20]), f"{s['n']:,}" if s.get("n") else "-"])
        X.add(_dtable(t, [24 * mm, 66 * mm, 14 * mm, 32 * mm, 26 * mm, 14 * mm], right=5, esc=False))
    X.note("All industry-sponsored studies where the company (or a registered subsidiary) is the lead sponsor, from ClinicalTrials.gov. Primary completion is when the last patient is examined for the main endpoint; results usually follow within months. Dates are the sponsor's own estimates and often slip.")
    X.data("Clinical trials", ["NCT", "Title", "Status", "Phase", "Condition", "Primary completion", "Patients", "Start"],
           [[s["id"], s["title"], s["status"], s["phase"], s["cond"], s["pcd"], s["n"], s["start"]] for s in st])
    X.kpi("Active trials", str(len(active)))
    if soon:
        nxt = soon[0]
        X.flag("info", f"Next trial primary completion: {nxt[0].strftime('%b %Y')} ({nxt[1]['phase']}, {nxt[1]['cond'][:30]})", "Catalyst")
    X.source("ClinicalTrials.gov", "clinicaltrials.gov API v2, live at build time")


def recalls(X: Ctx) -> None:
    d = X.D.get("nhtsa")
    if not d:
        return
    rs = d["recalls"]
    X.title("VEHICLE RECALLS (NHTSA)")
    today = dt.date.today()
    r12 = [r for r in rs if (today - dt.date.fromisoformat(r["d"])).days <= 365]
    r24 = [r for r in rs if (today - dt.date.fromisoformat(r["d"])).days <= 730]
    comp: dict[str, int] = {}
    for r in r24:
        k = (r["component"] or "n/a").split(":")[0].title()
        comp[k] = comp.get(k, 0) + 1
    X.add(_kv([("Campaigns, last 12 months", str(len(r12))), ("Campaigns, last 24 months", str(len(r24))), ("Campaigns found (4 model years)", str(len(rs))),
               ("Most common components", ", ".join(f"{k} ({v})" for k, v in sorted(comp.items(), key=lambda x: -x[1])[:4]) or "n/a")]))
    X.sp(5)
    t = [["Date", "Campaign", "Models", "Component", "Summary"]]
    for r in rs[:8]:
        t.append([_s(r["d"]), _s(r["id"]), _s(", ".join(r["models"])[:26]), _s(r["component"][:30]), _s(r["summary"][:92])])
    X.add(_dtable(t, [20 * mm, 22 * mm, 30 * mm, 32 * mm, 70 * mm], right=99, esc=False))
    X.note(f"US National Highway Traffic Safety Administration recall campaigns for {d['make'].title()} vehicles, searched by model and model year (the last four model years). A campaign can cover several models. Many are fixed with a software update and cost little; the count and the components show where quality problems cluster.")
    X.data("NHTSA recalls", ["Date", "Campaign", "Models", "Component", "Summary"], [[r["d"], r["id"], ", ".join(r["models"]), r["component"], r["summary"]] for r in rs])
    X.kpi("Recalls (12m)", str(len(r12)), "bad" if len(r12) >= 12 else None)
    if len(r12) >= 12:
        X.flag("bad", f"{len(r12)} NHTSA recall campaigns in the last 12 months", "Quality")
    X.source("NHTSA recalls", "api.nhtsa.gov recallsByVehicle")


def language(X: Ctx) -> None:
    d = X.D.get("sec_lang")
    if not d:
        return
    rows = d["rows"]
    X.title("FILING LANGUAGE MONITOR (SEC FULL-TEXT SEARCH)")
    t = [["Phrase", "Filings, last 12 months", "Filings, prior 12 months", "Change"]]
    for r in sorted(rows, key=lambda r: -(r["recent"] - r["prior"])):
        ch = r["recent"] - r["prior"]
        t.append([_s(r["term"]), str(r["recent"]), str(r["prior"]), f"<font color='{'#b91c1c' if ch > 0 else '#15803d' if ch < 0 else '#6b7285'}'><b>{ch:+d}</b></font>"])
    X.add(_dtable(t, [60 * mm, 38 * mm, 38 * mm, 24 * mm], esc=False))
    X.note("How many of the company's 10-K, 10-Q and 8-K filings contain each phrase, last twelve months against the twelve before. A phrase appearing for the first time, or doubling, is a cheap early signal of what management is worried about. Counts are filings, not mentions, and a phrase can appear in boilerplate risk factors.")
    X.data("Filing language", ["Phrase", "Last 12 months", "Prior 12 months"], [[r["term"], r["recent"], r["prior"]] for r in rows])
    by = {r["term"]: r for r in rows}
    gc = by.get("going concern")
    if gc and gc["recent"] > 0 and gc["prior"] == 0:
        X.flag("bad", "'Going concern' appears in recent filings (it did not in the prior year)", "Balance sheet")
    mw = by.get("material weakness")
    if mw and mw["recent"] > 0 and mw["prior"] == 0:
        X.flag("bad", "'Material weakness' appears in recent filings (new this year)", "Quality")
    for term in ("restructuring", "reduction in force", "investigation"):
        r = by.get(term)
        if r and r["recent"] >= 3 and r["recent"] >= 2 * max(1, r["prior"]):
            X.flag("info", f"'{term}' mentioned in {r['recent']} filings (was {r['prior']})", "Filings")
    X.source("SEC full-text search", "efts.sec.gov, 10-K, 10-Q and 8-K filings, live at build time")


def rank(X: Ctx) -> None:
    d = X.D.get("xrank")
    if not d:
        return
    X.title(f"RANK AGAINST EVERY SEC FILER (FY{d['period'][2:]})")
    t = [["Metric", "This company", "Median, all filers", "Percentile, all", "Median, similar size", "Percentile, similar size"]]
    for m in d["metrics"]:
        f = (lambda v: _usd(v)) if m["kind"] == "usd" else (lambda v: _pct_plain(v, 100)) if m["kind"] == "pct" else (lambda v: f"{v:.2f}x")
        good = m["pct_all"] >= 50 if m["higher_better"] else m["pct_all"] <= 50
        col = "#15803d" if good else "#b91c1c"
        pb = f"{m['pct_band']:.0f}" if m.get("pct_band") is not None else "-"
        t.append([_s(m["metric"]), f"<b>{f(m['value'])}</b>", f(m["median_all"]), f"<font color='{col}'><b>{m['pct_all']:.0f}</b></font>", f(m["median_band"]) if m.get("median_band") is not None else "-", pb])
    X.add(_dtable(t, [48 * mm, 28 * mm, 28 * mm, 22 * mm, 28 * mm, 22 * mm], esc=False))
    X.note(f"Compared with {d['universe']:,} US-listed companies that filed annual XBRL data with at least $50M of revenue, and {d['band']:,} of similar size (0.3x to 3x this company's revenue). Percentile is the share of companies this one beats on that measure (for debt/equity a LOWER percentile is the better position). Based on fiscal-year data tagged in SEC filings, so a company whose year ends mid-calendar is matched to the nearest calendar year.")
    X.data("SEC filer rank", ["Metric", "Value", "Median all", "Percentile all", "Median similar size", "Percentile similar size"],
           [[m["metric"], m["value"], m["median_all"], m["pct_all"], m["median_band"], m["pct_band"]] for m in d["metrics"]])
    by = {m["metric"]: m for m in d["metrics"]}
    nm = by.get("Net margin")
    if nm:
        X.kpi("Net margin rank", f"top {100 - nm['pct_all']:.0f}%" if nm["pct_all"] >= 50 else f"bottom {nm['pct_all']:.0f}%", None if nm["pct_all"] >= 50 else "bad")
    ro = by.get("Return on equity")
    if ro and ro["pct_all"] >= 85:
        X.flag("good", f"Return on equity beats {ro['pct_all']:.0f}% of all SEC filers", "Quality")
    if nm and nm["pct_all"] <= 15:
        X.flag("bad", f"Net margin is worse than {100 - nm['pct_all']:.0f}% of all SEC filers", "Quality")
    X.source("SEC XBRL frames", "data.sec.gov frames API, all filers, fiscal " + d["period"])


def attention(X: Ctx) -> None:
    w, st, hn, gd = (X.D.get(k) for k in ("wiki", "stwits", "hn", "gdelt"))
    if not any((w, st, hn, gd)):
        return
    X.title("PUBLIC ATTENTION AND SENTIMENT")
    kv: list[tuple[str, str]] = []
    if w:
        kv += [("Wikipedia page views, last 7 days (daily avg)", f"{w['last7']:,.0f}"), ("Versus the 4-month median", f"{w['ratio']:.2f}x")]
    if st:
        tot = st["bull"] + st["bear"]
        kv += [("StockTwits watchers", f"{st['watchers']:,}" if st.get("watchers") else "n/a"), ("Latest 30 posts that took a side", f"{st['bull']} bullish / {st['bear']} bearish" if tot else "none tagged"),
               ("Posting pace", f"{st['per_hour']:.1f} posts / hour" if st.get("per_hour") else "n/a")]
    if hn:
        kv += [("Hacker News stories, last 45 days", f"{hn['count']} ({hn['points']:,} points, {hn['comments']:,} comments)")]
    if gd:
        wd = _dparse(gd["worst"][0])
        kv += [("News tone, last 7 days / 30 days (GDELT)", f"{gd['tone7']:+.2f} / {gd['tone30']:+.2f}"), ("Most negative day", f"{wd.strftime('%d %b') if wd else gd['worst'][0]} ({gd['worst'][1]:+.1f})")]
    X.add(_kv(kv))
    if w:
        X.sp(5)
        fig, ax = plt.subplots(figsize=(7.4, 2.3))
        ds = [dt.datetime.strptime(p[0], "%Y%m%d") for p in w["points"]]
        ax.fill_between(ds, [p[1] for p in w["points"]], color="#d9830c", alpha=0.35)
        ax.plot(ds, [p[1] for p in w["points"]], color="#b45309", lw=1.2)
        ax.axhline(w["median"], color="#6b7285", lw=0.8, ls="--")
        ax.set_title(f"Wikipedia page views: {w['title']} (daily, dashed = median)", fontsize=8.5, loc="left")
        _style_ax(ax)
        X.fig(fig)
    if hn:
        X.sp(4)
        t = [["Date", "Hacker News story", "Points", "Comments"]] + [[_s(h["date"]), _s(h["title"][:92]), str(h["points"]), str(h["comments"])] for h in hn["top"]]
        X.add(_dtable(t, [20 * mm, 112 * mm, 18 * mm, 22 * mm], right=2, esc=False))
    X.note("Attention is a crowd signal, not fundamentals: Wikipedia views and StockTwits chatter jump around news and big price moves, and a spike usually means the story is already known. Treat extremes as a reason to look at the news, not as a trade. StockTwits counts only posts where the author tagged a side. GDELT tone is the average emotional tone of English-language news about the company (negative = bad news flow).")
    if w:
        X.data("Wikipedia views", ["Date", "Views"], [[p[0], p[1]] for p in w["points"]])
        X.kpi("Attention vs normal", f"{w['ratio']:.1f}x", "bad" if w["ratio"] > 2 else None)
        if w["ratio"] > 2:
            X.flag("info", f"Wikipedia views are {w['ratio']:.1f}x their normal level this week", "Sentiment")
    if st and st["bull"] + st["bear"] >= 8:
        X.flag("info", f"StockTwits: {st['bull']} bullish vs {st['bear']} bearish among tagged posts", "Sentiment")
    if gd and gd["tone7"] < gd["tone30"] - 1.5:
        X.flag("bad", f"News tone worsened: {gd['tone7']:+.1f} this week vs {gd['tone30']:+.1f} over 30 days", "Sentiment")
    X.source("Attention and sentiment", "Wikimedia pageviews, StockTwits, Hacker News (Algolia), GDELT")


def contracts(X: Ctx) -> None:
    d = X.D.get("usasp")
    if not d:
        return
    X.title("US GOVERNMENT CONTRACTS (USASPENDING)")
    t = [["Awarding agency", "Recipient", "Award", "Period", "Description"]]
    for a in d["awards"]:
        t.append([_s((a.get("Awarding Agency") or "")[:30]), _s((a.get("Recipient Name") or "")[:28]), _usd(a.get("Award Amount")), _s(f"{(a.get('Start Date') or '')[:7]} to {(a.get('End Date') or '')[:7]}"), _s((a.get("Description") or "")[:60])])
    X.add(_dtable(t, [34 * mm, 34 * mm, 22 * mm, 28 * mm, 56 * mm], right=2, esc=False))
    if d.get("agencies"):
        X.sp(4)
        X.add(_kv([(a["name"][:42], _usd(a["amount"])) for a in d["agencies"][:6]]))
    X.note("Federal contract awards with start dates in the last twelve months, matched on the recipient's name in USAspending.gov. Amounts are the full ceiling of each award, which can run for years and is not revenue booked in the period. Name matching is by text, so check the recipient column.")
    X.data("US government awards", ["Agency", "Recipient", "Amount", "Start", "End", "Description"], [[a.get("Awarding Agency"), a.get("Recipient Name"), a.get("Award Amount"), a.get("Start Date"), a.get("End Date"), a.get("Description")] for a in d["awards"]])
    X.kpi("Largest federal award", _usd(max(a.get("Award Amount") or 0 for a in d["awards"])))
    X.source("USAspending", "api.usaspending.gov, contract awards last 12 months")


def imf(X: Ctx) -> None:
    rows = X.D.get("imf")
    if not rows:
        return
    X.title("IMF ECONOMIC OUTLOOK")
    years = list(rows[0]["growth"])
    t = [["Country"] + [f"GDP growth {y}" for y in years] + [f"Inflation {years[1]}", f"Inflation {years[2]}"]]
    for r in rows:
        t.append([_s(r["country"])] + [(_p(r["growth"][y], 1) if r["growth"].get(y) is not None else "-") for y in years] + [(_p(r["infl"].get(years[1]), 1) if r["infl"].get(years[1]) is not None else "-"), (_p(r["infl"].get(years[2]), 1) if r["infl"].get(years[2]) is not None else "-")])
    X.add(_dtable(t, [32 * mm] + [21 * mm] * 6, esc=False, bold_first=True))
    X.note("IMF World Economic Outlook database (DataMapper API). Years from the current one onward are IMF forecasts and get revised twice a year. Market-wide context, not specific to this company.")
    X.data("IMF outlook", ["Country"] + [f"Growth {y}" for y in years] + [f"Inflation {y}" for y in years], [[r["country"]] + [r["growth"][y] for y in years] + [r["infl"].get(y) for y in years] for r in rows])
    X.source("IMF DataMapper", "imf.org datamapper API, WEO database")
