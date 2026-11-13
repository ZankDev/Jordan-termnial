"""WHY? — "Every move has a cause. Clue. Rumour. Headline."

Given a symbol and a time window, gather everything that could explain the move and rank it:

  price      Yahoo chart bars around the window -> the move, its onset (when 25% of it was done) and its peak bar
  news       GDELT DOC 2.0 (keyless, global, minute-stamped), Google News RSS (date-bounded), Yahoo Finance RSS + search,
             the SPIDER's own headline memory (spider_news), Reddit (JSON, then RSS; often rate-limited)
  world      EVENTS MAP impact rows (quakes / storms / hazards with exposed assets) and chokepoint risk (BMAP)
  macro      the economic calendar (econ_events) inside the window
  related    the same window for SPY, QQQ, the sector ETF, crude, the dollar, 10y yield, VIX, gold

Scoring (0-100): 45% time proximity to the move onset, 35% relevance (symbol / company-name / keyword match), 20% source
credibility. Buckets: RUMOUR = social or "reportedly / sources say / in talks" language; CLUE = before the onset and not
a confirmed headline (the early weak signal); HEADLINE = credible news at or after the onset. A screen, not attribution."""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import email.utils
import math
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) NOSTRO-terminal/1.0"}
YCHART = "https://query1.finance.yahoo.com/v8/finance/chart/{s}"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
_POOL = cf.ThreadPoolExecutor(max_workers=40, thread_name_prefix="cause")
SOURCE_TIMEOUT = 8.0  # hard per-source budget: a slow feed is dropped from this answer, never waited on
HTTP_TIMEOUT = 7

MAJOR = ("reuters", "bloomberg", "wsj", "wall street journal", "financial times", "ft.com", "cnbc", "associated press", "apnews", "marketwatch",
         "barron", "yahoo", "nytimes", "new york times", "washington post", "economist", "the information", "axios", "fortune", "forbes", "seeking alpha",
         "investing.com", "business insider", "nikkei", "bbc", "guardian", "sec.gov", "federalreserve", "benzinga", "dow jones",
         "cbs", "nbc", "abc news", "cnn", "wired", "the verge", "techcrunch", "newsroom", "press release", "businesswire", "prnewswire", "globenewswire")
RUMOUR_WORDS = re.compile(r"\b(reportedly|sources say|people familiar|according to sources|in talks|considering|weighs?|explor(es|ing)|rumou?r|speculat|could|may be|said to|leak|unconfirmed|chatter)\b", re.I)
SECTOR_ETF = {"Technology": "XLK", "Energy": "XLE", "Financial Services": "XLF", "Healthcare": "XLV", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
              "Industrials": "XLI", "Basic Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}
RELATED = [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("CL=F", "WTI crude"), ("DX-Y.NYB", "US dollar (DXY)"), ("^TNX", "US 10y yield"), ("^VIX", "VIX"), ("GC=F", "Gold")]
# words that tie futures / FX / indices to their news vocabulary
KEYWORDS = {"CL=F": ["crude", "oil", "opec", "brent", "wti"], "BZ=F": ["brent", "crude", "oil", "opec"], "NG=F": ["natural gas", "lng", "henry hub"],
            "GC=F": ["gold", "bullion"], "SI=F": ["silver"], "HG=F": ["copper"], "ZW=F": ["wheat", "grain"], "ZC=F": ["corn"], "ZS=F": ["soybean", "soy"],
            "KC=F": ["coffee"], "CC=F": ["cocoa"], "^VIX": ["volatility", "vix", "selloff"], "^TNX": ["treasury", "yield", "bond"], "DX-Y.NYB": ["dollar", "dxy", "fed"],
            "BTC-USD": ["bitcoin", "crypto", "btc"], "ETH-USD": ["ethereum", "ether", "crypto"], "^GSPC": ["stocks", "s&p", "wall street"], "SPY": ["stocks", "s&p", "wall street"],
            "QQQ": ["nasdaq", "tech stocks"], "NQ=F": ["nasdaq", "tech stocks"], "ES=F": ["s&p", "stocks"], "JPY=X": ["yen", "boj"], "EURUSD=X": ["euro", "ecb"]}
STOP = {"inc", "corp", "corporation", "company", "co", "ltd", "plc", "holdings", "group", "the", "class", "a", "sa", "ag", "nv", "se", "limited", "futures", "index", "trust", "etf"}


def _get(url: str, params: dict[str, Any] | None = None, timeout: int = HTTP_TIMEOUT) -> requests.Response:
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


# ------------------------------------------------------------------ price
def _interval(span: float) -> str:
    return "5m" if span <= 2 * 86400 else "15m" if span <= 6 * 86400 else "1h" if span <= 45 * 86400 else "1d"


def bars(symbol: str, t0: float, t1: float) -> dict[str, Any]:
    span = max(3600.0, t1 - t0)
    iv = _interval(span)
    pad = max(span * 0.6, 6 * 3600 if iv != "1d" else 5 * 86400)
    p1, p2 = int(t0 - pad), int(min(time.time(), t1 + pad))
    if iv in ("5m", "15m") and time.time() - p1 > 59 * 86400:
        iv = "1h"
    if iv == "1h" and time.time() - p1 > 720 * 86400:
        iv = "1d"
    j = requests.get(YCHART.format(s=requests.utils.quote(symbol)), params={"period1": p1, "period2": p2, "interval": iv, "includePrePost": "false"},
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=HTTP_TIMEOUT).json()
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        raise ValueError(f"no price data for {symbol}")
    r = res[0]
    q, ts = r["indicators"]["quote"][0], r.get("timestamp") or []
    out = [[t, o, h, l, c] for t, o, h, l, c in zip(ts, q["open"], q["high"], q["low"], q["close"]) if None not in (o, h, l, c)]
    meta = r.get("meta") or {}
    return {"interval": iv, "bars": out, "name": meta.get("longName") or meta.get("shortName") or symbol, "currency": meta.get("currency"),
            "type": meta.get("instrumentType"), "exchange": meta.get("exchangeName")}


def move(b: list[list[float]], t0: float, t1: float) -> dict[str, Any] | None:
    inside = [x for x in b if t0 <= x[0] <= t1] or b
    prev = [x for x in b if x[0] < t0]
    if not inside:
        return None
    start = prev[-1][4] if prev else inside[0][1]
    end = inside[-1][4]
    tot = end - start
    onset, peak, cum = inside[0][0], inside[0][0], 0.0
    big = 0.0
    last = start
    for x in inside:
        d = x[4] - last
        if abs(d) > big:
            big, peak = abs(d), x[0]
        last = x[4]
    for x in inside:  # onset: first bar at which 25% of the total move is done
        cum = x[4] - start
        if tot and cum / tot >= 0.25:
            onset = x[0]
            break
    hi = max(x[2] for x in inside)
    lo = min(x[3] for x in inside)
    return {"start": start, "end": end, "pct": (end / start - 1) * 100 if start else 0, "onset": onset, "peak": peak, "hi": hi, "lo": lo,
            "range_pct": (hi / lo - 1) * 100 if lo else 0}


# ------------------------------------------------------------------ news sources
def _rss_items(content: bytes, source: str) -> list[dict[str, Any]]:
    out = []
    root = ET.fromstring(content)
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = lambda name: next((c for c in it if c.tag.split("}")[-1] == name), None)  # noqa: E731
        title = (get("title").text or "").strip() if get("title") is not None else ""
        link_el = get("link")
        link = (link_el.text or link_el.get("href") or "").strip() if link_el is not None else ""
        when = None
        for name in ("pubDate", "updated", "published"):
            el = get(name)
            if el is not None and el.text:
                try:
                    when = email.utils.parsedate_to_datetime(el.text).timestamp()
                except Exception:  # noqa: BLE001
                    try:
                        when = dt.datetime.fromisoformat(el.text.replace("Z", "+00:00")).timestamp()
                    except Exception:  # noqa: BLE001
                        pass
                break
        src_el = get("source")
        src = (src_el.text or "").strip() if src_el is not None and src_el.text else source
        if src and title.endswith(f" - {src}"):
            title = title[: -len(src) - 3]
        if title and when:
            out.append({"title": title, "link": link, "source": src, "ts": when})
    return out


def gdelt(query: str, t0: float, t1: float) -> list[dict[str, Any]]:
    f = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y%m%d%H%M%S")  # noqa: E731
    r = _get(GDELT, {"query": f"{query} sourcelang:english", "mode": "artlist", "format": "json", "maxrecords": 75, "sort": "datedesc",
                     "startdatetime": f(t0), "enddatetime": f(t1)}, timeout=HTTP_TIMEOUT)
    if not r.text.strip().startswith("{"):
        raise RuntimeError(r.text.strip()[:120] or "GDELT empty")
    out = []
    for a in r.json().get("articles") or []:
        try:
            ts = dt.datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
        except Exception:  # noqa: BLE001
            continue
        out.append({"title": a.get("title") or "", "link": a.get("url"), "source": a.get("domain") or "GDELT", "ts": ts, "via": "GDELT"})
    return out


def google_news(query: str, t0: float, t1: float) -> list[dict[str, Any]]:
    d0 = dt.datetime.fromtimestamp(t0 - 86400, dt.timezone.utc).strftime("%Y-%m-%d")
    d1 = dt.datetime.fromtimestamp(t1 + 86400, dt.timezone.utc).strftime("%Y-%m-%d")
    r = _get("https://news.google.com/rss/search", {"q": f"{query} after:{d0} before:{d1}", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return [{**x, "via": "Google News"} for x in _rss_items(r.content, "Google News")]


def yahoo_rss(symbol: str) -> list[dict[str, Any]]:
    r = _get("https://feeds.finance.yahoo.com/rss/2.0/headline", {"s": symbol, "region": "US", "lang": "en-US"})
    return [{**x, "source": "Yahoo Finance", "via": "Yahoo RSS"} for x in _rss_items(r.content, "Yahoo Finance")]


def yahoo_search(q: str) -> list[dict[str, Any]]:
    j = requests.get("https://query2.finance.yahoo.com/v1/finance/search", params={"q": q, "quotesCount": 0, "newsCount": 30},
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=HTTP_TIMEOUT).json()
    return [{"title": n.get("title"), "link": n.get("link"), "source": n.get("publisher") or "Yahoo", "ts": n.get("providerPublishTime"), "via": "Yahoo search"}
            for n in j.get("news") or [] if n.get("title") and n.get("providerPublishTime")]


def reddit(query: str, t0: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        j = _get("https://www.reddit.com/search.json", {"q": query, "sort": "new", "limit": 50, "t": "month"}, timeout=HTTP_TIMEOUT).json()
        for c in (j.get("data") or {}).get("children") or []:
            d = c.get("data") or {}
            out.append({"title": d.get("title") or "", "link": f"https://www.reddit.com{d.get('permalink', '')}", "source": f"r/{d.get('subreddit')}",
                        "ts": d.get("created_utc"), "via": "Reddit", "score": d.get("score")})
    except Exception:  # noqa: BLE001 - JSON is often 403 / 429 for servers: try the RSS search
        r = _get("https://www.reddit.com/search.rss", {"q": query, "sort": "new", "t": "month"}, timeout=HTTP_TIMEOUT)
        out = [{**x, "via": "Reddit"} for x in _rss_items(r.content, "Reddit")]
    return [x for x in out if x.get("ts") and x["ts"] >= t0 - 3 * 86400]


def spider(query: str, t0: float, t1: float) -> list[dict[str, Any]]:
    from app.intel import spider_memory as sm

    days = max(1.0, (time.time() - t0) / 86400 + 1)
    rows = sm.search_news(query, days=days, n=80)
    return [{"title": r["title"], "link": r["link"], "source": r.get("source") or "SPIDER", "ts": r["ts"], "via": "SPIDER memory", "level": r.get("level")}
            for r in rows if t0 - 3 * 86400 <= (r.get("ts") or 0) <= t1 + 86400]


def macro(t0: float, t1: float) -> list[dict[str, Any]]:
    from app import db
    from app.api.market_routes import _ECO_SCHEMA

    with db.get_conn() as conn:
        conn.execute(_ECO_SCHEMA)
        rows = [dict(r) for r in conn.execute("SELECT * FROM econ_events WHERE ts BETWEEN ? AND ? AND impact IN ('High','Medium') ORDER BY ts",
                                              (int(t0 - 6 * 3600), int(t1 + 3600))).fetchall()]
    return [{"title": f"{r['country']} {r['title']}" + (f" — actual {r['actual']} vs fc {r['forecast']}" if r.get("actual") else f" (fc {r.get('forecast') or '—'})"),
             "ts": r["ts"], "impact": r.get("impact"), "source": "Economic calendar", "link": None, "via": "Calendar"} for r in rows]


def world(symbol: str, t0: float, t1: float) -> list[dict[str, Any]]:
    """EVENTS MAP rows: impact events in the window, flagged when their exposed assets include the symbol."""
    from app.api import world_live_routes as wl

    d = wl.api_all("week")
    out = []
    for r in d["impact"]:
        if not (t0 - 3 * 86400 <= (r.get("t") or 0) <= t1 + 3600):
            continue
        syms = {a["sym"] for a in r["assets"]}
        out.append({"title": r["title"], "ts": r["t"], "source": "EVENTS MAP", "link": None, "via": "World", "kind": r["kind"], "exposed": symbol in syms,
                    "regions": r["regions"], "assets": sorted(syms)})
    return out


def related(symbol: str, sector: str | None, t0: float, t1: float) -> list[dict[str, Any]]:
    rel = list(RELATED)
    etf = SECTOR_ETF.get(sector or "")
    if etf:
        rel.insert(2, (etf, f"{sector} sector"))
    rel = [(s, n) for s, n in rel if s != symbol]

    def one(sn: tuple[str, str]) -> dict[str, Any] | None:
        s, n = sn
        try:
            b = bars(s, t0, t1)
            m = move(b["bars"], t0, t1)
            return {"sym": s, "name": n, "pct": round(m["pct"], 2)} if m else None
        except Exception:  # noqa: BLE001
            return None
    futs = [_POOL.submit(one, sn) for sn in rel]
    done, _ = cf.wait(futs, timeout=SOURCE_TIMEOUT - 1.5)  # inside the outer 8 s budget
    return [f.result() for f in futs if f in done and not f.exception() and f.result()]


def sector_of(symbol: str) -> str | None:
    try:
        from app.api.invest_routes import _yget

        j = _yget(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}", {"modules": "assetProfile"}, timeout=HTTP_TIMEOUT)
        return ((j.get("quoteSummary") or {}).get("result") or [{}])[0].get("assetProfile", {}).get("sector")
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ scoring
def _terms(symbol: str, name: str) -> tuple[list[str], list[str]]:
    base = symbol.split("=")[0].split(".")[0].replace("^", "").replace("-USD", "")
    strong = [base.lower()] if len(base) >= 2 and base.isalpha() else []
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&'.-]+", name or "") if w.lower().strip(".") not in STOP]
    if words:
        strong.append(" ".join(words[:2]).lower() if len(words) > 1 and len(words[0]) <= 3 else words[0].lower())
    weak = KEYWORDS.get(symbol, [])
    return list(dict.fromkeys(strong)), weak


def _cred(src: str, via: str) -> float:
    s = (src or "").lower()
    if via == "Reddit":
        return 0.35
    if via in ("Calendar", "World"):
        return 0.9
    return 1.0 if any(m in s for m in MAJOR) else 0.6


def score(items: list[dict[str, Any]], symbol: str, name: str, mv: dict[str, Any] | None, t0: float, t1: float) -> list[dict[str, Any]]:
    strong, weak = _terms(symbol, name)
    onset = mv["onset"] if mv else (t0 + t1) / 2
    tau = max(3600.0, (t1 - t0) / 3)
    seen: set[str] = set()
    out = []
    for it in items:
        title = (it.get("title") or "").strip()
        key = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
        if not title or key in seen or not it.get("ts"):
            continue
        seen.add(key)
        tl = title.lower()
        rel = 1.0 if any(re.search(rf"\b{re.escape(s)}\b", tl) for s in strong) else 0.55 if any(w in tl for w in weak) else 0.15
        if it.get("via") == "World":
            rel = 1.0 if it.get("exposed") else 0.3
        if it.get("via") == "Calendar":
            rel = 0.6 if symbol in ("SPY", "QQQ", "^GSPC", "ES=F", "NQ=F", "^TNX", "DX-Y.NYB", "GC=F", "EURUSD=X", "JPY=X") else 0.35
        dtm = it["ts"] - onset
        prox = math.exp(-abs(dtm) / tau) if dtm < 0 else math.exp(-abs(dtm) / (tau * 0.7))
        if it["ts"] > t1 + 6 * 3600:
            prox *= 0.4  # written well after the move: explanation, not cause
        cred = _cred(it.get("source", ""), it.get("via", ""))
        conf = round(100 * (0.45 * prox + 0.35 * rel + 0.2 * cred))
        if it.get("via") == "Reddit" or RUMOUR_WORDS.search(title):
            bucket = "rumour"
        elif dtm < -15 * 60 and (cred < 1.0 or it.get("via") in ("World", "Calendar", "GDELT")):
            bucket = "clue"
        elif dtm < -15 * 60:
            bucket = "clue" if rel < 1.0 else "headline"
        else:
            bucket = "headline"
        out.append({**{k: v for k, v in it.items() if k not in ("score",)}, "title": title, "bucket": bucket, "conf": conf, "rel": round(rel, 2), "prox": round(prox, 2),
                    "cred": cred, "dt_min": round(dtm / 60)})
    out.sort(key=lambda x: -x["conf"])
    return out


# ------------------------------------------------------------------ main entry
def explain(symbol: str, t0: float, t1: float) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    if t1 <= t0:
        t0, t1 = t1, t0
    t1 = min(t1, time.time())
    status: dict[str, Any] = {}
    px = bars(symbol, t0, t1)
    mv = move(px["bars"], t0, t1)
    name = px["name"]
    strong, weak = _terms(symbol, name)
    q_name = f'"{strong[1]}"' if len(strong) > 1 and " " in strong[1] else (strong[1] if len(strong) > 1 else symbol)
    gq = " OR ".join(dict.fromkeys([q_name] + ([f'"{w}"' for w in weak[:2]] if weak else [])))
    if len(gq.split(" OR ")) > 1:
        gq = f"({gq})"
    look0 = t0 - max(2 * 86400, (t1 - t0))  # clues can come days before
    jobs = {
        "gdelt": (gdelt, gq, max(look0, time.time() - 88 * 86400), t1 + 6 * 3600),
        "google": (google_news, q_name if not weak else f"{q_name} OR {weak[0]}", look0, t1),
        "yahoo_rss": (yahoo_rss, symbol),
        "yahoo": (yahoo_search, symbol if len(symbol) <= 5 else name),
        "reddit": (reddit, strong[0] if strong and len(strong[0]) > 2 else q_name.strip('"'), look0),
        "spider": (spider, (strong[1] if len(strong) > 1 else symbol), look0, t1),
        "macro": (macro, t0, t1),
        "world": (world, symbol, t0, t1),
        "related": (related, symbol, None, t0, t1),
    }
    if px.get("type") == "EQUITY":
        jobs["sector"] = (sector_of, symbol)
    fut = {k: _POOL.submit(*v) for k, v in jobs.items()}
    # every source runs in parallel; whatever is not back within SOURCE_TIMEOUT is reported as timed out and skipped
    cf.wait(list(fut.values()), timeout=SOURCE_TIMEOUT)
    items: list[dict[str, Any]] = []
    world_rows: list[dict[str, Any]] = []
    macro_rows: list[dict[str, Any]] = []
    sector, rel = None, []
    for k, f in fut.items():
        if not f.done():
            status[k] = {"ok": False, "err": f"timed out after {SOURCE_TIMEOUT:g}s"}
            continue
        if f.exception() is not None:
            e = f.exception()
            status[k] = {"ok": False, "err": f"{type(e).__name__}: {e}"[:140]}
            continue
        rows = f.result()
        if k == "sector":
            sector = rows
            continue
        if k == "related":
            rel = rows
            status[k] = {"ok": True, "n": len(rows)}
            continue
        status[k] = {"ok": True, "n": len(rows)}
        rows = [r for r in rows if look0 <= (r.get("ts") or 0) <= t1 + 2 * 86400]
        if k == "world":
            world_rows = rows
        elif k == "macro":
            macro_rows = rows
        items += rows
    etf = SECTOR_ETF.get(sector or "")
    if etf and not any(r["sym"] == etf for r in rel):  # sector ETF move, with the same hard budget
        f = _POOL.submit(lambda: (lambda b: move(b["bars"], t0, t1))(bars(etf, t0, t1)))
        try:
            m = f.result(timeout=SOURCE_TIMEOUT)
            if m:
                rel.insert(0, {"sym": etf, "name": f"{sector} sector", "pct": round(m["pct"], 2)})
        except Exception:  # noqa: BLE001
            pass
    ranked = score(items, symbol, name, mv, t0, t1)
    buckets = {b: [x for x in ranked if x["bucket"] == b][:12] for b in ("clue", "rumour", "headline")}
    return {"symbol": symbol, "name": name, "sector": sector, "from": t0, "to": t1, "interval": px["interval"], "bars": px["bars"], "move": mv,
            "buckets": buckets, "world": [x for x in ranked if x.get("via") == "World"][:8], "macro": [x for x in ranked if x.get("via") == "Calendar"][:8],
            "related": rel, "status": status, "n_items": len(ranked), "query": {"gdelt": gq, "terms": strong, "keywords": weak},
            "ai_available": bool(shutil.which("claude")), "_raw_world": len(world_rows), "_raw_macro": len(macro_rows)}


def summarize(d: dict[str, Any], timeout: int = 75) -> str:
    """Optional 3-5 line read of the evidence via the local `claude -p` CLI. Raises when it is not installed or fails."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not installed")
    mv = d.get("move") or {}
    lines = [f"{d['symbol']} ({d['name']}) moved {mv.get('pct', 0):+.2f}% between {dt.datetime.utcfromtimestamp(d['from']):%Y-%m-%d %H:%M} and "
             f"{dt.datetime.utcfromtimestamp(d['to']):%Y-%m-%d %H:%M} UTC; onset {dt.datetime.utcfromtimestamp(mv.get('onset') or d['from']):%Y-%m-%d %H:%M} UTC."]
    for b in ("clue", "rumour", "headline"):
        for x in d["buckets"][b][:6]:
            lines.append(f"[{b.upper()} {x['conf']}] {dt.datetime.utcfromtimestamp(x['ts']):%m-%d %H:%M} {x['source']}: {x['title']}")
    for x in d.get("related", []):
        lines.append(f"[RELATED] {x['name']} {x['pct']:+.2f}%")
    prompt = ("You are a markets analyst. From ONLY the evidence below, explain in 3-5 short lines the most likely cause(s) of the move, "
              "say which items are clues vs rumours vs confirmed headlines, and state plainly if the evidence is thin. No preamble.\n\n" + "\n".join(lines))
    r = subprocess.run([exe, "-p", prompt, "--output-format", "text"], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError((r.stderr or "claude CLI failed").strip()[:200])
    return r.stdout.strip()
