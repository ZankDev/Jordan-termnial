"""INVESTING API: security deep-dive (Bloomberg-style DES / GP / FA / N / ANR / HDS / FIL / DVD / OMON / RV),
market monitors, macro series and crypto detail. Every source is public and keyless:

  Yahoo Finance   quotes, history, company profile, statistics, analysts, holders, options, news, symbol search
  SEC EDGAR       filings list and audited financial statements (XBRL "company facts") for US issuers
  FRED            macroeconomic series (public CSV downloads)
  CoinGecko       crypto market data and coin descriptions
  Wikipedia       description fallback

All of it is served stale-while-revalidate (see capital_routes.cached).
"""
from __future__ import annotations

import csv
import datetime as dt
import email.utils
import io
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests
from fastapi import APIRouter, HTTPException

from app.api.capital_routes import _POOL, UA, _change, cached

router = APIRouter(prefix="/api/invest")

SEC_UA = {"User-Agent": "NOSTRO Terminal personal-research tool nostro@example.com", "Accept-Encoding": "gzip, deflate"}
Y1 = "https://query1.finance.yahoo.com"
Y2 = "https://query2.finance.yahoo.com"

# ------------------------------------------------------------------ Yahoo session (cookie + crumb for the v10/v7 endpoints)
_ys = requests.Session()
_ys.headers.update(UA)
_crumb: dict[str, Any] = {"v": None, "at": 0.0}
_ylock = threading.Lock()


def _yahoo_crumb(force: bool = False) -> str | None:
    with _ylock:
        if _crumb["v"] and not force and time.time() - _crumb["at"] < 6 * 3600:
            return _crumb["v"]
        try:
            _ys.get("https://fc.yahoo.com", timeout=12)
            r = _ys.get(f"{Y1}/v1/test/getcrumb", timeout=12)
            if r.status_code == 200 and r.text and "<" not in r.text and len(r.text) < 40:
                _crumb["v"], _crumb["at"] = r.text, time.time()
        except requests.RequestException:
            pass
        return _crumb["v"]


def _yget(url: str, params: dict[str, Any] | None = None, crumb: bool = True, timeout: int = 15) -> Any:
    """GET a Yahoo JSON endpoint, refreshing the crumb once if it was rejected."""
    for attempt in range(2):
        p = dict(params or {})
        if crumb:
            c = _yahoo_crumb(force=attempt == 1)
            if c:
                p["crumb"] = c
        r = _ys.get(url, params=p, timeout=timeout)
        if r.status_code in (401, 403) and attempt == 0:
            continue
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Yahoo returned {r.status_code}")
        return r.json()
    raise HTTPException(status_code=502, detail="Yahoo rejected the request")


def _flat(x: Any) -> Any:
    """Yahoo wraps numbers as {raw, fmt}; keep raw, drop the display strings."""
    if isinstance(x, dict):
        if "raw" in x:
            return x["raw"]
        return {k: _flat(v) for k, v in x.items() if k not in ("fmt", "longFmt")}
    if isinstance(x, list):
        return [_flat(v) for v in x]
    return x


def _qsummary(symbol: str, modules: str) -> dict[str, Any]:
    j = _yget(f"{Y1}/v10/finance/quoteSummary/{requests.utils.quote(symbol)}", {"modules": modules})
    res = (j.get("quoteSummary") or {}).get("result")
    return _flat(res[0]) if res else {}


def _qs(symbol: str, modules: str, ttl: float = 600) -> dict[str, Any]:
    try:
        return cached(f"qs:{symbol}:{modules}", ttl, lambda: _qsummary(symbol, modules))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"data unavailable: {type(e).__name__}") from e


# ------------------------------------------------------------------ symbols: Bloomberg-style resolution
SUFFIX = {  # Bloomberg exchange code -> Yahoo suffix
    "US": "", "UN": "", "UW": "", "UQ": "", "UA": "", "UP": "", "LN": ".L", "GR": ".DE", "GY": ".DE", "FP": ".PA", "IM": ".MI",
    "SM": ".MC", "NA": ".AS", "SW": ".SW", "SS": ".ST", "DC": ".CO", "NO": ".OL", "FH": ".HE", "BB": ".BR", "ID": ".IR", "PL": ".LS",
    "AV": ".VI", "JP": ".T", "JT": ".T", "HK": ".HK", "CH": ".SS", "C2": ".SZ", "CN": ".TO", "AU": ".AX", "KS": ".KS", "IN": ".NS",
    "TT": ".TW", "SP": ".SI", "BZ": ".SA", "MM": ".MX", "IT": ".TA", "SJ": ".JO", "NZ": ".NZ",
}
YAHOO_EXCH_TO_BBG = {
    **{k: "US" for k in ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "OQB", "PNK", "OTC", "NAS", "NYS", "NASDAQ", "NYSE")},
    "LSE": "LN", "IOB": "LN", "GER": "GR", "FRA": "GR", "STU": "GR", "BER": "GR", "DUS": "GR", "HAM": "GR", "MUN": "GR", "PAR": "FP",
    "AMS": "NA", "BRU": "BB", "MIL": "IM", "MCE": "SM", "STO": "SS", "CPH": "DC", "OSL": "NO", "HEL": "FH", "VIE": "AV", "LIS": "PL",
    "ISE": "ID", "SWX": "SW", "EBS": "SW", "TOR": "CN", "VAN": "CN", "CVE": "CN", "JPX": "JP", "OSA": "JP", "HKG": "HK", "SHH": "CH",
    "SHZ": "C2", "ASX": "AU", "KSC": "KS", "KOE": "KS", "NSI": "IN", "BSE": "IN", "TAI": "TT", "SES": "SP", "SAO": "BZ", "MEX": "MM",
    "TLV": "IT", "JNB": "SJ", "NZE": "NZ",
}
INDEX_ALIAS = {
    "SPX": "^GSPC", "NDX": "^NDX", "INDU": "^DJI", "RTY": "^RUT", "VIX": "^VIX", "UKX": "^FTSE", "DAX": "^GDAXI", "CAC": "^FCHI",
    "SX5E": "^STOXX50E", "NKY": "^N225", "HSI": "^HSI", "SHCOMP": "000001.SS", "KOSPI": "^KS11", "SENSEX": "^BSESN", "AS51": "^AXJO",
    "SPTSX": "^GSPTSE", "IBOV": "^BVSP", "TA35": "^TA125.TA", "TA125": "^TA125.TA", "COMP": "^IXIC", "MID": "^MID", "SML": "^SP600",
}
COMMODITY_ALIAS = {
    "CL1": "CL=F", "WTI": "CL=F", "CO1": "BZ=F", "BRENT": "BZ=F", "NG1": "NG=F", "GC1": "GC=F", "XAU": "GC=F", "GOLD": "GC=F",
    "SI1": "SI=F", "XAG": "SI=F", "SILVER": "SI=F", "HG1": "HG=F", "COPPER": "HG=F", "PL1": "PL=F", "C1": "ZC=F", "W1": "ZW=F", "S1": "ZS=F",
}
CRYPTO = set("BTC ETH SOL XRP ADA DOGE AVAX DOT LINK MATIC POL LTC BCH ATOM XLM UNI ETC FIL APT ARB OP NEAR ICP HBAR VET ALGO AAVE "
             "MKR SAND MANA FTM EGLD THETA XMR TRX TON SHIB PEPE SUI SEI INJ RUNE GRT IMX RNDR RENDER TAO ENA WLD JUP BNB USDT USDC".split())
FUNCTION_CODES = {"DES", "GP", "G", "FA", "N", "NEWS", "ANR", "HDS", "FIL", "FILINGS", "DVD", "OMON", "OPT", "RV", "PEERS", "DATA", "FLDS", "LP", "LAUNCHPAD", "CN", "EE", "SEC"}
CLASS_WORDS = {"EQUITY", "INDEX", "CURNCY", "CMDTY", "COMDTY", "CORP", "GOVT", "MTGE", "PFD", "CRYPTO", "<GO>", "GO"}


def resolve(q: str) -> dict[str, Any]:
    """'AAPL US Equity DES' / 'VOD LN' / 'TEVA IT' / 'SPX Index' / 'EURUSD Curncy' / 'BTC' -> a Yahoo symbol plus function.

    The function word can lead instead of trail, e.g. 'SEC AAPL', 'GP TSLA', 'FA MSFT', 'N BTC': a command-line style
    that mirrors the trailing form ('AAPL GP') already used on the security page itself. 'SEC' just means "open this
    security" - no specific function - so the caller's own default (GP if available, else DES) takes over.
    """
    tokens = [t for t in re.split(r"\s+", q.strip().upper().replace("<", " ").replace(">", " ")) if t]
    fn = None
    if len(tokens) > 1 and tokens[0] in FUNCTION_CODES:
        fn = tokens.pop(0)
    while tokens and (tokens[-1] in FUNCTION_CODES or tokens[-1] in ("GO",)):
        t = tokens.pop()
        if t in FUNCTION_CODES and fn is None:
            fn = t
    if fn == "SEC":
        fn = None
    cls = None
    while tokens and tokens[-1] in CLASS_WORDS:
        c = tokens.pop()
        if c != "GO" and c != "<GO>":
            cls = c
    if not tokens:
        raise HTTPException(status_code=400, detail="empty query")
    base = tokens[0]
    country = tokens[1] if len(tokens) > 1 else None
    if base in INDEX_ALIAS:
        sym = INDEX_ALIAS[base]
    elif base in COMMODITY_ALIAS:
        sym = COMMODITY_ALIAS[base]
    elif cls in ("CURNCY",) or (re.fullmatch(r"[A-Z]{6}", base) and not country and cls is None and base[:3] in ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "ILS")):
        pair = base if len(base) == 6 else base
        sym = f"{pair[3:]}=X" if pair.startswith("USD") else f"{pair}=X"
    elif base in CRYPTO or cls == "CRYPTO":
        sym = f"{base}-USD"
    elif cls == "INDEX":
        sym = base if base.startswith("^") else f"^{base}"
    elif country and country in SUFFIX:
        sym = base.replace("/", "-") + SUFFIX[country]
    elif country is None and (base.startswith("^") or "=" in base or "." in base or "-" in base):
        sym = base
    elif country is None and re.fullmatch(r"[A-Z]{1,5}", base):
        sym = base
    else:
        sym = None
    if sym is None:  # free text: let Yahoo search decide
        hits = search(" ".join(tokens), 1)["results"]
        if not hits:
            raise HTTPException(status_code=404, detail=f"no security found for '{q}'")
        sym = hits[0]["symbol"]
    return {"symbol": sym, "fn": fn}


def _bbg(symbol: str, exch: str | None, qtype: str | None) -> str:
    qtype = (qtype or "").upper()
    if qtype == "INDEX" or symbol.startswith("^"):
        rev = {v: k for k, v in INDEX_ALIAS.items()}
        return f"{rev.get(symbol, symbol.lstrip('^'))} Index"
    if qtype == "CURRENCY" or symbol.endswith("=X"):
        core = symbol[:-2]
        return f"{core if len(core) == 6 else 'USD' + core} Curncy"
    if qtype == "CRYPTOCURRENCY" or symbol.endswith("-USD"):
        return f"{symbol.replace('-USD', '')} Crypto"
    if qtype == "FUTURE" or symbol.endswith("=F"):
        rev = {v: k for k, v in COMMODITY_ALIAS.items()}
        return f"{rev.get(symbol, symbol[:-2])} Comdty"
    country = YAHOO_EXCH_TO_BBG.get((exch or "").upper())
    if not country:  # derive from the symbol suffix
        for code, suf in SUFFIX.items():
            if suf and symbol.endswith(suf):
                country = code
                break
    base = symbol
    for suf in SUFFIX.values():
        if suf and symbol.endswith(suf):
            base = symbol[: -len(suf)]
            break
    tag = "Equity" if qtype in ("EQUITY", "ETF", "MUTUALFUND", "") else qtype.title()
    return f"{base} {country or 'US'} {tag}"


def search(q: str, limit: int = 8) -> dict[str, Any]:
    def go() -> list[dict[str, Any]]:
        j = _yget(f"{Y1}/v1/finance/search", {"q": q, "quotesCount": limit + 4, "newsCount": 0, "enableFuzzyQuery": "false"}, crumb=False)
        out = []
        for it in j.get("quotes", []):
            if not it.get("symbol") or it.get("quoteType") in ("OPTION",):
                continue
            out.append({"symbol": it["symbol"], "name": it.get("longname") or it.get("shortname") or "", "type": it.get("quoteType"),
                        "exchange": it.get("exchDisp") or it.get("exchange"), "bbg": _bbg(it["symbol"], it.get("exchange"), it.get("quoteType"))})
        return out[:limit]
    return {"results": cached(f"search:{q.lower()}", 300, go)}


@router.get("/search")
def api_search(q: str, limit: int = 8):
    if len(q.strip()) < 1:
        return {"results": []}
    return search(q.strip(), max(1, min(limit, 15)))


@router.get("/resolve")
def api_resolve(q: str):
    return resolve(q)


# ------------------------------------------------------------------ price history
RANGES = {"1D": ("1d", "5m"), "5D": ("5d", "15m"), "1M": ("1mo", "1h"), "3M": ("3mo", "1d"), "6M": ("6mo", "1d"), "YTD": ("ytd", "1d"),
          "1Y": ("1y", "1d"), "2Y": ("2y", "1wk"), "5Y": ("5y", "1wk"), "MAX": ("max", "1mo")}
# Yahoo only serves intraday bars for a limited look-back: the longest range each interval may request
_RANGE_ORDER = ["1d", "5d", "1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y", "max"]
_INTERVAL_CAP = {"1m": "5d", "2m": "1mo", "5m": "1mo", "15m": "1mo", "30m": "1mo", "60m": "2y", "90m": "1mo", "1d": "max", "1wk": "max", "1mo": "max", "3mo": "max"}


def _history(symbol: str, rng: str, interval: str, events: bool = False, prepost: bool = False) -> dict[str, Any]:
    params: dict[str, Any] = {"range": rng, "interval": interval}
    if prepost:
        params["includePrePost"] = "true"  # pre-market (04:00-09:30 ET) and after-hours bars
    if events:
        params["events"] = "div,splits"
    j = _yget(f"{Y1}/v8/finance/chart/{requests.utils.quote(symbol)}", params, crumb=False)
    res = (j.get("chart") or {}).get("result")
    if not res:
        raise HTTPException(status_code=404, detail=f"no market data for {symbol}")
    r = res[0]
    q = r["indicators"]["quote"][0]
    candles = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        candles.append([t, q["open"][i] if q["open"][i] is not None else c, q["high"][i] if q["high"][i] is not None else c,
                        q["low"][i] if q["low"][i] is not None else c, c, q["volume"][i] or 0])
    return {"meta": r["meta"], "candles": candles, "events": r.get("events") or {}}


@router.get("/chart")
def api_chart(symbol: str, range: str = "1Y", interval: str | None = None, prepost: bool = False):
    rng, itv = RANGES.get(range.upper(), RANGES["1Y"])
    if interval:
        if interval not in _INTERVAL_CAP:
            raise HTTPException(status_code=400, detail="interval must be one of " + ", ".join(_INTERVAL_CAP))
        itv = interval
        cap = _INTERVAL_CAP[itv]
        if _RANGE_ORDER.index(rng) > _RANGE_ORDER.index(cap):
            rng = cap  # longer than Yahoo serves at this granularity: use the longest it does
    intraday = itv.endswith("m") and itv not in ("1mo", "3mo")
    pp = bool(prepost and intraday)
    h = cached(f"hist:{symbol}:{rng}:{itv}:{int(pp)}", 8 if intraday else 60, lambda: _history(symbol, rng, itv, prepost=pp))  # short, so the chart can poll for live bars
    m = h["meta"]
    return {"symbol": symbol, "range": range.upper(), "used_range": rng, "interval": itv, "currency": m.get("currency"), "candles": h["candles"],
            "prev_close": m.get("chartPreviousClose") or m.get("previousClose"), "prepost": pp}


# ------------------------------------------------------------------ security snapshot (DES)
def _wiki(name: str) -> str | None:
    try:
        title = re.sub(r"\s+(inc|corp|corporation|ltd|plc|co|company|holdings|group|sa|nv|ag)\.?$", "", name, flags=re.I).strip()
        j = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title.replace(' ', '_'))}", headers=UA, timeout=8).json()
        return j.get("extract")
    except Exception:  # noqa: BLE001
        return None


def _security(symbol: str) -> dict[str, Any]:
    with_qs = True
    try:
        qs = _qsummary(symbol, "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile,quoteType,calendarEvents,recommendationTrend")
    except Exception:  # noqa: BLE001
        qs, with_qs = {}, False
    hist = _history(symbol, "1y", "1d")
    meta, candles = hist["meta"], hist["candles"]
    pr, sd, ks, fd, ap, qt = (qs.get(k) or {} for k in ("price", "summaryDetail", "defaultKeyStatistics", "financialData", "assetProfile", "quoteType"))
    price = pr.get("regularMarketPrice") or meta.get("regularMarketPrice")
    prev = pr.get("regularMarketPreviousClose") or meta.get("chartPreviousClose") or (candles[-2][4] if len(candles) > 1 else price)
    qtype = qt.get("quoteType") or meta.get("instrumentType")
    exch = qt.get("exchange") or meta.get("exchangeName")
    name = pr.get("longName") or qt.get("longName") or meta.get("longName") or pr.get("shortName") or symbol
    desc = ap.get("longBusinessSummary")
    closes = [c[4] for c in candles]

    def perf(n: int) -> float | None:
        return (closes[-1] / closes[-1 - n] - 1) * 100 if len(closes) > n and closes[-1 - n] else None

    ytd_start = next((c[4] for c in candles if dt.datetime.fromtimestamp(c[0], dt.timezone.utc).year == dt.datetime.now(dt.timezone.utc).year), None)
    out = {
        "symbol": symbol, "bbg": _bbg(symbol, exch, qtype), "name": name, "type": qtype, "exchange": pr.get("exchangeName") or meta.get("fullExchangeName") or exch,
        "currency": pr.get("currency") or meta.get("currency"), "market_state": pr.get("marketState"), "timezone": qt.get("timeZoneFullName"),
        "price": price, "prev": prev, "chg": (price - prev) if (price is not None and prev) else None,
        "pct": ((price / prev - 1) * 100) if (price is not None and prev) else None,
        "open": pr.get("regularMarketOpen"), "high": pr.get("regularMarketDayHigh") or meta.get("regularMarketDayHigh"),
        "low": pr.get("regularMarketDayLow") or meta.get("regularMarketDayLow"), "volume": pr.get("regularMarketVolume") or meta.get("regularMarketVolume"),
        "avg_volume": sd.get("averageVolume") or sd.get("averageDailyVolume10Day"), "market_cap": pr.get("marketCap") or sd.get("marketCap"),
        "week52": [meta.get("fiftyTwoWeekLow") or sd.get("fiftyTwoWeekLow"), meta.get("fiftyTwoWeekHigh") or sd.get("fiftyTwoWeekHigh")],
        "perf": {"1W": perf(5), "1M": perf(21), "3M": perf(63), "6M": perf(126), "1Y": perf(len(closes) - 1) if len(closes) > 1 else None,
                 "YTD": ((closes[-1] / ytd_start - 1) * 100) if ytd_start else None},
        "spark": [round(c, 4) for c in closes[-60:]],
        "stats": {
            "pe": sd.get("trailingPE"), "fwd_pe": sd.get("forwardPE") or ks.get("forwardPE"), "eps": ks.get("trailingEps"), "fwd_eps": ks.get("forwardEps"),
            "peg": ks.get("pegRatio"), "pb": ks.get("priceToBook"), "ps": sd.get("priceToSalesTrailing12Months"), "beta": sd.get("beta") or ks.get("beta"),
            "div_yield": sd.get("dividendYield"), "div_rate": sd.get("dividendRate"), "payout": sd.get("payoutRatio"), "ex_div": sd.get("exDividendDate"),
            "shares_out": ks.get("sharesOutstanding"), "float": ks.get("floatShares"), "short_pct_float": ks.get("shortPercentOfFloat"),
            "ev": ks.get("enterpriseValue"), "ev_rev": ks.get("enterpriseToRevenue"), "ev_ebitda": ks.get("enterpriseToEbitda"),
            "book_value": ks.get("bookValue"), "profit_margin": fd.get("profitMargins") or ks.get("profitMargins"), "op_margin": fd.get("operatingMargins"),
            "gross_margin": fd.get("grossMargins"), "roe": fd.get("returnOnEquity"), "roa": fd.get("returnOnAssets"), "revenue": fd.get("totalRevenue"),
            "rev_growth": fd.get("revenueGrowth"), "earn_growth": fd.get("earningsGrowth"), "ebitda": fd.get("ebitda"), "fcf": fd.get("freeCashflow"),
            "total_cash": fd.get("totalCash"), "total_debt": fd.get("totalDebt"), "debt_equity": fd.get("debtToEquity"), "current_ratio": fd.get("currentRatio"),
            "sma50": sd.get("fiftyDayAverage"), "sma200": sd.get("twoHundredDayAverage"),
        },
        "target": {"mean": fd.get("targetMeanPrice"), "high": fd.get("targetHighPrice"), "low": fd.get("targetLowPrice"), "median": fd.get("targetMedianPrice"),
                   "n": fd.get("numberOfAnalystOpinions"), "rating": fd.get("recommendationKey"), "score": fd.get("recommendationMean")},
        "profile": {"sector": ap.get("sector"), "industry": ap.get("industry"), "employees": ap.get("fullTimeEmployees"), "country": ap.get("country"),
                    "city": ap.get("city"), "state": ap.get("state"), "address": ap.get("address1"), "phone": ap.get("phone"), "website": ap.get("website"),
                    "officers": [{"name": o.get("name"), "title": o.get("title"), "age": o.get("age"), "pay": o.get("totalPay")} for o in (ap.get("companyOfficers") or [])[:10]]},
        "next_earnings": ((qs.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate"),
        "description": desc, "description_source": "Yahoo Finance" if desc else None, "detail": with_qs,
    }
    if not desc and qtype in ("EQUITY", "ETF"):
        w = _wiki(name)
        if w:
            out["description"], out["description_source"] = w, "Wikipedia"
    return out


@router.get("/security")
def api_security(symbol: str):
    symbol = symbol.strip()
    if not re.fullmatch(r"[\w.\-=^]{1,20}", symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    return cached(f"sec:{symbol}", 45, lambda: _security(symbol))


# ------------------------------------------------------------------ news
def _yahoo_news(q: str, n: int) -> list[dict[str, Any]]:
    j = _yget(f"{Y1}/v1/finance/search", {"q": q, "quotesCount": 0, "newsCount": n}, crumb=False)
    return [{"title": it.get("title"), "link": it.get("link"), "source": it.get("publisher"), "ts": it.get("providerPublishTime"),
             "tickers": it.get("relatedTickers", [])[:4]} for it in j.get("news", []) if it.get("title") and it.get("link")]


def _yahoo_rss(symbol: str) -> list[dict[str, Any]]:
    r = requests.get("https://feeds.finance.yahoo.com/rss/2.0/headline", params={"s": symbol, "region": "US", "lang": "en-US"}, headers=UA, timeout=10)
    out = []
    for it in ET.fromstring(r.content).findall(".//item"):
        ts = time.time()
        try:
            ts = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except Exception:  # noqa: BLE001
            pass
        out.append({"title": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(), "source": "Yahoo Finance", "ts": ts, "tickers": [symbol]})
    return out


def _google_news(q: str) -> list[dict[str, Any]]:
    r = requests.get("https://news.google.com/rss/search", params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}, headers=UA, timeout=12)
    out = []
    for it in ET.fromstring(r.content).findall(".//item")[:40]:
        ts = time.time()
        try:
            ts = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except Exception:  # noqa: BLE001
            pass
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(f" - {src}"):
            title = title[: -len(src) - 3]
        out.append({"title": title, "link": (it.findtext("link") or "").strip(), "source": src or "Google News", "ts": ts, "tickers": []})
    return out


def _news(q: str, symbol: bool) -> list[dict[str, Any]]:
    batches: list[list[dict[str, Any]]] = []
    sources = [lambda: _yahoo_news(q, 30), lambda: _google_news(f"{q} stock" if symbol else q)]
    if symbol:
        sources.append(lambda: _yahoo_rss(q))
    for fn in sources:
        try:
            batches.append(fn())
        except Exception:  # noqa: BLE001
            batches.append([])
    seen, out = set(), []
    for it in sorted((i for b in batches for i in b), key=lambda i: i.get("ts") or 0, reverse=True):
        key = re.sub(r"\W+", "", (it["title"] or "").lower())[:60]
        if key and key not in seen and str(it["link"]).startswith(("http://", "https://")):
            seen.add(key)
            out.append(it)
    return out[:40]


@router.get("/news")
def api_news(symbol: str | None = None, q: str | None = None):
    query = (symbol or q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="give a symbol or a search query")
    return {"query": query, "items": cached(f"news:{'s' if symbol else 'q'}:{query.lower()}", 180, lambda: _news(query, bool(symbol)))}


# ------------------------------------------------------------------ SEC EDGAR: filings + audited financials
def _sec_map() -> dict[str, tuple[int, str]]:
    def go() -> dict[str, tuple[int, str]]:
        j = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA, timeout=25).json()
        return {v["ticker"].upper(): (int(v["cik_str"]), v["title"]) for v in j.values()}
    return cached("sec:tickers", 24 * 3600, go)


def _cik(symbol: str) -> tuple[int, str] | None:
    try:
        return _sec_map().get(symbol.upper().replace(".", "-"))
    except Exception:  # noqa: BLE001
        return None


def _sec_json(url: str) -> Any:
    r = requests.get(url, headers=SEC_UA, timeout=30)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"SEC returned {r.status_code}")
    return r.json()


FORM_NOTES = {"10-K": "Annual report", "10-Q": "Quarterly report", "8-K": "Current report", "4": "Insider transaction", "3": "Insider initial statement",
              "5": "Insider annual statement", "DEF 14A": "Proxy statement", "S-1": "Registration statement", "S-3": "Registration statement", "13F-HR": "Institutional holdings",
              "SC 13G": "5% owner (passive)", "SC 13D": "5% owner (active)", "144": "Notice of proposed sale", "424B2": "Prospectus", "6-K": "Foreign issuer report", "20-F": "Foreign annual report",
              "11-K": "Employee plan report", "25": "Delisting notice", "10-K/A": "Annual report (amended)", "8-K/A": "Current report (amended)", "ARS": "Annual report to holders"}


@router.get("/filings")
def api_filings(symbol: str, form: str | None = None):
    ck = _cik(symbol)
    if not ck:
        return {"available": False, "reason": "SEC filings are available for US-listed issuers only.", "items": []}
    cik, title = ck

    def go() -> dict[str, Any]:
        j = _sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        rec = j["filings"]["recent"]
        items = []
        for i, f in enumerate(rec["form"]):
            acc = rec["accessionNumber"][i]
            doc = rec["primaryDocument"][i]
            base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
            items.append({"form": f, "note": FORM_NOTES.get(f, ""), "filed": rec["filingDate"][i], "period": rec["reportDate"][i] or None,
                          "desc": rec.get("primaryDocDescription", [""] * len(rec["form"]))[i], "url": f"{base}/{doc}" if doc else f"{base}/{acc}-index.htm",
                          "index": f"{base}/{acc}-index.htm"})
        return {"available": True, "cik": f"{cik:010d}", "name": j.get("name"), "sic": j.get("sicDescription"), "exchanges": j.get("exchanges"), "tickers": j.get("tickers"),
                "fiscal_year_end": j.get("fiscalYearEnd"), "state": j.get("stateOfIncorporation"), "items": items[:400]}
    data = cached(f"fil:{cik}", 900, go)
    items = data["items"]
    if form:
        items = [x for x in items if x["form"].upper() == form.upper()]
    return {**data, "items": items[:80]}


XBRL = [  # key, label, unit, tags in order of preference
    ("revenue", "Revenue", "USD", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]),
    ("cogs", "Cost of revenue", "USD", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]),
    ("gross", "Gross profit", "USD", ["GrossProfit"]),
    ("rnd", "R&D expense", "USD", ["ResearchAndDevelopmentExpense"]),
    ("sga", "SG&A expense", "USD", ["SellingGeneralAndAdministrativeExpense"]),
    ("opinc", "Operating income", "USD", ["OperatingIncomeLoss"]),
    ("netinc", "Net income", "USD", ["NetIncomeLoss", "ProfitLoss"]),
    ("eps", "Diluted EPS", "USD/shares", ["EarningsPerShareDiluted"]),
    ("shares", "Diluted shares", "shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
    ("cash", "Cash & equivalents", "USD", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    ("curassets", "Current assets", "USD", ["AssetsCurrent"]),
    ("assets", "Total assets", "USD", ["Assets"]),
    ("curliab", "Current liabilities", "USD", ["LiabilitiesCurrent"]),
    ("debt", "Long-term debt", "USD", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("liab", "Total liabilities", "USD", ["Liabilities"]),
    ("equity", "Shareholders' equity", "USD", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    ("ocf", "Operating cash flow", "USD", ["NetCashProvidedByUsedInOperatingActivities"]),
    ("capex", "Capital expenditure", "USD", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
    ("div", "Dividends paid", "USD", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]),
    ("buyback", "Share repurchases", "USD", ["PaymentsForRepurchaseOfCommonStock"]),
]
INSTANT = {"cash", "curassets", "assets", "curliab", "debt", "liab", "equity"}


def _annual(facts: dict[str, Any], tags: list[str], unit: str, instant: bool) -> dict[str, float]:
    out: dict[str, tuple[str, float]] = {}
    for tag in tags:
        for e in (((facts.get(tag) or {}).get("units") or {}).get(unit) or []):
            if e.get("form") not in ("10-K", "10-K/A") or e.get("fp") != "FY":
                continue
            end = e["end"]
            if not instant:
                try:
                    days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(e["start"])).days
                except Exception:  # noqa: BLE001
                    continue
                if not 340 <= days <= 380:
                    continue
            if end not in out or e["filed"] >= out[end][0]:
                if end in out and tag != tags[0] and out[end][0] > e["filed"]:
                    continue
                out[end] = (e["filed"], float(e["val"]))
        if len(out) >= 4:
            break  # the preferred tag covers enough years; do not mix definitions
    return {k: v[1] for k, v in out.items()}


@router.get("/financials")
def api_financials(symbol: str):
    ck = _cik(symbol)
    if not ck:
        return {"available": False, "reason": "Audited statements (SEC XBRL) are available for US-listed issuers. See DES for Yahoo ratios on this security.", "periods": [], "rows": []}
    cik, title = ck

    def go() -> dict[str, Any]:
        facts = _sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")["facts"]
        gaap = facts.get("us-gaap") or {}
        series = {k: _annual(gaap, tags, unit, k in INSTANT) for k, _l, unit, tags in XBRL}
        ends = sorted({d for k in ("revenue", "netinc", "assets") for d in series[k]}, reverse=True)[:6]
        if not ends:
            return {"available": False, "reason": "No annual XBRL data found for this issuer.", "periods": [], "rows": []}
        if not series["gross"] and series["revenue"] and series["cogs"]:
            series["gross"] = {d: series["revenue"][d] - series["cogs"][d] for d in series["revenue"] if d in series["cogs"]}
        series["fcf"] = {d: series["ocf"][d] - abs(series["capex"].get(d, 0.0)) for d in series["ocf"]}
        rows = []
        for k, label, unit, _t in XBRL[:9] + [("fcf", "Free cash flow", "USD", [])] + XBRL[9:]:
            vals = [series[k].get(d) for d in ends]
            if any(v is not None for v in vals):
                rows.append({"key": k, "label": label, "unit": unit, "values": vals})
        return {"available": True, "source": "SEC EDGAR XBRL (audited, 10-K)", "company": title, "periods": ends, "rows": rows}
    return cached(f"fin:{cik}", 6 * 3600, go)


# ------------------------------------------------------------------ analysts, holders, dividends, options, peers
@router.get("/analysts")
def api_analysts(symbol: str):
    qs = _qs(symbol, "recommendationTrend,upgradeDowngradeHistory,earningsTrend,earningsHistory,financialData,calendarEvents")
    fd = qs.get("financialData") or {}
    hist = [{"firm": h.get("firm"), "action": h.get("action"), "from": h.get("fromGrade"), "to": h.get("toGrade"), "ts": h.get("epochGradeDate")}
            for h in ((qs.get("upgradeDowngradeHistory") or {}).get("history") or [])[:25]]
    return {
        "recommendation": (qs.get("recommendationTrend") or {}).get("trend") or [],
        "target": {"mean": fd.get("targetMeanPrice"), "high": fd.get("targetHighPrice"), "low": fd.get("targetLowPrice"), "median": fd.get("targetMedianPrice"),
                   "n": fd.get("numberOfAnalystOpinions"), "rating": fd.get("recommendationKey"), "score": fd.get("recommendationMean"), "price": fd.get("currentPrice")},
        "changes": hist,
        "estimates": [{"period": t.get("period"), "end": t.get("endDate"), "eps_avg": (t.get("earningsEstimate") or {}).get("avg"), "eps_low": (t.get("earningsEstimate") or {}).get("low"),
                       "eps_high": (t.get("earningsEstimate") or {}).get("high"), "rev_avg": (t.get("revenueEstimate") or {}).get("avg"), "growth": t.get("growth"),
                       "analysts": (t.get("earningsEstimate") or {}).get("numberOfAnalysts")} for t in ((qs.get("earningsTrend") or {}).get("trend") or [])],
        "surprises": [{"quarter": h.get("quarter"), "actual": h.get("epsActual"), "estimate": h.get("epsEstimate"), "surprise_pct": h.get("surprisePercent")}
                      for h in ((qs.get("earningsHistory") or {}).get("history") or [])],
        "next_earnings": ((qs.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate"),
    }


@router.get("/verdict")
def api_verdict(symbol: str):
    """WHY panel: reasons to buy / not buy / hold, each with the numbers behind it (see app/invest/verdict.py)."""
    symbol = symbol.strip()
    if not re.fullmatch(r"[\w.\-=^]{1,20}", symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")

    def go() -> dict[str, Any]:
        from app.invest import verdict

        sec = cached(f"sec:{symbol}", 45, lambda: _security(symbol))
        closes = [c[4] for c in _history(symbol, "1y", "1d")["candles"] if c[4] is not None]
        try:
            analysts = api_analysts(symbol)
        except Exception:  # noqa: BLE001 - the verdict still works on the rest
            analysts = None
        try:
            news = cached(f"news:s:{symbol.lower()}", 180, lambda: _news(symbol, True))
        except Exception:  # noqa: BLE001
            news = []
        return verdict.build(sec, closes, analysts, news)

    return cached(f"verdict:{symbol}", 120, go)


@router.get("/gamma")
def api_gamma(symbol: str):
    """Gamma exposure levels from the option chain: call wall, put wall, zero-gamma flip, net GEX and the per-strike profile (app/invest/gamma.py)."""
    from app.invest import gamma

    try:
        return gamma.compute(symbol.upper())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"option data unavailable: {type(e).__name__}") from e


@router.get("/gamma/surface")
def api_gamma_surface(symbol: str, lo: float | None = None, hi: float | None = None):
    """Strike x expiration grid of net GEX for the interactive 3D surface (optionally only strikes between lo and hi, e.g. a box drawn on the chart)."""
    from app.invest import gamma

    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    try:
        return gamma.surface(symbol.upper(), lo=lo, hi=hi)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"option data unavailable: {type(e).__name__}") from e


@router.get("/quant")
def api_quant(symbol: str, bench: str = "SPY", pair: str | None = None, years: int = 3):
    """Quant lab: SciPy distribution fits + VaR, statsmodels regression / ADF / ACF / cointegration, NumPy Monte Carlo (app/invest/quant.py)."""
    from app.invest import quant

    try:
        return quant.analyse(symbol.upper(), bench.upper(), pair.upper() if pair else None, max(1, min(years, 10)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"quant analysis failed: {type(e).__name__}") from e


@router.get("/holders")
def api_holders(symbol: str):
    qs = _qs(symbol, "majorHoldersBreakdown,institutionOwnership,fundOwnership,insiderHolders,insiderTransactions,netSharePurchaseActivity")
    own = lambda k: ((qs.get(k) or {}).get("ownershipList") or [])  # noqa: E731
    row = lambda o: {"name": o.get("organization"), "pct": o.get("pctHeld"), "shares": o.get("position"), "value": o.get("value"), "date": o.get("reportDate"), "chg": o.get("pctChange")}  # noqa: E731
    return {
        "breakdown": qs.get("majorHoldersBreakdown") or {},
        "institutions": [row(o) for o in own("institutionOwnership")[:15]],
        "funds": [row(o) for o in own("fundOwnership")[:15]],
        "insiders": [{"name": h.get("name"), "relation": h.get("relation"), "last": h.get("latestTransDescription"), "date": h.get("latestTransDate"), "shares": h.get("positionDirect")}
                     for h in ((qs.get("insiderHolders") or {}).get("holders") or [])[:15]],
        "transactions": [{"name": t.get("filerName"), "relation": t.get("filerRelation"), "text": t.get("transactionText"), "shares": t.get("shares"), "value": t.get("value"), "date": t.get("startDate")}
                         for t in ((qs.get("insiderTransactions") or {}).get("transactions") or [])[:20]],
        "net_activity": qs.get("netSharePurchaseActivity") or {},
    }


@router.get("/dividends")
def api_dividends(symbol: str):
    h = cached(f"hist:{symbol}:max:1mo:ev", 3600, lambda: _history(symbol, "10y", "1mo", events=True))
    ev = h["events"]
    divs = sorted(({"ts": int(v["date"]), "amount": v["amount"]} for v in (ev.get("dividends") or {}).values()), key=lambda d: d["ts"], reverse=True)
    splits = sorted(({"ts": int(v["date"]), "ratio": v.get("splitRatio") or f"{v.get('numerator')}:{v.get('denominator')}"} for v in (ev.get("splits") or {}).values()), key=lambda d: d["ts"], reverse=True)
    yearly: dict[int, float] = {}
    for d in divs:
        y = dt.datetime.fromtimestamp(d["ts"], dt.timezone.utc).year
        yearly[y] = yearly.get(y, 0.0) + d["amount"]
    return {"dividends": divs[:60], "splits": splits[:20], "yearly": [{"year": y, "total": round(t, 4)} for y, t in sorted(yearly.items(), reverse=True)][:10],
            "currency": h["meta"].get("currency")}


@router.get("/options")
def api_options(symbol: str, date: int | None = None):
    params: dict[str, Any] = {}
    if date:
        params["date"] = date
    j = cached(f"opt:{symbol}:{date}", 120, lambda: _yget(f"{Y2}/v7/finance/options/{requests.utils.quote(symbol)}", params))
    res = ((j.get("optionChain") or {}).get("result") or [None])[0]
    if not res:
        return {"expirations": [], "calls": [], "puts": [], "price": None}
    ch = (res.get("options") or [{}])[0]
    row = lambda o: {"strike": o.get("strike"), "last": o.get("lastPrice"), "bid": o.get("bid"), "ask": o.get("ask"), "vol": o.get("volume"), "oi": o.get("openInterest"),  # noqa: E731
                     "iv": o.get("impliedVolatility"), "itm": o.get("inTheMoney"), "chg": o.get("percentChange")}
    return {"price": (res.get("quote") or {}).get("regularMarketPrice"), "expirations": res.get("expirationDates") or [], "date": ch.get("expirationDate"),
            "calls": [row(o) for o in ch.get("calls", [])], "puts": [row(o) for o in ch.get("puts", [])]}


@router.get("/peers")
def api_peers(symbol: str):
    def go() -> list[dict[str, Any]]:
        j = _yget(f"{Y2}/v6/finance/recommendationsbysymbol/{requests.utils.quote(symbol)}", {})
        syms = [r["symbol"] for r in ((j.get("finance") or {}).get("result") or [{}])[0].get("recommendedSymbols", [])][:10]
        rows = []
        for s, h in zip(syms, _POOL.map(lambda x: _safe_history(x), syms)):
            if h:
                m, c = h["meta"], h["candles"]
                prev = c[-2][4] if len(c) > 1 else m.get("chartPreviousClose")
                rows.append({"symbol": s, "name": m.get("longName") or m.get("shortName"), "price": m.get("regularMarketPrice"),
                             "pct": ((m["regularMarketPrice"] / prev - 1) * 100) if prev and m.get("regularMarketPrice") else None, "week52": [m.get("fiftyTwoWeekLow"), m.get("fiftyTwoWeekHigh")],
                             "spark": [x[4] for x in c[-30:]]})
        return rows
    try:
        return {"peers": cached(f"peers:{symbol}", 900, go)}
    except HTTPException:
        return {"peers": []}


def _safe_history(symbol: str, rng: str = "6mo", interval: str = "1d") -> dict[str, Any] | None:
    try:
        return cached(f"hist:{symbol}:{rng}:{interval}", 600, lambda: _history(symbol, rng, interval))
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ monitors
MONITORS: dict[str, list[tuple[str, str]]] = {
    "indices": [("^GSPC", "S&P 500"), ("^DJI", "Dow Jones"), ("^NDX", "Nasdaq 100"), ("^IXIC", "Nasdaq Comp"), ("^RUT", "Russell 2000"), ("^VIX", "VIX"),
                ("^FTSE", "FTSE 100"), ("^GDAXI", "DAX"), ("^FCHI", "CAC 40"), ("^STOXX50E", "Euro Stoxx 50"), ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"),
                ("000001.SS", "Shanghai Comp"), ("^KS11", "KOSPI"), ("^BSESN", "Sensex"), ("^AXJO", "ASX 200"), ("^GSPTSE", "TSX Comp"), ("^BVSP", "Bovespa"), ("^TA125.TA", "TA-125")],
    "futures": [("ES=F", "S&P 500 E-mini"), ("NQ=F", "Nasdaq E-mini"), ("YM=F", "Dow E-mini"), ("RTY=F", "Russell E-mini")],
    "stocks": [(s, "") for s in ("AAPL MSFT NVDA GOOGL AMZN META TSLA AVGO BRK-B LLY JPM V UNH XOM WMT MA JNJ PG HD ORCL COST NFLX BAC AMD CRM ABBV KO PEP ADBE "
                                 "TMO CSCO MCD INTC QCOM DIS CAT GS UBER PLTR COIN MSTR SMCI ARM SHOP NVO TSM ASML BABA").split()],
    "etfs": [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow 30"), ("IWM", "Russell 2000"), ("VTI", "US Total Market"), ("VEA", "Developed ex-US"), ("EEM", "Emerging Mkts"),
             ("VNQ", "US Real Estate"), ("TLT", "20Y+ Treasury"), ("IEF", "7-10Y Treasury"), ("HYG", "High Yield"), ("LQD", "IG Corporate"), ("GLD", "Gold"), ("SLV", "Silver"),
             ("USO", "Crude Oil"), ("UNG", "Natural Gas"), ("SMH", "Semiconductors"), ("ARKK", "ARK Innovation"), ("IBIT", "Bitcoin Trust"), ("ETHA", "Ether Trust"), ("XLK", "Technology"),
             ("XLF", "Financials"), ("XLE", "Energy"), ("XLV", "Health Care"), ("XLY", "Cons. Discretionary"), ("XLP", "Cons. Staples"), ("XLI", "Industrials"), ("XLU", "Utilities"),
             ("XLB", "Materials"), ("XLRE", "Real Estate"), ("XLC", "Communication")],
    "sectors": [("XLK", "Technology"), ("XLF", "Financials"), ("XLE", "Energy"), ("XLV", "Health Care"), ("XLY", "Cons. Discretionary"), ("XLP", "Cons. Staples"),
                ("XLI", "Industrials"), ("XLU", "Utilities"), ("XLB", "Materials"), ("XLRE", "Real Estate"), ("XLC", "Communication")],
    "fx": [("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"), ("USDJPY=X", "USD/JPY"), ("USDCHF=X", "USD/CHF"), ("AUDUSD=X", "AUD/USD"), ("USDCAD=X", "USD/CAD"),
           ("NZDUSD=X", "NZD/USD"), ("ILS=X", "USD/ILS"), ("EURILS=X", "EUR/ILS"), ("EURGBP=X", "EUR/GBP"), ("EURJPY=X", "EUR/JPY"), ("DX-Y.NYB", "US Dollar Index")],
    "commodities": [("GC=F", "Gold"), ("SI=F", "Silver"), ("PL=F", "Platinum"), ("HG=F", "Copper"), ("CL=F", "WTI Crude"), ("BZ=F", "Brent Crude"), ("NG=F", "Natural Gas"),
                    ("ZC=F", "Corn"), ("ZW=F", "Wheat"), ("ZS=F", "Soybeans"), ("KC=F", "Coffee"), ("SB=F", "Sugar")],
    "rates": [("^IRX", "US 13-Week"), ("^FVX", "US 5-Year"), ("^TNX", "US 10-Year"), ("^TYX", "US 30-Year")],
}


def _mon_row(sym: str, name: str) -> dict[str, Any] | None:
    h = _safe_history(sym, "1y", "1d")
    if not h or not h["candles"]:
        return None
    m, c = h["meta"], h["candles"]
    closes = [x[4] for x in c]
    price = m.get("regularMarketPrice") or closes[-1]
    mkt_day = dt.datetime.fromtimestamp(m.get("regularMarketTime") or c[-1][0], dt.timezone.utc).date()
    last_day = dt.datetime.fromtimestamp(c[-1][0], dt.timezone.utc).date()
    prev = closes[-2] if len(closes) > 1 and last_day >= mkt_day else closes[-1]
    lo, hi = m.get("fiftyTwoWeekLow") or min(closes), m.get("fiftyTwoWeekHigh") or max(closes)
    perf = lambda n: ((price / closes[-1 - n] - 1) * 100) if len(closes) > n and closes[-1 - n] else None  # noqa: E731
    year = dt.datetime.now(dt.timezone.utc).year
    y0 = next((x[4] for x in c if dt.datetime.fromtimestamp(x[0], dt.timezone.utc).year == year), None)
    return {"symbol": sym, "name": name or m.get("longName") or m.get("shortName") or sym, "price": price, "chg": price - prev, "pct": (price / prev - 1) * 100 if prev else 0.0,
            "p1w": perf(5), "p1m": perf(21), "ytd": ((price / y0 - 1) * 100) if y0 else None, "lo": lo, "hi": hi, "vol": c[-1][5], "spark": [round(x, 4) for x in closes[-30:]], "cur": m.get("currency")}


def _build_monitor(name: str) -> list[dict[str, Any]]:
    items = MONITORS[name]
    rows = list(_POOL.map(lambda it: _mon_row(*it), items))
    return [r for r in rows if r]


@router.get("/monitor")
def api_monitor(set: str):
    if set not in MONITORS:
        raise HTTPException(status_code=404, detail=f"unknown monitor '{set}'")
    return {"set": set, "rows": cached(f"mon:{set}", 90, lambda: _build_monitor(set)), "at": time.time()}


@router.get("/quotes")
def api_quotes(symbols: str):
    """Batch snapshot rows (same shape as a monitor) for an arbitrary list, e.g. the watchlist."""
    syms = [x.strip() for x in symbols.split(",") if re.fullmatch(r"[\w.\-=^]{1,20}", x.strip())][:40]
    rows = [r for r in _POOL.map(lambda x: _mon_row(x, ""), syms) if r]
    return {"rows": rows, "at": time.time()}


# ------------------------------------------------------------------ live wall (many small charts)
WALL_RANGES = {"1D": ("1d", "5m"), "5D": ("5d", "15m"), "1M": ("1mo", "1h"), "3M": ("3mo", "1d"), "1Y": ("1y", "1d")}
WALL_BARS = 64


def _wall_row(sym: str, rng: str) -> dict[str, Any] | None:
    r, itv = WALL_RANGES[rng]
    try:
        h = cached(f"wall:{sym}:{rng}", 30 if rng in ("1D", "5D") else 120, lambda: _history(sym, r, itv))
    except Exception:  # noqa: BLE001
        return None
    c = h["candles"]
    if len(c) < 2:
        return None
    m = h["meta"]
    price = c[-1][4]
    base = (m.get("chartPreviousClose") or m.get("previousClose") or c[0][1]) if rng == "1D" else c[0][1]
    g = max(1, -(-len(c) // WALL_BARS))  # merge neighbouring bars so every tile carries at most WALL_BARS candles
    bars = []
    for i in range(0, len(c), g):
        part = c[i:i + g]
        bars.append([part[0][1], max(x[2] for x in part), min(x[3] for x in part), part[-1][4]])
    return {"symbol": sym, "name": m.get("shortName") or m.get("longName") or sym, "price": price, "base": base, "pct": (price / base - 1) * 100 if base else 0.0,
            "bars": [[round(v, 6) for v in b] for b in bars], "t0": c[0][0], "t1": c[-1][0], "cur": m.get("currency")}


@router.get("/wall")
def api_wall(symbols: str, range: str = "1D"):
    """Small live charts for up to 60 symbols at once (the LIVE WALL screen): one row per symbol with merged OHLC bars."""
    rng = range.upper() if range.upper() in WALL_RANGES else "1D"
    syms = []
    for x in symbols.split(","):
        x = x.strip()
        if re.fullmatch(r"[\w.\-=^]{1,20}", x) and x not in syms:
            syms.append(x)
    syms = syms[:60]
    rows = {r["symbol"]: r for r in _POOL.map(lambda x: _wall_row(x, rng), syms) if r}
    return {"range": rng, "rows": [rows[x] for x in syms if x in rows], "missing": [x for x in syms if x not in rows], "at": time.time()}


# ------------------------------------------------------------------ RWAS: stablecoin supply dashboard (DefiLlama)
LLAMA_SC = "https://stablecoins.llama.fi"
RWAS_MIN_USD = 100_000_000  # the dashboard covers stablecoins above $100M, like the Bloomberg screen it mirrors
PEG_BUCKET = {"peggedUSD": "USD", "peggedEUR": "EUR", "peggedVAR": "Gold, baskets and yield-bearing"}


def _llama(path: str, params: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    r = requests.get(f"{LLAMA_SC}{path}", params=params, headers=UA, timeout=timeout)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"DefiLlama returned {r.status_code}")
    return r.json()


def _sc_usd(asset: dict[str, Any], d: dict[str, Any] | None) -> float:
    """A circulating figure in USD: USD pegs are already dollars, other pegs are token counts priced in dollars."""
    v = sum((d or {}).values()) if d else 0.0
    return v if asset.get("pegType") == "peggedUSD" else v * float(asset.get("price") or 1.0)


def _build_rwas() -> dict[str, Any]:
    assets = _llama("/stablecoins", {"includePrices": "true"})["peggedAssets"]
    coins: list[dict[str, Any]] = []
    chains: dict[str, dict[str, float]] = {}
    tot = {"now": 0.0, "d1": 0.0, "d7": 0.0, "d30": 0.0}
    peg: dict[str, float] = {}
    for a in assets:
        now = _sc_usd(a, a.get("circulating"))
        if now < RWAS_MIN_USD:
            continue
        p1, p7, p30 = (_sc_usd(a, a.get(k)) for k in ("circulatingPrevDay", "circulatingPrevWeek", "circulatingPrevMonth"))
        tot["now"] += now
        tot["d1"] += p1
        tot["d7"] += p7
        tot["d30"] += p30
        bucket = PEG_BUCKET.get(a.get("pegType"), "Other fiat")
        peg[bucket] = peg.get(bucket, 0.0) + now
        price = a.get("price")
        per_chain = []
        for ch, v in (a.get("chainCirculating") or {}).items():
            cur = _sc_usd(a, v.get("current"))
            c = chains.setdefault(ch, {"now": 0.0, "d7": 0.0, "d30": 0.0})
            c["now"] += cur
            c["d7"] += _sc_usd(a, v.get("circulatingPrevWeek"))
            c["d30"] += _sc_usd(a, v.get("circulatingPrevMonth"))
            per_chain.append((ch, cur))
        per_chain.sort(key=lambda x: -x[1])
        pct = lambda p: ((now / p - 1) * 100) if p else None  # noqa: E731
        coins.append({"id": a["id"], "symbol": a["symbol"], "name": a["name"], "peg": a.get("pegType"), "bucket": bucket, "mechanism": a.get("pegMechanism"), "supply": now,
                      "d1": now - p1, "d7": now - p7, "d30": now - p30, "d1_pct": pct(p1), "d7_pct": pct(p7), "d30_pct": pct(p30), "price": price,
                      "dev_bps": ((price - 1) * 1e4) if price is not None and a.get("pegType") == "peggedUSD" else None,
                      "n_chains": len(per_chain), "chains": [{"chain": c, "supply": s} for c, s in per_chain[:4]]})
    coins.sort(key=lambda c: -c["supply"])
    for c in coins:
        c["share"] = c["supply"] / tot["now"] * 100 if tot["now"] else 0
    chain_rows = sorted(({"chain": k, "supply": v["now"], "d7": v["now"] - v["d7"], "d30": v["now"] - v["d30"], "d7_pct": ((v["now"] / v["d7"] - 1) * 100) if v["d7"] else None,
                          "d30_pct": ((v["now"] / v["d30"] - 1) * 100) if v["d30"] else None, "share": v["now"] / tot["now"] * 100 if tot["now"] else 0}
                         for k, v in chains.items() if v["now"] >= 10_000_000), key=lambda r: -r["supply"])[:25]
    hist: list[list[float]] = []
    try:
        for row in _llama("/stablecoincharts/all")[-400:]:
            hist.append([int(row["date"]), sum((row.get("totalCirculatingUSD") or {}).values())])
    except Exception:  # noqa: BLE001
        pass
    net = [[hist[i][0], hist[i][1] - hist[i - 1][1]] for i in range(1, len(hist))][-120:]
    return {"total": tot["now"], "d1": tot["now"] - tot["d1"], "d7": tot["now"] - tot["d7"], "d30": tot["now"] - tot["d30"],
            "d1_pct": (tot["now"] / tot["d1"] - 1) * 100 if tot["d1"] else None, "d7_pct": (tot["now"] / tot["d7"] - 1) * 100 if tot["d7"] else None,
            "d30_pct": (tot["now"] / tot["d30"] - 1) * 100 if tot["d30"] else None, "n_coins": len(coins), "coins": coins[:60],
            "by_peg": sorted(({"peg": k, "supply": v, "share": v / tot["now"] * 100} for k, v in peg.items()), key=lambda r: -r["supply"]),
            "by_chain": chain_rows, "history": hist[-365:], "net": net, "min_usd": RWAS_MIN_USD}


@router.get("/rwas")
def api_rwas():
    return {**cached("rwas:all", 300, _build_rwas), "at": time.time(), "source": "DefiLlama stablecoins"}


@router.get("/rwas/coin")
def api_rwas_coin(id: str):
    if not re.fullmatch(r"\d{1,5}", id):
        raise HTTPException(status_code=400, detail="bad id")

    def go() -> dict[str, Any]:
        j = _llama(f"/stablecoin/{id}")
        peg = j.get("pegType") or "peggedUSD"
        pts = [[int(t["date"]), float(sum((t.get("circulating") or {}).values()))] for t in (j.get("tokens") or []) if t.get("circulating")]
        pts = pts[-400:]
        chains = []
        for ch, v in (j.get("currentChainBalances") or {}).items():
            cur = sum((v or {}).values())
            if cur > 0:
                chains.append({"chain": ch, "supply": cur})
        chains.sort(key=lambda c: -c["supply"])
        return {"id": id, "name": j.get("name"), "symbol": j.get("symbol"), "peg": peg, "mechanism": j.get("pegMechanism"), "url": j.get("url"),
                "description": (j.get("description") or "")[:600], "mint_redeem": (j.get("mintRedeemDescription") or "")[:600], "history": pts[-365:],
                "net": [[pts[i][0], pts[i][1] - pts[i - 1][1]] for i in range(1, len(pts))][-120:], "chains": chains[:12]}
    return cached(f"rwas:coin:{id}", 300, go)


# ------------------------------------------------------------------ crypto
def _cg(path: str, params: dict[str, Any] | None = None) -> Any:
    r = requests.get(f"https://api.coingecko.com/api/v3/{path}", params=params, headers=UA, timeout=15)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"CoinGecko returned {r.status_code}")
    return r.json()


def _crypto_markets() -> list[dict[str, Any]]:
    j = _cg("coins/markets", {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 100, "page": 1, "sparkline": "true", "price_change_percentage": "1h,24h,7d,30d"})
    return [{"id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"], "rank": c.get("market_cap_rank"), "price": c.get("current_price"), "mcap": c.get("market_cap"),
             "vol": c.get("total_volume"), "p1h": c.get("price_change_percentage_1h_in_currency"), "p24h": c.get("price_change_percentage_24h_in_currency"),
             "p7d": c.get("price_change_percentage_7d_in_currency"), "p30d": c.get("price_change_percentage_30d_in_currency"), "ath": c.get("ath"),
             "ath_pct": c.get("ath_change_percentage"), "spark": [round(x, 6) for x in ((c.get("sparkline_in_7d") or {}).get("price") or [])[::6]], "image": c.get("image")} for c in j]


@router.get("/crypto/markets")
def api_crypto_markets():
    return {"rows": cached("cg:markets", 120, _crypto_markets), "at": time.time()}


@router.get("/crypto/global")
def api_crypto_global():
    def go() -> dict[str, Any]:
        d = _cg("global")["data"]
        return {"mcap": (d.get("total_market_cap") or {}).get("usd"), "vol": (d.get("total_volume") or {}).get("usd"), "chg24": d.get("market_cap_change_percentage_24h_usd"),
                "btc_dom": (d.get("market_cap_percentage") or {}).get("btc"), "eth_dom": (d.get("market_cap_percentage") or {}).get("eth"), "coins": d.get("active_cryptocurrencies")}
    return cached("cg:global", 300, go)


@router.get("/crypto/coin")
def api_crypto_coin(symbol: str):
    sym = symbol.upper().replace("-USD", "")
    rows = cached("cg:markets", 120, _crypto_markets)
    hit = next((r for r in rows if r["symbol"] == sym), None)
    cid = hit["id"] if hit else None
    if not cid:
        found = (_cg("search", {"query": sym}).get("coins") or [])
        exact = [c for c in found if c.get("symbol", "").upper() == sym] or found
        if not exact:
            raise HTTPException(status_code=404, detail=f"no coin found for {sym}")
        cid = exact[0]["id"]

    def go() -> dict[str, Any]:
        j = _cg(f"coins/{cid}", {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false", "sparkline": "false"})
        md = j.get("market_data") or {}
        usd = lambda k: (md.get(k) or {}).get("usd")  # noqa: E731
        return {"id": cid, "symbol": j.get("symbol", "").upper(), "name": j.get("name"), "rank": j.get("market_cap_rank"), "description": re.sub(r"<[^>]+>", "", (j.get("description") or {}).get("en", ""))[:3000],
                "categories": [c for c in (j.get("categories") or []) if c][:8], "genesis": j.get("genesis_date"), "hashing": j.get("hashing_algorithm"),
                "links": {"website": next(iter((j.get("links") or {}).get("homepage") or []), None), "explorer": next(iter((j.get("links") or {}).get("blockchain_site") or []), None),
                          "twitter": (j.get("links") or {}).get("twitter_screen_name"), "reddit": (j.get("links") or {}).get("subreddit_url"),
                          "github": next(iter(((j.get("links") or {}).get("repos_url") or {}).get("github") or []), None)},
                "market": {"price": usd("current_price"), "mcap": usd("market_cap"), "fdv": usd("fully_diluted_valuation"), "vol": usd("total_volume"), "high24": usd("high_24h"), "low24": usd("low_24h"),
                           "ath": usd("ath"), "ath_pct": (md.get("ath_change_percentage") or {}).get("usd"), "ath_date": (md.get("ath_date") or {}).get("usd"), "atl": usd("atl"),
                           "atl_pct": (md.get("atl_change_percentage") or {}).get("usd"), "supply": md.get("circulating_supply"), "total_supply": md.get("total_supply"), "max_supply": md.get("max_supply"),
                           "p1h": (md.get("price_change_percentage_1h_in_currency") or {}).get("usd"), "p24h": md.get("price_change_percentage_24h"), "p7d": md.get("price_change_percentage_7d"),
                           "p30d": md.get("price_change_percentage_30d"), "p1y": md.get("price_change_percentage_1y")}}
    return cached(f"cg:coin:{cid}", 300, go)


# ------------------------------------------------------------------ macro (FRED)
MACRO = [  # id, label, unit, mode
    ("FEDFUNDS", "Fed funds rate", "%", "level"), ("DGS2", "US 2-year yield", "%", "level"), ("DGS10", "US 10-year yield", "%", "level"), ("DGS30", "US 30-year yield", "%", "level"),
    ("T10Y2Y", "10Y-2Y spread", "%", "level"), ("T10Y3M", "10Y-3M spread", "%", "level"), ("CPIAUCSL", "CPI (all items)", "% y/y", "yoy"), ("CPILFESL", "Core CPI", "% y/y", "yoy"),
    ("PCEPI", "PCE price index", "% y/y", "yoy"), ("UNRATE", "Unemployment rate", "%", "level"), ("PAYEMS", "Nonfarm payrolls", "K jobs", "level"), ("GDPC1", "Real GDP", "% y/y", "yoy"),
    ("INDPRO", "Industrial production", "% y/y", "yoy"), ("RSAFS", "Retail sales", "% y/y", "yoy"), ("UMCSENT", "Consumer sentiment", "index", "level"), ("M2SL", "M2 money supply", "% y/y", "yoy"),
    ("BAMLH0A0HYM2", "High-yield spread", "%", "level"), ("DCOILWTICO", "WTI crude", "$/bbl", "level"), ("DTWEXBGS", "Broad dollar index", "index", "level"), ("MORTGAGE30US", "30Y mortgage rate", "%", "level"),
]


def _fred(series: str) -> list[tuple[str, float]]:
    def go() -> list[tuple[str, float]]:
        r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", headers=UA, timeout=20)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"FRED returned {r.status_code}")
        out = []
        for row in list(csv.reader(io.StringIO(r.text)))[1:]:
            if len(row) == 2 and row[1] not in (".", ""):
                try:
                    out.append((row[0], float(row[1])))
                except ValueError:
                    pass
        return out
    return cached(f"fred:{series}", 3 * 3600, go)


def _macro_view(series: str, mode: str) -> list[tuple[str, float]]:
    data = _fred(series)
    if mode != "yoy":
        return data
    idx = {d: v for d, v in data}
    dates = [d for d, _ in data]
    out = []
    for d, v in data:
        y, m, day = d.split("-")
        prior = f"{int(y) - 1}-{m}-{day}"
        base = idx.get(prior)
        if base is None:  # quarterly / irregular: nearest earlier observation about a year back
            cand = [x for x in dates if x <= prior]
            base = idx[cand[-1]] if cand and (dt.date.fromisoformat(prior) - dt.date.fromisoformat(cand[-1])).days < 120 else None
        if base:
            out.append((d, (v / base - 1) * 100))
    return out


@router.get("/macro")
def api_macro():
    return cached("macro:summary", 900, _build_macro)


def _build_macro() -> dict[str, Any]:
    def one(m: tuple[str, str, str, str]) -> dict[str, Any] | None:
        sid, label, unit, mode = m
        try:
            v = _macro_view(sid, mode)
        except Exception:  # noqa: BLE001
            return None
        if len(v) < 2:
            return None
        return {"id": sid, "label": label, "unit": unit, "mode": mode, "date": v[-1][0], "value": v[-1][1], "prev": v[-2][1], "prev_date": v[-2][0],
                "year_ago": next((x[1] for x in reversed(v) if x[0] <= f"{int(v[-1][0][:4]) - 1}{v[-1][0][4:]}"), None), "spark": [round(x[1], 4) for x in v[-36:]]}
    rows = [r for r in _POOL.map(one, MACRO) if r]
    return {"rows": rows, "at": time.time()}


@router.get("/macro/series")
def api_macro_series(id: str, years: int = 10):
    meta = next((m for m in MACRO if m[0] == id), None)
    if not meta:
        raise HTTPException(status_code=404, detail="unknown series")
    v = _macro_view(meta[0], meta[3])
    cutoff = (dt.date.today() - dt.timedelta(days=365 * max(1, min(years, 60)))).isoformat()
    pts = [p for p in v if p[0] >= cutoff]
    if len(pts) > 1500:
        pts = pts[:: max(1, len(pts) // 1500)]
    return {"id": id, "label": meta[1], "unit": meta[2], "points": pts}


def warm() -> None:
    """Prime the Yahoo cookie/crumb and the pages people open first, so the first click is instant."""
    def run() -> None:
        _yahoo_crumb()
        for key, ttl, fn in (("mon:indices", 90, lambda: _build_monitor("indices")), ("mon:stocks", 90, lambda: _build_monitor("stocks")),
                             ("cg:markets", 120, _crypto_markets), ("mon:fx", 90, lambda: _build_monitor("fx")), ("macro:summary", 900, _build_macro)):
            try:
                cached(key, ttl, fn)
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=run, daemon=True, name="invest-warm").start()
