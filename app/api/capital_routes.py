"""CAPITAL page API: market tape, stock+crypto portfolio tracker, news wire, hot coins/stocks.

Data sources (all free, keyless):
  quotes/history  Yahoo chart endpoint (the same one app/data/yfinance_source.py uses - the yfinance
                  library's own quote calls are rejected by Yahoo right now, the chart endpoint is not)
  news            public RSS feeds (headlines + links only, each links back to its publisher)
  crypto trending CoinGecko public API

Everything is cached stale-while-revalidate: a request never waits for a refresh once there is data;
an expired entry is served immediately and refreshed in a background thread.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import email.utils
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from html import unescape as html_unescape
from typing import Any, Callable

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/api/capital")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_POOL = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="capital")
_FEED_POOL = cf.ThreadPoolExecutor(max_workers=36, thread_name_prefix="feeds")

# ------------------------------------------------------------------ stale-while-revalidate cache
_cache: dict[str, tuple[float, Any]] = {}
_inflight: set[str] = set()
_lock = threading.Lock()


def cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if hit:  # stale: serve it now, refresh in the background (one refresh at a time per key)
        with _lock:
            if key in _inflight:
                return hit[1]
            _inflight.add(key)

        def refresh() -> None:
            try:
                val = fn()
                with _lock:
                    _cache[key] = (time.time(), val)
            except Exception:  # noqa: BLE001 - keep serving the stale value
                pass
            finally:
                with _lock:
                    _inflight.discard(key)

        threading.Thread(target=refresh, daemon=True).start()
        return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


# ------------------------------------------------------------------ quotes
def _chart(symbol: str, rng: str = "1mo", interval: str = "1d") -> dict[str, Any] | None:
    """Price, previous close, recent closes (for a sparkline) and volume stats for one symbol."""
    try:
        r = requests.get(CHART_URL.format(symbol=requests.utils.quote(symbol)), params={"range": rng, "interval": interval},
                         headers=UA, timeout=10)
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        meta, q = res["meta"], res["indicators"]["quote"][0]
        stamps = res.get("timestamp") or []
        closes = [(t, c) for t, c in zip(stamps, q["close"]) if c is not None]
        vols = [v for v in (q.get("volume") or []) if v is not None]
        price = meta.get("regularMarketPrice") or (closes[-1][1] if closes else None)
        if price is None or not closes:
            return None
        mkt_day = dt.datetime.fromtimestamp(meta.get("regularMarketTime") or closes[-1][0], dt.timezone.utc).date()
        last_day = dt.datetime.fromtimestamp(closes[-1][0], dt.timezone.utc).date()
        # the newest bar is "today" while a session is open, so yesterday's close is the one before it
        prev = closes[-2][1] if len(closes) > 1 and last_day >= mkt_day else closes[-1][1]
        vol_today = vols[-1] if vols else 0
        prior = vols[-21:-1] if len(vols) > 1 else []
        return {
            "symbol": symbol, "price": float(price), "prev": float(prev),
            "spark": [round(c, 6) for _t, c in closes[-24:]],
            "vol": vol_today, "vol_avg": (sum(prior) / len(prior)) if prior else 0,
        }
    except Exception:  # noqa: BLE001
        return None


def _ext(symbol: str) -> dict[str, Any] | None:
    """Pre-market / after-hours move for a stock: {'state': 'PRE'|'POST'|'AH', 'price', 'pct'}. PRE is measured against the previous
    regular close (the usual "pre +4.5%"), POST / AH against today's regular close. None when there are no extended-hours bars."""
    try:
        r = requests.get(CHART_URL.format(symbol=requests.utils.quote(symbol)),
                         params={"range": "1d", "interval": "5m", "includePrePost": "true"}, headers=UA, timeout=10)
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        meta, q = res["meta"], res["indicators"]["quote"][0]
        bars = [(t, c) for t, c in zip(res.get("timestamp") or [], q["close"]) if c is not None]
        per = meta.get("currentTradingPeriod") or {}
        pre, post = per.get("pre") or {}, per.get("post") or {}
        if not bars or not pre or not post:
            return None
        t, c = bars[-1]
        now = time.time()
        if pre["start"] <= t < pre["end"]:
            ref, state = meta.get("chartPreviousClose") or meta.get("previousClose"), "PRE"
        elif post["start"] <= t < post["end"]:
            ref, state = meta.get("regularMarketPrice"), "POST" if post["start"] <= now < post["end"] else "AH"
        else:
            return None
        if state == "PRE" and not (pre["start"] <= now < pre["end"]):
            return None  # yesterday's pre-market is history once the session is open
        return {"state": state, "price": float(c), "pct": ((c / ref - 1) * 100.0) if ref else None, "at": t}
    except Exception:  # noqa: BLE001 - an extra, never a reason to break the portfolio
        return None


def _quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for sym, res in zip(symbols, _POOL.map(_chart, symbols)):
        if res:
            out[sym] = res
    return out


def _change(q: dict[str, Any]) -> tuple[float, float]:
    chg = q["price"] - q["prev"]
    return chg, (chg / q["prev"] * 100.0) if q["prev"] else 0.0


# ------------------------------------------------------------------ tape
TAPE = [
    ("^GSPC", "S&P 500", "IDX"), ("^NDX", "NASDAQ 100", "IDX"), ("^DJI", "DOW", "IDX"), ("^RUT", "RUSSELL 2K", "IDX"),
    ("^VIX", "VIX", "VOL"), ("ES=F", "ES FUT", "FUT"), ("NQ=F", "NQ FUT", "FUT"),
    ("BTC-USD", "BTC", "CRYPTO"), ("ETH-USD", "ETH", "CRYPTO"), ("SOL-USD", "SOL", "CRYPTO"), ("XRP-USD", "XRP", "CRYPTO"),
    ("GC=F", "GOLD", "CMDTY"), ("CL=F", "WTI OIL", "CMDTY"), ("SI=F", "SILVER", "CMDTY"),
    ("^TNX", "US 10Y", "RATE"), ("DX-Y.NYB", "DXY", "FX"), ("EURUSD=X", "EUR/USD", "FX"), ("JPY=X", "USD/JPY", "FX"),
]


def _build_tape() -> list[dict[str, Any]]:
    quotes = _quotes([s for s, _l, _g in TAPE])
    rows = []
    for sym, label, group in TAPE:
        q = quotes.get(sym)
        if not q:
            continue
        chg, pct = _change(q)
        rows.append({"sym": sym, "label": label, "group": group, "price": q["price"], "chg": chg, "pct": pct, "spark": q["spark"]})
    return rows


@router.get("/tape")
def get_tape():
    return {"items": cached("tape", 25, _build_tape), "at": time.time()}


# ------------------------------------------------------------------ portfolio (stocks + crypto)
class HoldingIn(BaseModel):
    kind: str  # "stock" | "crypto"
    symbol: str
    qty: float
    cost_basis: float = 0.0  # average cost per unit, USD


_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-=^]{1,15}$")


def _ensure_table() -> None:
    with db.get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS capital_holdings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL, "
            "cost_basis REAL NOT NULL DEFAULT 0, added_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )


def _yahoo_symbol(kind: str, symbol: str) -> str:
    s = symbol.upper()
    return s if (kind != "crypto" or "-" in s) else f"{s}-USD"


@router.get("/portfolio")
def get_portfolio():
    _ensure_table()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM capital_holdings ORDER BY id").fetchall()]
    quotes = _quotes(sorted({_yahoo_symbol(r["kind"], r["symbol"]) for r in rows})) if rows else {}
    stock_syms = sorted({_yahoo_symbol(r["kind"], r["symbol"]) for r in rows if r["kind"] != "crypto"})
    ext = {s: e for s, e in zip(stock_syms, _POOL.map(_ext, stock_syms)) if e}
    holdings, tv, tc, td = [], 0.0, 0.0, 0.0
    for r in rows:
        q = quotes.get(_yahoo_symbol(r["kind"], r["symbol"]))
        price = q["price"] if q else None
        value = price * r["qty"] if price is not None else None
        cost = r["cost_basis"] * r["qty"]
        day = (price - q["prev"]) * r["qty"] if q else 0.0
        holdings.append({
            "id": r["id"], "kind": r["kind"], "symbol": r["symbol"].upper(), "qty": r["qty"], "cost_basis": r["cost_basis"],
            "price": price, "value": value, "pnl": (value - cost) if value is not None else None,
            "pnl_pct": ((value - cost) / cost * 100.0) if (value is not None and cost) else None,
            "day_pct": _change(q)[1] if q else None, "day_pnl": day, "spark": q["spark"] if q else [],
            "ext": ext.get(_yahoo_symbol(r["kind"], r["symbol"])),
        })
        if value is not None:
            tv, tc, td = tv + value, tc + cost, td + day
    for h in holdings:
        h["weight"] = (h["value"] / tv * 100.0) if (tv and h["value"] is not None) else 0.0
    by_kind = {k: sum(h["value"] or 0.0 for h in holdings if h["kind"] == k) for k in ("stock", "crypto")}
    return {
        "holdings": holdings,
        "totals": {"value": tv, "cost": tc, "pnl": tv - tc, "pnl_pct": ((tv - tc) / tc * 100.0) if tc else 0.0,
                   "day_pnl": td, "day_pct": (td / (tv - td) * 100.0) if (tv - td) else 0.0, "by_kind": by_kind},
    }


@router.post("/holdings")
def add_holding(h: HoldingIn):
    kind, symbol = h.kind.lower(), h.symbol.strip()
    if kind not in ("stock", "crypto"):
        raise HTTPException(status_code=400, detail="kind must be 'stock' or 'crypto'")
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    if not (h.qty > 0 and math.isfinite(h.qty)) or h.cost_basis < 0 or not math.isfinite(h.cost_basis):
        raise HTTPException(status_code=400, detail="quantity must be > 0 and cost basis >= 0")
    if not _chart(_yahoo_symbol(kind, symbol), "5d"):
        raise HTTPException(status_code=400, detail=f"no market data found for {symbol.upper()}")
    _ensure_table()
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO capital_holdings (kind, symbol, qty, cost_basis) VALUES (?, ?, ?, ?)",
                           (kind, symbol.upper(), h.qty, h.cost_basis))
        return {"id": cur.lastrowid}


@router.delete("/holdings/{holding_id}")
def delete_holding(holding_id: int):
    _ensure_table()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM capital_holdings WHERE id = ?", (holding_id,))
    return {"ok": True}


# ------------------------------------------------------------------ news wire
FEEDS = [
    ("CNBC", "MKT", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MARKETWATCH", "MKT", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("YAHOO FIN", "MKT", "https://finance.yahoo.com/news/rssindex"),
    ("COINDESK", "CRYPTO", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("COINTELEGRAPH", "CRYPTO", "https://cointelegraph.com/rss"),
    ("DECRYPT", "CRYPTO", "https://decrypt.co/feed"),
    ("BLOOMBERG", "MKT", "https://feeds.bloomberg.com/markets/news.rss"),
    ("FT", "MKT", "https://www.ft.com/markets?format=rss"),
    ("NASDAQ", "MKT", "https://www.nasdaq.com/feed/rssoutbound?category=Markets"),
    ("SEEKING ALPHA", "MKT", "https://seekingalpha.com/market_currents.xml"),
    ("GOOGLE NEWS", "MKT", "https://news.google.com/rss/search?q=stock+market+OR+federal+reserve+OR+tariffs+OR+oil+prices+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("CNBC ECONOMY", "MACRO", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("INVESTINGLIVE", "FX", "https://investinglive.com/feed/news"),
    ("FXSTREET", "FX", "https://www.fxstreet.com/rss/news"),
    ("FED", "CB", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB", "CB", "https://www.ecb.europa.eu/rss/press.html"),
    ("BANK OF ENGLAND", "CB", "https://www.bankofengland.co.uk/rss/news"),
    ("WSJ", "MKT", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    ("MW BULLETINS", "MKT", "https://feeds.content.dowjones.io/public/rss/mw_bulletins"),
    ("CNBC EARNINGS", "EARN", "https://www.cnbc.com/id/15839135/device/rss/rss.html"),
    ("CNBC TECH", "MKT", "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
    ("CNBC FINANCE", "MKT", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("CNBC WORLD", "MACRO", "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
    ("INVESTING.COM", "MKT", "https://www.investing.com/rss/news_25.rss"),
    ("INVESTING ECON", "MACRO", "https://www.investing.com/rss/news_14.rss"),
    ("INVESTING CMDTY", "MACRO", "https://www.investing.com/rss/news_11.rss"),
    ("OILPRICE", "MACRO", "https://oilprice.com/rss/main"),
    ("NYT BUSINESS", "MKT", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("GUARDIAN BIZ", "MACRO", "https://www.theguardian.com/uk/business/rss"),
    ("THE BLOCK", "CRYPTO", "https://www.theblock.co/rss.xml"),
    # company reports as they go public: press releases (earnings, guidance, deals) and SEC 8-K current reports
    ("PR NEWSWIRE", "PRESS", "https://www.prnewswire.com/rss/news-releases-list.rss"),
    ("SEC 8-K", "FILING", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom"),
]
# 8-K item numbers that tell what a filing is about (2.02 = the quarterly results)
SEC_ITEMS = {"1.01": "material agreement", "1.03": "bankruptcy", "2.01": "acquisition / disposal", "2.02": "RESULTS (earnings)", "2.05": "restructuring",
             "2.06": "impairment", "3.01": "delisting notice", "4.02": "restatement", "5.02": "executive change", "7.01": "Reg FD disclosure", "8.01": "other event"}
_ATOM = "{http://www.w3.org/2005/Atom}"


PER_SOURCE = 14  # cap per publisher so one chatty feed (Yahoo posts 50 at once) cannot bury the others


def _when(s: str | None) -> float:
    """RSS dates are RFC 822; Atom and some publishers (Investing.com) use ISO 8601. Unknown -> now."""
    s = (s or "").strip()
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except Exception:  # noqa: BLE001
        pass
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
        return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()
    except ValueError:
        return time.time()


def _strip(html: str | None) -> str:
    return html_unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip())


def _sec_entry(title: str, summary: str) -> str:
    """'8-K - Liberty Energy Inc. (0001694028) (Filer)' + items -> '8-K · Liberty Energy Inc. · RESULTS (earnings), exec change'."""
    name = re.sub(r"\s*\(\d{10}\)\s*\((Filer|Subject)\)\s*$", "", re.sub(r"^\S+\s+-\s+", "", title)).strip()
    items = [SEC_ITEMS.get(m, f"item {m}") for m in re.findall(r"Item (\d\.\d\d)", summary)]
    return f"8-K · {name}" + (f" · {', '.join(dict.fromkeys(items))}" if items else "")


_FEED_LAST: dict[str, tuple[float, list[dict[str, Any]]]] = {}  # url -> (when, last good batch)


def _parse_feed(source: str, tag: str, url: str) -> list[dict[str, Any]]:
    """One feed, with its last good batch (up to 2 h old) standing in when the publisher is slow or down this time."""
    try:
        items = _parse_feed_once(source, tag, url)
    except Exception:  # noqa: BLE001
        items = []
    if items:
        _FEED_LAST[url] = (time.time(), items)
        return items
    last = _FEED_LAST.get(url)
    return last[1] if last and time.time() - last[0] < 7200 else []


def _parse_feed_once(source: str, tag: str, url: str) -> list[dict[str, Any]]:
    from app.api.invest_routes import SEC_UA  # SEC asks every caller to identify itself
    r = requests.get(url, headers=SEC_UA if "sec.gov" in url else UA, timeout=15)
    root = ET.fromstring(r.content)
    items = []
    entries = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
    for it in entries[:PER_SOURCE]:
        if it.tag.startswith(_ATOM):  # Atom (SEC EDGAR)
            title = (it.findtext(f"{_ATOM}title") or "").strip()
            ln = it.find(f"{_ATOM}link")
            link = (ln.get("href") if ln is not None else "") or ""
            summary = _strip(it.findtext(f"{_ATOM}summary"))
            ts = _when(it.findtext(f"{_ATOM}updated"))
            if "sec.gov" in url:
                title = _sec_entry(title, summary)
        else:
            title = re.sub(r"\s+", " ", (it.findtext("title") or "")).strip()
            link = (it.findtext("link") or "").strip()
            summary = _strip(it.findtext("description"))
            ts = _when(it.findtext("pubDate"))
        if not title or not link.startswith(("http://", "https://")):
            continue
        # the feed's own summary: the article reader falls back on it when a publisher blocks the full text
        items.append({"title": title, "link": link, "source": source, "tag": tag, "ts": min(ts, time.time()), "desc": summary[:600]})
    return items


def _build_news() -> list[dict[str, Any]]:
    batches = list(_FEED_POOL.map(lambda f: _parse_feed(*f), FEEDS))  # every publisher at once (I/O bound)
    scored: list[tuple[float, dict[str, Any]]] = []
    for batch in batches:
        # newest first within a publisher; each step down costs 15 minutes of "sort time", so a feed that
        # posts 14 items at the same instant is interleaved with the others instead of filling the top
        for rank, it in enumerate(sorted(batch, key=lambda i: i["ts"], reverse=True)):
            scored.append((it["ts"] - rank * 900.0, it))
    seen, out = set(), []
    for _k, it in sorted(scored, key=lambda x: x[0], reverse=True):
        key = re.sub(r"\W+", "", it["title"].lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out[:320]


@router.get("/news")
def get_news():
    return {"items": cached("news", 240, _build_news), "at": time.time()}


# ------------------------------------------------------------------ hot stocks / hot coins
STOCK_UNIVERSE = (
    "NVDA TSLA AAPL MSFT AMZN META GOOGL AMD PLTR COIN MSTR HOOD SOFI GME AMC RIVN SMCI ARM AVGO NFLX BA INTC MU "
    "UBER SHOP CRWD DKNG AFRM MARA RIOT IONQ RKLB F BAC JPM XOM LLY"
).split()


def _build_hot_stocks() -> dict[str, list[dict[str, Any]]]:
    """Ranked by size of the move. Volume is shown in absolute terms - a volume-vs-average ratio is
    misleading mid-session (today's bar is partial, the average is a full day)."""
    quotes = _quotes(list(STOCK_UNIVERSE))
    rows = []
    for sym, q in quotes.items():
        chg, pct = _change(q)
        rows.append({"sym": sym, "price": q["price"], "chg": chg, "pct": pct, "vol": q["vol"], "spark": q["spark"]})
    return {"hot": sorted(rows, key=lambda r: abs(r["pct"]), reverse=True)[:12],
            "gainers": sorted(rows, key=lambda r: r["pct"], reverse=True)[:12],
            "losers": sorted(rows, key=lambda r: r["pct"])[:12]}


def _coin_row(c: dict[str, Any], trending: bool = False) -> dict[str, Any]:
    return {"sym": str(c.get("symbol", "")).upper(), "name": c.get("name", ""), "price": c.get("current_price"),
            "pct": c.get("price_change_percentage_24h_in_currency") or c.get("price_change_percentage_24h") or 0.0,
            "pct1h": c.get("price_change_percentage_1h_in_currency"), "vol": c.get("total_volume"),
            "mcap": c.get("market_cap"), "rank": c.get("market_cap_rank"), "trending": trending}


def _build_hot_coins() -> dict[str, list[dict[str, Any]]]:
    base = "https://api.coingecko.com/api/v3"
    markets = requests.get(f"{base}/coins/markets", headers=UA, timeout=12, params={
        "vs_currency": "usd", "order": "market_cap_desc", "per_page": 100, "page": 1,
        "price_change_percentage": "1h,24h"}).json()
    if not isinstance(markets, list):
        raise RuntimeError("coingecko markets unavailable")
    by_id = {c["id"]: c for c in markets}
    trending_rows: list[dict[str, Any]] = []
    try:
        tr = requests.get(f"{base}/search/trending", headers=UA, timeout=12).json()
        for entry in tr.get("coins", [])[:12]:
            it = entry["item"]
            c = by_id.get(it["id"])
            if c:
                trending_rows.append(_coin_row(c, True))
                continue
            d = it.get("data") or {}
            price = d.get("price")
            trending_rows.append({"sym": str(it.get("symbol", "")).upper(), "name": it.get("name", ""),
                                  "price": price if isinstance(price, (int, float)) else None,
                                  "pct": (d.get("price_change_percentage_24h") or {}).get("usd") or 0.0, "pct1h": None,
                                  "vol": None, "mcap": None, "rank": it.get("market_cap_rank"), "trending": True})
    except Exception:  # noqa: BLE001 - trending is a bonus list
        pass
    liquid = [c for c in markets if (c.get("total_volume") or 0) >= 20_000_000]
    ranked = sorted(liquid, key=lambda c: c.get("price_change_percentage_24h") or 0.0, reverse=True)
    return {"trending": trending_rows[:12] or [_coin_row(c) for c in ranked[:12]],
            "gainers": [_coin_row(c) for c in ranked[:12]], "losers": [_coin_row(c) for c in ranked[::-1][:12]]}


def _build_hot_polymarket() -> list[dict[str, Any]]:
    """Currently-active 5-minute Up-or-Down series, ranked by 24h volume — Gamma
    doesn't hand back a clean 24h % change the way Yahoo/CoinGecko do (a 5-minute
    session resets every 5 minutes, "24h change" isn't a meaningful concept for
    it), so this is honestly volume-ranked only, never a fabricated % figure."""
    from app.polymarket import gamma

    series = gamma.discover_5m_series()
    return [{"sym": s["asset"].upper(), "slug": s["slug"], "vol24hr": s["volume24hr"]} for s in series[:12]]


@router.get("/hot")
def get_hot():
    def safe(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
        try:
            return cached(key, ttl, fn)
        except Exception:  # noqa: BLE001
            return None

    stocks_f = _POOL.submit(safe, "hot_stocks", 120, _build_hot_stocks)
    coins_f = _POOL.submit(safe, "hot_coins", 150, _build_hot_coins)
    pm_f = _POOL.submit(safe, "hot_polymarket", 60, _build_hot_polymarket)
    return {"stocks": stocks_f.result(), "coins": coins_f.result(), "polymarket": pm_f.result(), "at": time.time()}


def warm() -> None:
    """Fill the caches in the background at startup so the first page load is instant."""
    def run() -> None:
        for key, ttl, fn in (("tape", 25, _build_tape), ("news", 240, _build_news),
                             ("hot_coins", 150, _build_hot_coins), ("hot_stocks", 120, _build_hot_stocks)):
            try:
                cached(key, ttl, fn)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=run, daemon=True, name="capital-warm").start()
