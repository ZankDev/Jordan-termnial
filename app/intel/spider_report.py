"""SPIDER stock report: everything public about one stock, read together, with the spider's view.

Gathered in parallel (all from the terminal's own data functions, so it matches the SECURITY page):
  quote + profile + ratios (Yahoo) · 2 years of daily prices -> trend, momentum, RSI, volatility, drawdown, levels
  audited annual financials (SEC XBRL) · analysts (ratings, targets, estimates, surprises, changes) · holders + insiders
  the rules-based WHY verdict · SEC filings list
Read in full:
  the latest earnings press release (8-K item 2.02, exhibit 99) · the newest readable news articles about it ·
  what the spider already read about it (memory) · every headline it stored that mentions it
Then the brain writes the report: stance + conviction, thesis, bull and bear case, fundamentals, valuation, balance
sheet, the latest results, technicals with levels, news flow, analysts, ownership, catalysts, risks, scenarios, what
would change its mind, and a 0-10 scorecard. Saved in spider_reports. A research aid, not personal advice.

Reports take ~30-90 s, so they run in a background thread; `report()` returns the stored one or the job's progress.
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
import math
import re
import threading
import time
from typing import Any

from app.intel import llm

logger = logging.getLogger(__name__)
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
MAX_AGE = 6 * 3600


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _technicals(candles: list[list[Any]]) -> dict[str, Any]:
    c = [x[4] for x in candles if x[4] is not None]
    h = [x[2] for x in candles if x[2] is not None]
    lo = [x[3] for x in candles if x[3] is not None]
    if len(c) < 30:
        return {}
    sma = lambda n: sum(c[-n:]) / n if len(c) >= n else None  # noqa: E731
    gains = [max(0.0, c[i] - c[i - 1]) for i in range(len(c) - 14, len(c))]
    losses = [max(0.0, c[i - 1] - c[i]) for i in range(len(c) - 14, len(c))]
    ag, al = sum(gains) / 14, sum(losses) / 14
    rsi = 100 - 100 / (1 + ag / al) if al else 100.0
    rets = [c[i] / c[i - 1] - 1 for i in range(max(1, len(c) - 252), len(c))]
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 * (252 ** 0.5) * 100
    trs = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(len(c) - 14, len(c))] if len(h) == len(c) and len(lo) == len(c) else []
    year = c[-252:]
    peak, dd = year[0], 0.0
    for x in year:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    hi, low = max(year), min(year)
    s20, s50, s200 = sma(20), sma(50), sma(200)
    trend = ("uptrend" if s50 and s200 and c[-1] > s50 > s200 else "downtrend" if s50 and s200 and c[-1] < s50 < s200 else "mixed / range")
    # swing levels: recent 60-day pivots
    win = c[-60:]
    supports = sorted({round(min(win[i - 3:i + 4]), 2) for i in range(3, len(win) - 3) if win[i] == min(win[i - 3:i + 4])})[-3:]
    resist = sorted({round(max(win[i - 3:i + 4]), 2) for i in range(3, len(win) - 3) if win[i] == max(win[i - 3:i + 4])})[:3]
    return {"last": c[-1], "sma20": s20, "sma50": s50, "sma200": s200, "rsi14": round(rsi, 1), "vol_1y_pct": round(vol, 1),
            "atr14_pct": round(sum(trs) / len(trs) / c[-1] * 100, 2) if trs else None, "max_drawdown_1y_pct": round(dd * 100, 1),
            "from_52w_high_pct": round((c[-1] / hi - 1) * 100, 1), "from_52w_low_pct": round((c[-1] / low - 1) * 100, 1), "trend": trend,
            "ret": {k: round((c[-1] / c[-1 - n] - 1) * 100, 1) for k, n in (("1m", 21), ("3m", 63), ("6m", 126), ("1y", 251), ("2y", len(c) - 1)) if len(c) > n},
            "supports": supports, "resistances": resist}


def _latest_results(symbol: str) -> dict[str, Any] | None:
    """The newest 8-K with item 2.02 (results of operations): its EX-99 press release, read in full."""
    from app.api import invest_routes as inv
    from app.intel import article
    ck = inv._cik(symbol)  # noqa: SLF001
    if not ck:
        return None
    cik = ck[0]
    j = inv.cached(f"subm:{cik}", 900, lambda: inv._sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))  # noqa: SLF001
    rec = j["filings"]["recent"]
    for i, form in enumerate(rec["form"]):
        if form in ("8-K", "6-K") and ("2.02" in (rec.get("items") or [""] * len(rec["form"]))[i] or form == "6-K"):
            acc = rec["accessionNumber"][i]
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"
            a = article.fetch(url, {"title": f"{form} results · {symbol}"})
            if a["paragraphs"]:
                return {"url": url, "filed": rec["filingDate"][i], "form": form, "text": "\n".join(p["t"] for p in a["paragraphs"])[:9000]}
            break
    return None


def _gather(symbol: str, step) -> dict[str, Any]:  # noqa: ANN001
    from app.api import invest_routes as inv
    from app.intel import article, spider_memory as mem
    jobs = {
        "sec": lambda: inv.api_security(symbol),
        "hist": lambda: inv._history(symbol, "2y", "1d"),  # noqa: SLF001
        "verdict": lambda: inv.api_verdict(symbol),
        "analysts": lambda: inv.api_analysts(symbol),
        "fin": lambda: inv.api_financials(symbol),
        "filings": lambda: inv.api_filings(symbol),
        "holders": lambda: inv.api_holders(symbol),
        "news": lambda: inv.api_news(symbol=symbol)["items"],
        "results": lambda: _latest_results(symbol),
    }
    step("collecting price, financials, analysts, filings and news")
    out: dict[str, Any] = {}
    with cf.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {ex.submit(fn): k for k, fn in jobs.items()}
        for f in cf.as_completed(futs):
            try:
                out[futs[f]] = f.result()
            except Exception as e:  # noqa: BLE001 - a missing piece leaves a gap, not a failed report
                out[futs[f]] = None
                logger.info("spider report %s: %s unavailable (%s)", symbol, futs[f], type(e).__name__)
    if not out.get("sec"):
        raise LookupError(f"no market data for {symbol}")
    step("reading the newest articles in full")
    arts = []
    for it in (out.get("news") or [])[:10]:
        if len(arts) >= 3:
            break
        try:
            a = article.fetch(it["link"], it)
        except Exception:  # noqa: BLE001
            continue
        if a["words"] > 150 and not a.get("partial"):
            arts.append({"title": a.get("title") or it["title"], "source": it.get("source"), "ts": it.get("ts"), "url": it["link"],
                         "text": " ".join(p["t"] for p in a["paragraphs"])[:2200]})
    out["articles"] = arts
    name = out["sec"].get("name") or symbol
    try:
        out["memory"] = mem.recall(f"{symbol} {name.split()[0]}", days=30, n=8)
        out["wire"] = mem.search_news(symbol, days=30, n=20) + (mem.search_news(name.split()[0], days=30, n=20) if len(name.split()[0]) > 3 else [])
    except Exception:  # noqa: BLE001
        out["memory"], out["wire"] = [], []
    out["tech"] = _technicals((out.get("hist") or {}).get("candles") or [])
    return out


def _fmt_money(x: Any) -> str:
    x = _num(x)
    if x is None:
        return "n/a"
    a = abs(x)
    return f"{x / 1e12:.2f}T" if a >= 1e12 else f"{x / 1e9:.2f}B" if a >= 1e9 else f"{x / 1e6:.1f}M" if a >= 1e6 else f"{x:,.2f}"


def _brief(d: dict[str, Any], symbol: str, budget: int = 17000) -> str:
    """Everything gathered, as compact text for the model, cut to `budget` characters (a free Groq tier allows 8,000 tokens
    per request, prompt + answer; ~3.9 characters per token)."""
    s, st, tg = d["sec"], d["sec"].get("stats") or {}, d["sec"].get("target") or {}
    L = [f"STOCK: {symbol} · {s.get('name')} · {s.get('exchange')} · {(s.get('profile') or {}).get('sector')} / {(s.get('profile') or {}).get('industry')}",
         f"PRICE {s.get('price')} {s.get('currency')} ({_num(s.get('pct')) and round(s['pct'], 2)}% today) · mkt cap {_fmt_money(s.get('market_cap'))} · 52w {s.get('week52')}",
         f"PERFORMANCE %: {s.get('perf')}",
         "RATIOS: " + ", ".join(f"{k}={round(v, 4) if isinstance(v, float) else v}" for k, v in st.items() if _num(v) is not None),
         f"ANALYST TARGET: mean {tg.get('mean')} (low {tg.get('low')}, high {tg.get('high')}), n={tg.get('n')}, rating {tg.get('rating')} ({tg.get('score')}/5, 1=strong buy)",
         f"NEXT EARNINGS: {s.get('next_earnings')}",
         f"BUSINESS: {(s.get('description') or '')[:900]}"]
    t = d.get("tech") or {}
    if t:
        L.append(f"TECHNICALS: {t}")
    fin = d.get("fin") or {}
    if fin.get("available"):
        L.append(f"AUDITED ANNUAL FINANCIALS (SEC, periods {fin['periods'][:4]}):")
        for r in fin["rows"]:
            L.append(f"  {r['label']}: " + ", ".join(_fmt_money(v) if r["unit"] == "USD" else str(v) for v in r["values"][:4]))
    an = d.get("analysts") or {}
    if an:
        L.append(f"ANALYST RECOMMENDATION TREND: {an.get('recommendation', [])[:2]}")
        L.append(f"EPS SURPRISES: {an.get('surprises', [])[-4:]}")
        L.append(f"ESTIMATES: {[{k: e.get(k) for k in ('period', 'eps_avg', 'rev_avg', 'growth')} for e in an.get('estimates', [])[:4]]}")
        L.append(f"RECENT RATING CHANGES: {[(c.get('firm'), c.get('action'), c.get('from'), c.get('to')) for c in an.get('changes', [])[:8]]}")
    v = d.get("verdict") or {}
    if v:
        L.append(f"RULES VERDICT: {v.get('stance')} (score {v.get('score')}) — for: {[r.get('text') for r in v.get('why_buy', [])[:5]]}; against: {[r.get('text') for r in v.get('why_not', [])[:5]]}")
    h = d.get("holders") or {}
    if h:
        L.append(f"OWNERSHIP: {h.get('breakdown')}; insider net activity: {h.get('net_activity')}")
        L.append(f"INSIDER TRANSACTIONS: {[(x.get('name'), x.get('text'), x.get('value'), x.get('date')) for x in h.get('transactions', [])[:8]]}")
    fl = d.get("filings") or {}
    if fl.get("available"):
        L.append(f"RECENT SEC FILINGS: {[(x['form'], x['filed'], x.get('desc')) for x in fl['items'][:12]]}")
    r = d.get("results")
    if r:
        L.append(f"LATEST RESULTS PRESS RELEASE ({r['form']} filed {r['filed']}), TEXT:\n{r['text'][:int(budget * 0.32)]}")
    if d.get("articles"):
        L.append("NEWS ARTICLES READ IN FULL:")
        for a in d["articles"]:
            L.append(f"  [{a['source']} · {time.strftime('%d %b', time.localtime(a['ts'] or time.time()))}] {a['title']}\n  {a['text'][:int(budget * 0.08)]}")
    news = d.get("news") or []
    if news:
        L.append("LATEST HEADLINES: " + " | ".join(f"{time.strftime('%d %b', time.localtime(n.get('ts') or time.time()))}: {n['title']}" for n in news[:20]))
    if d.get("memory"):
        L.append("SPIDER MEMORY (articles read earlier): " + " | ".join(f"{m['title']}: {m['summary']}" for m in d["memory"]))
    return "\n".join(L)[:budget]


SYSTEM = (
    "You are SPIDER, the research brain of a professional trading terminal, writing a full equity research report from the data "
    "below (market data, audited financials, analysts, filings, the latest results press release, news read in full). Think like a "
    "senior buy-side analyst: weigh quality, growth, valuation, balance sheet, momentum, sentiment and catalysts; be specific, use the "
    "numbers, and say plainly what you think and why — and what would prove you wrong. If data is missing, say so; never invent "
    "numbers. This is research for an experienced investor, not personal financial advice. Answer ONLY JSON with these keys: "
    "{\"stance\": \"BULLISH\"|\"LEANING BULLISH\"|\"NEUTRAL\"|\"LEANING BEARISH\"|\"BEARISH\", \"conviction\": 1-5, "
    "\"horizon\": \"e.g. 6-12 months\", \"headline\": \"one line\", \"thesis\": \"4-6 sentences\", "
    "\"bull_case\": [4-6 points], \"bear_case\": [4-6 points], \"fundamentals\": \"growth, margins, returns\", "
    "\"valuation\": \"vs history/peers/growth, cheap or rich and why\", \"balance_sheet\": \"cash, debt, cash flow\", "
    "\"latest_results\": \"what the last report said, beats/misses, guidance\", \"technicals\": \"trend, momentum, levels\", "
    "\"levels\": {\"support\": [numbers], \"resistance\": [numbers]}, \"news_flow\": \"what the news says and its tone\", "
    "\"analysts\": \"consensus, target, recent changes\", \"ownership\": \"institutions and insiders\", "
    "\"catalysts\": [{\"what\": \"\", \"when\": \"\"}], \"risks\": [3-6], \"scenarios\": {\"bull\": {\"price\": number, \"why\": \"\"}, "
    "\"base\": {\"price\": number, \"why\": \"\"}, \"bear\": {\"price\": number, \"why\": \"\"}}, \"change_my_mind\": [2-4], "
    "\"watch\": [3-5 things to watch next], \"scores\": {\"quality\": 0-10, \"growth\": 0-10, \"valuation\": 0-10, "
    "\"momentum\": 0-10, \"sentiment\": 0-10, \"balance_sheet\": 0-10}}")


def _build(symbol: str) -> dict[str, Any]:
    from app.intel import spider_memory as mem
    job = _jobs[symbol]
    step = lambda s: job.update(step=s)  # noqa: E731
    d = _gather(symbol, step)
    step("the brain is reading it all and writing the report")
    view = None
    if llm.config():
        for budget in (17000, 10500):  # the second, shorter read if the first is refused as too large
            view = llm.chat_json(SYSTEM, _brief(d, symbol, budget), max_tokens=2700, temperature=0.2, reasoning="medium", retries=2)
            if view:
                break
    s = d["sec"]
    rep = {
        "symbol": symbol, "name": s.get("name"), "price": s.get("price"), "currency": s.get("currency"), "pct": s.get("pct"),
        "sector": (s.get("profile") or {}).get("sector"), "industry": (s.get("profile") or {}).get("industry"), "generated_at": time.time(),
        "view": view, "brain": bool(view),
        "tech": d.get("tech"), "stats": s.get("stats"), "perf": s.get("perf"), "target": s.get("target"), "market_cap": s.get("market_cap"),
        "week52": s.get("week52"), "next_earnings": s.get("next_earnings"),
        "rules_verdict": {k: (d.get("verdict") or {}).get(k) for k in ("stance", "score", "stance_why")},
        "financials": {k: (d.get("fin") or {}).get(k) for k in ("available", "periods", "rows")},
        "analysts": {k: (d.get("analysts") or {}).get(k) for k in ("recommendation", "surprises", "changes")},
        "filings": [{k: x.get(k) for k in ("form", "note", "filed", "url", "index")} for x in ((d.get("filings") or {}).get("items") or [])[:10]],
        "results": {k: (d.get("results") or {}).get(k) for k in ("url", "filed", "form")} if d.get("results") else None,
        "articles": [{k: a[k] for k in ("title", "source", "ts", "url")} for a in d.get("articles") or []],
        "news": [{k: n.get(k) for k in ("title", "link", "source", "ts")} for n in (d.get("news") or [])[:15]],
        "read": {"articles": len(d.get("articles") or []), "headlines": len(d.get("news") or []) + len(d.get("wire") or []),
                 "filings": len((d.get("filings") or {}).get("items") or []), "results": bool(d.get("results")), "memory": len(d.get("memory") or [])},
    }
    if not view:
        rep["note"] = "No language model answered, so this report has the data and the rules verdict but not the spider's written view."
    mem.save_report(symbol, rep)
    return rep


def _run(symbol: str) -> None:
    try:
        rep = _build(symbol)
        _jobs[symbol].update(status="done", report=rep, step="done")
    except Exception as e:  # noqa: BLE001
        logger.warning("spider report %s failed: %s", symbol, e)
        _jobs[symbol].update(status="error", error=str(e)[:200] if isinstance(e, LookupError) else f"couldn't build the report ({type(e).__name__})")


def report(symbol: str, fresh: bool = False) -> dict[str, Any]:
    from app.intel import spider_memory as mem
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[\w.\-=^]{1,20}", symbol):
        raise ValueError("invalid symbol")
    job = _jobs.get(symbol)
    if job and job["status"] == "running":
        return {"status": "running", "step": job.get("step"), "elapsed": round(time.time() - job["started"])}
    if job and job["status"] == "done" and not fresh and time.time() - job["report"]["generated_at"] < MAX_AGE:
        return {"status": "done", "report": job["report"]}
    if not fresh:
        old = mem.latest_report(symbol, MAX_AGE)
        if old:
            return {"status": "done", "report": old}
    if job and job["status"] == "error" and not fresh and time.time() - job["started"] < 60:
        return {"status": "error", "error": job.get("error")}
    with _lock:
        if (_jobs.get(symbol) or {}).get("status") != "running":
            _jobs[symbol] = {"status": "running", "step": "starting", "started": time.time()}
            threading.Thread(target=_run, args=(symbol,), daemon=True, name=f"spider-report-{symbol}").start()
    return {"status": "running", "step": "starting", "elapsed": 0}


def cached_only(symbol: str) -> dict[str, Any] | None:
    from app.intel import spider_memory as mem
    return mem.latest_report(symbol.upper(), MAX_AGE * 4)
