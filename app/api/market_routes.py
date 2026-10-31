"""MARKET ANALYTICS API: economic calendar (ECO), earnings calendar (EVTS), yield curve (GC), fed-funds-implied rate path
(WIRP), CFTC positioning (COT), seasonality (SEAS), relative performance (COMP), correlation (CORR), volatility surface /
skew / IV rank / unusual options (OVDV), equity screener (EQS), heatmap (IMAP) and portfolio risk (PORT).

Sources (all public, keyless): Yahoo Finance, FRED, Nasdaq earnings calendar, ForexFactory weekly calendar feed,
CFTC public reporting (Socrata). Everything is cached stale-while-revalidate.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import re
import statistics
import threading
import time
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app import db
from app.api import invest_routes as inv
from app.api.capital_routes import _POOL, UA, _yahoo_symbol, cached

router = APIRouter(prefix="/api/market")
NASDAQ_H = {**UA, "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}


def _num(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("$", "").replace(",", "").replace("%", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


# ================================================================== economic calendar (ECO)
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_ECO_SCHEMA = ("CREATE TABLE IF NOT EXISTS econ_events (ts INTEGER NOT NULL, title TEXT NOT NULL, country TEXT NOT NULL, impact TEXT, forecast TEXT, "
               "previous TEXT, actual TEXT, PRIMARY KEY (ts, title, country))")
_eco_lock = threading.Lock()


def refresh_calendar() -> int:
    """Pull this week's feed and merge it into the DB, so weeks accumulate into a history."""
    r = requests.get(FF_URL, headers=UA, timeout=15)
    j = r.json()
    if not isinstance(j, list):
        raise RuntimeError("calendar feed unavailable")
    n = 0
    with _eco_lock, db.get_conn() as conn:
        conn.execute(_ECO_SCHEMA)
        for x in j:
            try:
                ts = int(dt.datetime.fromisoformat(x["date"]).timestamp())
            except (KeyError, ValueError):
                continue
            conn.execute("INSERT INTO econ_events (ts, title, country, impact, forecast, previous, actual) VALUES (?,?,?,?,?,?,?) "
                         "ON CONFLICT(ts, title, country) DO UPDATE SET impact=excluded.impact, forecast=excluded.forecast, previous=excluded.previous, "
                         "actual=COALESCE(NULLIF(excluded.actual, ''), econ_events.actual)",
                         (ts, x.get("title", ""), x.get("country", ""), x.get("impact", ""), x.get("forecast", ""), x.get("previous", ""), x.get("actual", "") or ""))
            n += 1
    return n


def _fred_actual(title: str, ts: int) -> str | None:
    """Best-effort actual for headline US releases, from FRED, only when the observation is the release's reference period."""
    when = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
    ref = (when.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    t = title.lower()
    spec = None
    if "cpi" in t and "core" in t:
        spec = ("CPILFESL", "mom" if "m/m" in t else "yoy" if "y/y" in t else None)
    elif "cpi" in t:
        spec = ("CPIAUCSL", "mom" if "m/m" in t else "yoy" if "y/y" in t else None)
    elif "non-farm" in t:
        spec = ("PAYEMS", "diff")
    elif "unemployment rate" in t:
        spec = ("UNRATE", "level")
    elif "retail sales" in t and "core" not in t and "m/m" in t:
        spec = ("RSAFS", "mom")
    if not spec or not spec[1]:
        return None
    try:
        data = inv._fred(spec[0])
    except Exception:  # noqa: BLE001
        return None
    idx = {d[:7]: v for d, v in data}
    if ref not in idx:
        return None
    keys = sorted(idx)
    i = keys.index(ref)
    v = idx[ref]
    if spec[1] == "level":
        return f"{v:.1f}%"
    if spec[1] == "diff" and i:
        return f"{v - idx[keys[i - 1]]:+,.0f}K"
    if spec[1] == "mom" and i:
        return f"{(v / idx[keys[i - 1]] - 1) * 100:+.1f}%"
    if spec[1] == "yoy":
        y, m = ref.split("-")
        prior = idx.get(f"{int(y) - 1}-{m}")
        return f"{(v / prior - 1) * 100:.1f}%" if prior else None
    return None


@router.get("/calendar")
def api_calendar(back: int = 1, ahead: int = 14):
    try:
        cached("eco:refresh", 900, refresh_calendar)
    except Exception:  # noqa: BLE001 - serve whatever is stored
        pass
    now = time.time()
    lo, hi = now - back * 86400, now + ahead * 86400
    with db.get_conn() as conn:
        conn.execute(_ECO_SCHEMA)
        rows = [dict(r) for r in conn.execute("SELECT * FROM econ_events WHERE ts BETWEEN ? AND ? ORDER BY ts", (int(lo), int(hi))).fetchall()]
    for r in rows:
        if not r.get("actual") and r["ts"] < now and r["country"] == "USD":
            r["actual"] = _fred_actual(r["title"], r["ts"]) or ""
            r["actual_src"] = "FRED" if r["actual"] else ""
    return {"events": rows, "now": now, "note": "ForexFactory weekly feed; earlier weeks accumulate as the app runs. Actuals shown where FRED has the reference period."}


# ================================================================== earnings calendar (EVTS)
def _earn_day(day: str) -> list[dict[str, Any]]:
    def go() -> list[dict[str, Any]]:
        r = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": day}, headers=NASDAQ_H, timeout=15)
        rows = ((r.json().get("data") or {}).get("rows")) or []
        out = []
        for x in rows:
            out.append({"symbol": x.get("symbol"), "name": x.get("name"), "when": {"time-pre-market": "BMO", "time-after-hours": "AMC"}.get(x.get("time"), "—"),
                        "eps_est": _num(x.get("epsForecast")), "n_est": int(_num(x.get("noOfEsts")) or 0), "eps_last_year": _num(x.get("lastYearEPS")),
                        "mcap": _num(x.get("marketCap")), "fq": x.get("fiscalQuarterEnding"), "eps": _num(x.get("eps")), "surprise": _num(x.get("surprise"))})
        return out
    past = day < dt.date.today().isoformat()
    return cached(f"earn:{day}", 6 * 3600 if past else 1800, go)


@router.get("/earnings")
def api_earnings(start: str | None = None, days: int = 7, min_mcap: float = 3e9):
    d0 = dt.date.fromisoformat(start) if start else dt.date.today()
    dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(max(1, min(days, 14)))]
    dates = [d for d in dates if dt.date.fromisoformat(d).weekday() < 5]
    out = []
    for day, rows in zip(dates, _POOL.map(lambda d: _safe(lambda: _earn_day(d), []), dates)):
        big = sorted((r for r in rows if (r["mcap"] or 0) >= min_mcap), key=lambda r: r["mcap"] or 0, reverse=True)
        out.append({"date": day, "total": len(rows), "rows": big[:60]})
    return {"days": out}


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


# ================================================================== yield curve (GC) + implied Fed path (WIRP)
CURVE = [("1M", "DGS1MO", 1 / 12), ("3M", "DGS3MO", 0.25), ("6M", "DGS6MO", 0.5), ("1Y", "DGS1", 1), ("2Y", "DGS2", 2), ("3Y", "DGS3", 3),
         ("5Y", "DGS5", 5), ("7Y", "DGS7", 7), ("10Y", "DGS10", 10), ("20Y", "DGS20", 20), ("30Y", "DGS30", 30)]


def _at(series: list[tuple[str, float]], date: str) -> float | None:
    v = None
    for d, x in series:
        if d > date:
            break
        v = x
    return v


@router.get("/curve")
def api_curve():
    def go() -> dict[str, Any]:
        data = list(_POOL.map(lambda c: _safe(lambda: inv._fred(c[1]), []), CURVE))
        last = max((s[-1][0] for s in data if s), default=None)
        if not last:
            raise HTTPException(status_code=502, detail="FRED unavailable")
        d0 = dt.date.fromisoformat(last)
        marks = {"now": last, "1w": (d0 - dt.timedelta(days=7)).isoformat(), "1m": (d0 - dt.timedelta(days=30)).isoformat(),
                 "3m": (d0 - dt.timedelta(days=91)).isoformat(), "1y": (d0 - dt.timedelta(days=365)).isoformat()}
        curves = {k: [(_at(s, d) if s else None) for s in data] for k, d in marks.items()}
        spread = {}
        for sid, label in (("T10Y2Y", "2s10s"), ("T10Y3M", "3m10y")):
            s = _safe(lambda: inv._fred(sid), [])
            spread[label] = {"now": s[-1][1] if s else None, "1m": _at(s, marks["1m"]), "1y": _at(s, marks["1y"]), "series": s[-520:][::5]}
        return {"tenors": [c[0] for c in CURVE], "years": [c[2] for c in CURVE], "marks": marks, "curves": curves, "spreads": spread}
    return cached("curve", 3600, go)


FOMC = ["2026-10-28", "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-16", "2027-07-28", "2027-09-22", "2027-11-03", "2027-12-15"]
MONTH_CODE = "FGHJKMNQUVXZ"


def _zq_price(year: int, month: int) -> float | None:
    sym = f"ZQ{MONTH_CODE[month - 1]}{str(year)[2:]}.CBT"
    h = inv._safe_history(sym, "5d", "1d")
    if not h:
        return None
    return h["meta"].get("regularMarketPrice") or (h["candles"][-1][4] if h["candles"] else None)


@router.get("/wirp")
def api_wirp():
    def go() -> dict[str, Any]:
        eff = _safe(lambda: inv._fred("DFF")[-1][1], None) or _safe(lambda: inv._fred("FEDFUNDS")[-1][1], None)
        if eff is None:
            raise HTTPException(status_code=502, detail="effective fed funds rate unavailable")
        today = dt.date.today()
        months = [(today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1) for k in range(0, 18)]
        prices = dict(zip(months, _POOL.map(lambda ym: _safe(lambda: _zq_price(*ym), None), months)))
        implied = {ym: (100 - p) for ym, p in prices.items() if p}
        rows, prev = [], eff
        for m in FOMC:
            d = dt.date.fromisoformat(m)
            if d <= today:
                continue
            ym = (d.year, d.month)
            if ym not in implied:
                continue
            n = (dt.date(d.year + (d.month // 12), d.month % 12 + 1, 1) - dt.timedelta(days=1)).day
            eff_day = d.day + 1
            remaining = n - eff_day + 1
            if remaining <= 3:  # decision lands at month end: the next month's contract is the clean read
                nxt = (d.year + (d.month // 12), d.month % 12 + 1)
                post = implied.get(nxt, implied[ym])
            else:
                post = (implied[ym] * n - prev * (eff_day - 1)) / remaining
            delta = post - prev
            moves = delta / 0.25
            rows.append({"date": m, "implied_avg": round(implied[ym], 3), "post_rate": round(post, 3), "delta_bp": round(delta * 100, 1), "exp_moves": round(moves, 2),
                         "p_hike": round(min(1, max(0, moves)), 3), "p_cut": round(min(1, max(0, -moves)), 3), "p_hold": round(max(0, 1 - abs(moves)), 3)})
            prev = post
        return {"effective": eff, "meetings": rows, "note": "Estimated from CBOT 30-day fed funds futures (25 bp steps assumed). Not CME's official FedWatch."}
    return cached("wirp", 900, go)


# ================================================================== COT (CFTC)
COT = {
    "NQ": ("gpe5-46if", "NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE", "Nasdaq-100 E-mini", "tff"),
    "ES": ("gpe5-46if", "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", "S&P 500 E-mini", "tff"),
    "GOLD": ("72hh-3qpy", "GOLD - COMMODITY EXCHANGE INC.", "Gold", "disagg"),
    "OIL": ("72hh-3qpy", "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE", "WTI crude oil", "disagg"),
    "SILVER": ("72hh-3qpy", "SILVER - COMMODITY EXCHANGE INC.", "Silver", "disagg"),
    "10Y": ("gpe5-46if", "UST 10Y NOTE - CHICAGO BOARD OF TRADE", "10-year T-note", "tff"),
}
COT_GROUPS = {
    "tff": [("Dealers", "dealer_positions_long_all", "dealer_positions_short_all"), ("Asset managers", "asset_mgr_positions_long", "asset_mgr_positions_short"),
            ("Leveraged funds", "lev_money_positions_long", "lev_money_positions_short"), ("Other reportables", "other_rept_positions_long", "other_rept_positions_short"),
            ("Non-reportable", "nonrept_positions_long_all", "nonrept_positions_short_all")],
    "disagg": [("Producers/merchants", "prod_merc_positions_long", "prod_merc_positions_short"), ("Swap dealers", "swap_positions_long_all", "swap__positions_short_all"),
               ("Managed money", "m_money_positions_long_all", "m_money_positions_short_all"), ("Other reportables", "other_rept_positions_long", "other_rept_positions_short"),
               ("Non-reportable", "nonrept_positions_long_all", "nonrept_positions_short_all")],
}


@router.get("/cot")
def api_cot(market: str = "NQ"):
    if market not in COT:
        raise HTTPException(status_code=404, detail="unknown market")
    ds, name, label, kind = COT[market]

    def go() -> dict[str, Any]:
        r = requests.get(f"https://publicreporting.cftc.gov/resource/{ds}.json", headers=UA, timeout=30,
                         params={"$where": f"market_and_exchange_names='{name}'", "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 156})
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            raise HTTPException(status_code=502, detail="CFTC returned no rows for this market")
        weeks = []
        for x in reversed(rows):
            g = {}
            for label_, lo, sh in COT_GROUPS[kind]:
                lng, srt = _num(x.get(lo)), _num(x.get(sh))
                if lng is None and lo == "swap_positions_long_all":
                    continue
                g[label_] = {"long": lng, "short": srt, "net": (lng - srt) if (lng is not None and srt is not None) else None}
            weeks.append({"date": x["report_date_as_yyyy_mm_dd"][:10], "oi": _num(x.get("open_interest_all")), "groups": g})
        return {"market": market, "label": label, "kind": kind, "weeks": weeks, "source": "CFTC Commitments of Traders (futures only)"}
    return cached(f"cot:{market}", 3600, go)


# ================================================================== seasonality, compare, correlation
def _closes(symbol: str, rng: str, itv: str) -> list[tuple[int, float]]:
    h = inv._safe_history(symbol, rng, itv)
    if not h:
        raise HTTPException(status_code=404, detail=f"no data for {symbol}")
    return [(c[0], c[4]) for c in h["candles"]]


@router.get("/seasonality")
def api_seasonality(symbol: str):
    def go() -> dict[str, Any]:
        monthly = _closes(symbol, "max", "1mo")
        by_month: dict[int, list[float]] = {m: [] for m in range(1, 13)}
        for (t0, c0), (t1, c1) in zip(monthly, monthly[1:]):
            m = dt.datetime.fromtimestamp(t1, dt.timezone.utc).month
            if c0:
                by_month[m].append((c1 / c0 - 1) * 100)
        daily = _closes(symbol, "5y", "1d")
        by_wd: dict[int, list[float]] = {i: [] for i in range(5)}
        for (t0, c0), (t1, c1) in zip(daily, daily[1:]):
            wd = dt.datetime.fromtimestamp(t1, dt.timezone.utc).weekday()
            if wd < 5 and c0:
                by_wd[wd].append((c1 / c0 - 1) * 100)
        agg = lambda xs: {"avg": statistics.mean(xs) if xs else None, "median": statistics.median(xs) if xs else None, "hit": (sum(1 for v in xs if v > 0) / len(xs) * 100) if xs else None,  # noqa: E731
                          "n": len(xs), "best": max(xs) if xs else None, "worst": min(xs) if xs else None}
        return {"symbol": symbol, "months": [{"m": m, **agg(by_month[m])} for m in range(1, 13)],
                "weekdays": [{"d": ["Mon", "Tue", "Wed", "Thu", "Fri"][i], **agg(by_wd[i])} for i in range(5)], "years": len(monthly) // 12}
    return cached(f"seas:{symbol}", 6 * 3600, go)


COMP_RANGES = {"1M": ("1mo", "1d"), "3M": ("3mo", "1d"), "6M": ("6mo", "1d"), "YTD": ("ytd", "1d"), "1Y": ("1y", "1d"), "3Y": ("5y", "1wk"), "5Y": ("5y", "1wk")}


def _aligned(symbols: list[str], rng: str) -> tuple[list[str], dict[str, list[float]]]:
    r, itv = COMP_RANGES.get(rng, COMP_RANGES["1Y"])
    series = {}
    for s, h in zip(symbols, _POOL.map(lambda x: _safe(lambda: _closes(x, r, itv), None), symbols)):
        if h:
            series[s] = {dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d"): c for t, c in h}
    if not series:
        raise HTTPException(status_code=404, detail="no data for any symbol")
    dates = sorted(set().union(*[set(v) for v in series.values()]))
    out: dict[str, list[float]] = {}
    for s, m in series.items():
        last, vals = None, []
        for d in dates:
            last = m.get(d, last)
            vals.append(last)
        out[s] = vals
    start = next(i for i in range(len(dates)) if all(v[i] is not None for v in out.values()))
    return dates[start:], {s: v[start:] for s, v in out.items()}


def _split_syms(s: str, cap: int = 12) -> list[str]:
    return [x.strip() for x in s.split(",") if re.fullmatch(r"[\w.\-=^]{1,20}", x.strip())][:cap]


@router.get("/compare")
def api_compare(symbols: str, rng: str = Query("1Y", alias="range")):
    syms = _split_syms(symbols, 8)
    dates, vals = _aligned(syms, rng)
    res = {}
    for s, v in vals.items():
        rets = [(v[i] / v[i - 1] - 1) for i in range(1, len(v)) if v[i - 1]]
        peak, dd = v[0], 0.0
        for x in v:
            peak = max(peak, x)
            dd = min(dd, x / peak - 1)
        res[s] = {"series": [round(x / v[0] * 100, 3) for x in v], "total": (v[-1] / v[0] - 1) * 100,
                  "vol": (statistics.pstdev(rets) * math.sqrt(252) * 100) if len(rets) > 2 else None, "maxdd": dd * 100}
    return {"dates": dates, "series": res, "range": rng}


@router.get("/correlation")
def api_correlation(symbols: str, rng: str = Query("1Y", alias="range")):
    syms = _split_syms(symbols, 16)
    dates, vals = _aligned(syms, rng)
    keys = list(vals)
    rets = {k: [(vals[k][i] / vals[k][i - 1] - 1) for i in range(1, len(dates))] for k in keys}

    def corr(a: list[float], b: list[float]) -> float | None:
        if len(a) < 5:
            return None
        ma, mb = statistics.mean(a), statistics.mean(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
        return cov / math.sqrt(va * vb) if va and vb else None

    matrix = [[(1.0 if i == j else corr(rets[a], rets[b])) for j, b in enumerate(keys)] for i, a in enumerate(keys)]
    pairs = sorted(((matrix[i][j], keys[i], keys[j]) for i in range(len(keys)) for j in range(i + 1, len(keys)) if matrix[i][j] is not None), key=lambda p: p[0])
    return {"symbols": keys, "matrix": matrix, "observations": len(dates) - 1, "lowest": pairs[:3], "highest": pairs[-3:][::-1]}


# ================================================================== options analytics (OVDV) + unusual activity
def _chain_raw(symbol: str, date: int | None = None) -> dict[str, Any]:
    params = {"date": date} if date else {}
    j = cached(f"opt:{symbol}:{date}", 300, lambda: inv._yget(f"{inv.Y2}/v7/finance/options/{symbol}", params))
    return ((j.get("optionChain") or {}).get("result") or [{}])[0]


def _interp(points: list[tuple[float, float]], x: float) -> float | None:
    pts = sorted(points)
    if not pts:
        return None
    if x <= pts[0][0]:
        return pts[0][1] if pts[0][0] - x < 0.08 else None
    if x >= pts[-1][0]:
        return pts[-1][1] if x - pts[-1][0] < 0.08 else None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * ((x - x0) / (x1 - x0) if x1 != x0 else 0)
    return None


def _hv(closes: list[float], n: int) -> float | None:
    r = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] and closes[i]]
    return statistics.pstdev(r[-n:]) * math.sqrt(252) * 100 if len(r) >= n else None


@router.get("/options/vol")
def api_options_vol(symbol: str):
    def go() -> dict[str, Any]:
        base = _chain_raw(symbol)
        spot = (base.get("quote") or {}).get("regularMarketPrice")
        window = [e for e in (base.get("expirationDates") or []) if 2 * 86400 < e - time.time() < 200 * 86400]
        # a few near-dated expiries, then the ones closest to 30/45/60/90/120 days so the term structure and 30-day IV are real
        picks = set(window[:3])
        for target in (30, 45, 60, 90, 120):
            if window:
                picks.add(min(window, key=lambda e, t=target: abs((e - time.time()) / 86400 - t)))
        exps = sorted(picks)[:8]
        if not spot or not exps:
            raise HTTPException(status_code=404, detail="no listed options for this security")
        chains = list(_POOL.map(lambda e: _safe(lambda: _chain_raw(symbol, e), None), exps))
        grid_m = [round(0.80 + 0.05 * i, 2) for i in range(9)]
        exp_rows, surface, pc_vol, pc_oi = [], [], [0, 0], [0, 0]
        for e, ch in zip(exps, chains):
            if not ch or not ch.get("options"):
                continue
            o = ch["options"][0]
            pts, calls_v, puts_v = [], 0, 0
            for kind, rows in (("c", o.get("calls", [])), ("p", o.get("puts", []))):
                for x in rows:
                    k, iv = x.get("strike"), x.get("impliedVolatility")
                    v, oi = x.get("volume") or 0, x.get("openInterest") or 0
                    if kind == "c":
                        pc_vol[0] += v; pc_oi[0] += oi
                    else:
                        pc_vol[1] += v; pc_oi[1] += oi
                    if k and iv and iv > 0.02 and ((kind == "p" and k < spot) or (kind == "c" and k >= spot)) and (x.get("bid") or 0) > 0:
                        pts.append((k / spot, iv * 100))
            if len(pts) < 4:
                continue
            days = (e - time.time()) / 86400
            atm = _interp(pts, 1.0)
            put25, call25 = _interp(pts, 0.95), _interp(pts, 1.05)
            straddle = None
            atmk = min({x["strike"] for x in o.get("calls", []) if x.get("strike")}, key=lambda k: abs(k - spot), default=None)
            if atmk:
                c = next((x for x in o["calls"] if x["strike"] == atmk), {}); p = next((x for x in o.get("puts", []) if x["strike"] == atmk), {})
                mid = lambda x: ((x.get("bid") or 0) + (x.get("ask") or 0)) / 2 or x.get("lastPrice")  # noqa: E731
                if mid(c) and mid(p):
                    straddle = (mid(c) + mid(p)) / spot * 100
            exp_rows.append({"expiry": e, "days": round(days, 1), "atm_iv": atm, "skew": (put25 - call25) if (put25 and call25) else None, "exp_move_pct": straddle})
            surface.append({"expiry": e, "days": round(days, 1), "iv": [_interp(pts, m) for m in grid_m]})
        if not exp_rows:
            raise HTTPException(status_code=404, detail="option data too sparse to build a surface")
        # 30-day IV by interpolating ATM IV across expiries, compared with the last year of realised volatility
        term = [(r["days"], r["atm_iv"]) for r in exp_rows if r["atm_iv"]]
        iv30 = _interp([(d, v) for d, v in term], 30.0) if len(term) > 1 else (term[0][1] if term else None)
        closes = [c[4] for c in (inv._safe_history(symbol, "1y", "1d") or {"candles": []})["candles"]]
        hv_series = [_hv(closes[: i + 1], 30) for i in range(30, len(closes))]
        hv_series = [v for v in hv_series if v is not None]
        rank = pctile = None
        if iv30 and hv_series:
            lo, hi = min(hv_series), max(hv_series)
            rank = (iv30 - lo) / (hi - lo) * 100 if hi > lo else None
            pctile = sum(1 for v in hv_series if v < iv30) / len(hv_series) * 100
        return {"symbol": symbol, "spot": spot, "moneyness": grid_m, "expiries": exp_rows, "surface": surface, "iv30": iv30, "hv": {"10d": _hv(closes, 10), "30d": _hv(closes, 30), "60d": _hv(closes, 60)},
                "iv_rank": rank, "iv_percentile": pctile, "hv_range": [min(hv_series), max(hv_series)] if hv_series else None,
                "put_call_volume": (pc_vol[1] / pc_vol[0]) if pc_vol[0] else None, "put_call_oi": (pc_oi[1] / pc_oi[0]) if pc_oi[0] else None,
                "note": "IV rank/percentile compares 30-day implied vol with the last year of 30-day REALISED vol (Yahoo publishes no IV history)."}
    return cached(f"ovdv:{symbol}", 600, go)


UOA_UNIVERSE = ("SPY QQQ IWM AAPL MSFT NVDA AMZN GOOGL META TSLA AMD AVGO NFLX COIN MSTR PLTR SMCI ARM BABA JPM BAC XOM CVX LLY UNH DIS BA INTC MU UBER SHOP SOFI RIVN HOOD GME "
                "AMC F T PFE NIO SNOW CRWD").split()


def _uoa_one(sym: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    base = _chain_raw(sym)
    spot = (base.get("quote") or {}).get("regularMarketPrice")
    exps = [e for e in (base.get("expirationDates") or []) if e - time.time() > 0][:2]
    for e in exps:
        ch = base if e == (base.get("options") or [{}])[0].get("expirationDate") else _chain_raw(sym, e)
        o = (ch.get("options") or [{}])[0]
        for kind, rows in (("CALL", o.get("calls", [])), ("PUT", o.get("puts", []))):
            for x in rows:
                v, oi, last = x.get("volume") or 0, x.get("openInterest") or 0, x.get("lastPrice") or 0
                prem = last * v * 100
                if v >= 500 and prem >= 75_000 and (oi == 0 or v / oi >= 1.5):
                    out.append({"symbol": sym, "type": kind, "strike": x.get("strike"), "expiry": e, "vol": v, "oi": oi, "ratio": (v / oi) if oi else None, "last": last,
                                "iv": (x.get("impliedVolatility") or 0) * 100, "premium": prem, "spot": spot, "itm": bool(x.get("inTheMoney")),
                                "otm_pct": ((x["strike"] / spot - 1) * 100) if spot and x.get("strike") else None})
    return out


@router.get("/options/unusual")
def api_uoa():
    def go() -> list[dict[str, Any]]:
        rows = [r for part in _POOL.map(lambda s: _safe(lambda: _uoa_one(s), []), UOA_UNIVERSE) for r in part]
        return sorted(rows, key=lambda r: r["premium"], reverse=True)[:80]
    return {"rows": cached("uoa", 900, go), "universe": len(UOA_UNIVERSE), "note": "Contracts with volume >= 500, premium >= $75K and volume >= 1.5x open interest, first two expiries."}


# ================================================================== screener universe (EQS) + heatmap (IMAP)
NDX = set("AAPL MSFT NVDA AMZN META GOOGL GOOG AVGO TSLA COST NFLX AMD PEP ADBE CSCO TMUS INTC QCOM AMGN TXN INTU AMAT ISRG HON BKNG SBUX GILD VRTX ADP PANW MU LRCX ADI MDLZ REGN "
          "KLAC SNPS CDNS ASML MELI PYPL CRWD ABNB MAR ORLY FTNT CSX MRVL WDAY ADSK PDD DASH CHTR NXPI ROP PCAR MNST CPRT AEP KDP PAYX ODFL FAST CTAS EXC LULU KHC MCHP IDXX EA GEHC "
          "VRSK CCEP CTSH BKR XEL DDOG TTD ANSS ON CSGP CDW BIIB DXCM ZS GFS TEAM WBD MDB ILMN LIN".split())
UNIVERSE = sorted(set((
    "AAPL MSFT NVDA GOOGL GOOG AMZN META AVGO TSLA BRK-B LLY JPM V UNH XOM WMT MA JNJ PG HD ORCL COST NFLX BAC AMD CRM ABBV KO PEP ADBE TMO CSCO MCD INTC QCOM DIS CAT GS UBER PLTR "
    "ACN LIN ABT TXN DHR VZ NKE PM IBM INTU AMGN CMCSA NOW SPGI RTX HON LOW UNP AMAT PFE T MS ISRG BKNG NEE BLK PGR SCHW SYK ETN C TJX VRTX BSX ADP PANW LMT MU COP FI BMY CB MDT GILD "
    "ADI SBUX DE MMC KLAC AMT LRCX MDLZ SO ICE BX CI REGN CDNS SNPS UPS MO ELV DUK TMUS CME EQIX WM CL APH MCK ZTS PYPL CSX ITW MCO TGT GD ORLY MAR FCX PNC USB EMR HCA CTAS AON NOC "
    "APD ECL WELL MSI NSC CVS MNST PSX MPC OXY EOG SLB AZO GM F FDX ROP FTNT ABNB DASH CRWD WDAY ADSK COIN MSTR SMCI ARM SHOP SOFI HOOD RIVN GME AMC RBLX SNOW NET DDOG ZS TEAM MDB "
    "TTD LULU DXCM IDXX EA CTSH PAYX FAST ODFL CPRT VRSK KDP KHC MCHP NXPI ON GFS MRVL ANET PCAR TTWO WBD CHTR EXC AEP XEL BKR CSGP CDW BIIB ILMN GEHC KMB GIS STZ HSY SYY HPQ DELL "
    "STX WDC NTAP MPWR TER ENPH FSLR CEG VST NRG GEV PWR ANF DECK ULTA TSCO DG DLTR BBY WMB KMI OKE HAL DVN FANG HES CTRA NUE STLD MLM VMC CF MOS DOW DD LYB IP PKG SW BALL AVY".split())))
_SNAP: dict[str, dict[str, Any]] = {}
_snap_state = {"running": False, "done": 0, "total": len(UNIVERSE), "at": 0.0, "loaded": False}
_SNAP_SCHEMA = "CREATE TABLE IF NOT EXISTS screener_snapshot (symbol TEXT PRIMARY KEY, data TEXT NOT NULL, at REAL NOT NULL)"


def _rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def _snapshot_one(sym: str) -> dict[str, Any] | None:
    try:
        qs = inv._qsummary(sym, "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile")
        h = inv._history(sym, "1y", "1d")
    except Exception:  # noqa: BLE001
        return None
    pr, sd, ks, fd, ap = (qs.get(k) or {} for k in ("price", "summaryDetail", "defaultKeyStatistics", "financialData", "assetProfile"))
    c = h["candles"]
    if len(c) < 30:
        return None
    closes = [x[4] for x in c]
    price = h["meta"].get("regularMarketPrice") or closes[-1]
    prev = closes[-2] if len(closes) > 1 else price
    pf = lambda n: ((price / closes[-1 - n] - 1) * 100) if len(closes) > n and closes[-1 - n] else None  # noqa: E731
    year = dt.datetime.now(dt.timezone.utc).year
    y0 = next((x[4] for x in c if dt.datetime.fromtimestamp(x[0], dt.timezone.utc).year == year), None)
    sma = lambda n: (sum(closes[-n:]) / n) if len(closes) >= n else None  # noqa: E731
    hi, lo = max(x[2] for x in c), min(x[3] for x in c)
    avgvol = statistics.mean([x[5] for x in c[-30:]]) if c else None
    tgt = fd.get("targetMeanPrice")
    return {"symbol": sym, "name": pr.get("shortName") or pr.get("longName") or sym, "sector": ap.get("sector") or "Other", "industry": ap.get("industry"), "price": price,
            "mcap": pr.get("marketCap") or sd.get("marketCap"), "pct": (price / prev - 1) * 100 if prev else None, "p1w": pf(5), "p1m": pf(21), "p3m": pf(63), "p6m": pf(126),
            "ytd": ((price / y0 - 1) * 100) if y0 else None, "p1y": pf(len(closes) - 1), "pe": sd.get("trailingPE"), "fwd_pe": sd.get("forwardPE") or ks.get("forwardPE"), "peg": ks.get("pegRatio"),
            "pb": ks.get("priceToBook"), "ps": sd.get("priceToSalesTrailing12Months"), "ev_ebitda": ks.get("enterpriseToEbitda"), "div_yield": (sd.get("dividendYield") or 0) * 100 if sd.get("dividendYield") else None,
            "beta": sd.get("beta") or ks.get("beta"), "roe": (fd.get("returnOnEquity") or 0) * 100 if fd.get("returnOnEquity") is not None else None,
            "gross_margin": (fd.get("grossMargins") or 0) * 100 if fd.get("grossMargins") is not None else None, "op_margin": (fd.get("operatingMargins") or 0) * 100 if fd.get("operatingMargins") is not None else None,
            "net_margin": (fd.get("profitMargins") or 0) * 100 if fd.get("profitMargins") is not None else None, "rev_growth": (fd.get("revenueGrowth") or 0) * 100 if fd.get("revenueGrowth") is not None else None,
            "earn_growth": (fd.get("earningsGrowth") or 0) * 100 if fd.get("earningsGrowth") is not None else None, "debt_equity": fd.get("debtToEquity"), "current_ratio": fd.get("currentRatio"),
            "fcf": fd.get("freeCashflow"), "target_upside": ((tgt / price - 1) * 100) if tgt else None, "rating": fd.get("recommendationMean"), "n_analysts": fd.get("numberOfAnalystOpinions"),
            "rsi14": _rsi(closes), "vs_sma50": ((price / sma(50) - 1) * 100) if sma(50) else None, "vs_sma200": ((price / sma(200) - 1) * 100) if sma(200) else None,
            "from_high": (price / hi - 1) * 100, "from_low": (price / lo - 1) * 100, "avg_volume": avgvol, "rel_volume": (c[-1][5] / avgvol) if avgvol else None, "ndx": sym in NDX, "at": time.time()}


def _load_snapshot() -> None:
    if _snap_state["loaded"]:
        return
    with db.get_conn() as conn:
        conn.execute(_SNAP_SCHEMA)
        for r in conn.execute("SELECT symbol, data, at FROM screener_snapshot").fetchall():
            _SNAP[r["symbol"]] = json.loads(r["data"])
            _snap_state["at"] = max(_snap_state["at"], r["at"])
    _snap_state["loaded"] = True


def refresh_snapshot(max_age: float = 0) -> None:
    if _snap_state["running"]:
        return
    _snap_state.update(running=True, done=0, total=len(UNIVERSE))
    try:
        _load_snapshot()
        todo = [s for s in UNIVERSE if time.time() - _SNAP.get(s, {}).get("at", 0) > max_age]
        _snap_state["total"] = len(todo)
        for i in range(0, len(todo), 6):
            chunk = todo[i: i + 6]
            for s, rec in zip(chunk, _POOL.map(_snapshot_one, chunk)):
                _snap_state["done"] += 1
                if rec:
                    _SNAP[s] = rec
            with db.get_conn() as conn:
                conn.execute(_SNAP_SCHEMA)
                for s in chunk:
                    if s in _SNAP:
                        conn.execute("INSERT OR REPLACE INTO screener_snapshot (symbol, data, at) VALUES (?,?,?)", (s, json.dumps(_SNAP[s]), _SNAP[s]["at"]))
            _snap_state["at"] = time.time()
            time.sleep(0.4)
    finally:
        _snap_state["running"] = False


def snapshot_loop() -> None:
    """Background: keep the universe snapshot fresh (skipped when it is already recent)."""
    time.sleep(25)
    while True:
        try:
            refresh_snapshot(max_age=40 * 60)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(30 * 60)


SCREEN_FIELDS = [
    ("mcap", "Market cap ($)", "valuation"), ("price", "Price", "valuation"), ("pe", "P/E (TTM)", "valuation"), ("fwd_pe", "P/E (forward)", "valuation"), ("peg", "PEG", "valuation"),
    ("pb", "Price / book", "valuation"), ("ps", "Price / sales", "valuation"), ("ev_ebitda", "EV / EBITDA", "valuation"), ("div_yield", "Dividend yield %", "valuation"),
    ("beta", "Beta", "risk"), ("roe", "ROE %", "fundamentals"), ("gross_margin", "Gross margin %", "fundamentals"), ("op_margin", "Operating margin %", "fundamentals"),
    ("net_margin", "Net margin %", "fundamentals"), ("rev_growth", "Revenue growth %", "fundamentals"), ("earn_growth", "Earnings growth %", "fundamentals"), ("debt_equity", "Debt / equity %", "fundamentals"),
    ("current_ratio", "Current ratio", "fundamentals"), ("fcf", "Free cash flow ($)", "fundamentals"), ("target_upside", "Analyst target upside %", "analysts"), ("rating", "Analyst rating (1=buy..5=sell)", "analysts"),
    ("pct", "Change today %", "technicals"), ("p1w", "1-week %", "technicals"), ("p1m", "1-month %", "technicals"), ("p3m", "3-month %", "technicals"), ("ytd", "YTD %", "technicals"), ("p1y", "1-year %", "technicals"),
    ("rsi14", "RSI (14)", "technicals"), ("vs_sma50", "vs 50-day SMA %", "technicals"), ("vs_sma200", "vs 200-day SMA %", "technicals"), ("from_high", "From 52-week high %", "technicals"),
    ("from_low", "From 52-week low %", "technicals"), ("rel_volume", "Relative volume (x avg)", "technicals"), ("avg_volume", "Avg volume", "technicals"), ("sector", "Sector", "profile"),
]
_TEXT_FIELDS = {"sector", "industry", "symbol", "name"}


class Filter(BaseModel):
    field: str
    op: str
    value: Any = None
    value2: Any = None


class ScreenReq(BaseModel):
    filters: list[Filter] = []
    sort: str | None = "mcap"
    desc: bool = True
    limit: int = 100
    universe: str = "all"  # all | ndx


def _run_screen(req: ScreenReq) -> list[dict[str, Any]]:
    _load_snapshot()
    rows = list(_SNAP.values())
    if req.universe == "ndx":
        rows = [r for r in rows if r.get("ndx")]
    for f in req.filters:
        def ok(r: dict[str, Any], f: Filter = f) -> bool:
            v = r.get(f.field)
            if f.field in _TEXT_FIELDS:
                return v is not None and (str(v).lower() == str(f.value).lower() if f.op == "=" else str(f.value).lower() in str(v).lower())
            if v is None:
                return False
            a, b = _num(f.value), _num(f.value2)
            if a is None:
                return True
            return {">": v > a, ">=": v >= a, "<": v < a, "<=": v <= a, "=": v == a, "between": b is not None and a <= v <= b}.get(f.op, True)
        rows = [r for r in rows if ok(r)]
    if req.sort:
        rows.sort(key=lambda r: (r.get(req.sort) is None, -(r.get(req.sort) or 0) if req.desc and req.sort not in _TEXT_FIELDS else (r.get(req.sort) or 0)))
        if req.sort in _TEXT_FIELDS and req.desc:
            rows.reverse()
    return rows[: max(1, min(req.limit, 500))]


@router.get("/screener/fields")
def api_screen_fields():
    return {"fields": [{"key": k, "label": l, "group": g, "text": k in _TEXT_FIELDS} for k, l, g in SCREEN_FIELDS]}


@router.get("/screener/status")
def api_screen_status():
    _load_snapshot()
    return {"count": len(_SNAP), "universe": len(UNIVERSE), "updated": _snap_state["at"], "running": _snap_state["running"], "progress": _snap_state["done"], "total": _snap_state["total"]}


@router.post("/screener/run")
def api_screen_run(req: ScreenReq):
    rows = _run_screen(req)
    return {"rows": rows, "matched": len(rows), "universe": len(_SNAP)}


@router.post("/screener/refresh")
def api_screen_refresh():
    threading.Thread(target=refresh_snapshot, kwargs={"max_age": 0}, daemon=True).start()
    return {"started": True}


@router.post("/screener/export", response_class=PlainTextResponse)
def api_screen_export(req: ScreenReq):
    rows = _run_screen(req)
    keys = ["symbol", "name", "sector", "industry"] + [k for k, _l, _g in SCREEN_FIELDS if k not in ("sector",)]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(keys)
    for r in rows:
        w.writerow([("" if r.get(k) is None else (round(r[k], 4) if isinstance(r.get(k), float) else r.get(k))) for k in keys])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=nostro_screen.csv"})


_SCR_SCHEMA = "CREATE TABLE IF NOT EXISTS saved_screens (name TEXT PRIMARY KEY, req TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))"


@router.get("/screener/saved")
def api_screens_list():
    with db.get_conn() as conn:
        conn.execute(_SCR_SCHEMA)
        return {"screens": [{"name": r["name"], "req": json.loads(r["req"])} for r in conn.execute("SELECT * FROM saved_screens ORDER BY name").fetchall()]}


@router.post("/screener/saved/{name}")
def api_screens_save(name: str, req: ScreenReq):
    if not re.fullmatch(r"[\w .\-]{1,40}", name):
        raise HTTPException(status_code=400, detail="invalid name")
    with db.get_conn() as conn:
        conn.execute(_SCR_SCHEMA)
        conn.execute("INSERT INTO saved_screens (name, req) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET req=excluded.req, updated_at=datetime('now')", (name, req.model_dump_json()))
    return {"ok": True}


@router.delete("/screener/saved/{name}")
def api_screens_delete(name: str):
    with db.get_conn() as conn:
        conn.execute(_SCR_SCHEMA)
        conn.execute("DELETE FROM saved_screens WHERE name = ?", (name,))
    return {"ok": True}


@router.get("/heatmap")
def api_heatmap(index: str = "sp500"):
    _load_snapshot()
    rows = [r for r in _SNAP.values() if r.get("mcap") and r.get("pct") is not None and (index != "ndx" or r.get("ndx"))]
    return {"index": index, "items": [{"symbol": r["symbol"], "name": r["name"], "sector": r["sector"], "mcap": r["mcap"], "pct": r["pct"], "price": r["price"]} for r in rows],
            "updated": _snap_state["at"], "coverage": len(rows), "note": "Constituents shown are the ~250 most-followed US large caps we track, sized by market cap."}


# ================================================================== portfolio risk (PORT)
def _holdings() -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS capital_holdings (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL, cost_basis REAL NOT NULL DEFAULT 0, added_at TEXT NOT NULL DEFAULT (datetime('now')))")
        return [dict(r) for r in conn.execute("SELECT * FROM capital_holdings").fetchall()]


def _beta(a: list[float], b: list[float]) -> float | None:
    if len(a) < 20:
        return None
    mb = statistics.mean(b); ma = statistics.mean(a)
    var = sum((y - mb) ** 2 for y in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / var if var else None


@router.get("/portfolio/risk")
def api_portfolio_risk(benchmark: str = "SPY"):
    def go() -> dict[str, Any]:
        hold = _holdings()
        if not hold:
            raise HTTPException(status_code=404, detail="Add positions on the CAPITAL page first.")
        ysym = [_yahoo_symbol(h["kind"], h["symbol"]) for h in hold]
        syms = list(dict.fromkeys(ysym + [benchmark, "QQQ", "BTC-USD"]))
        dates, vals = _aligned(syms, "1Y")
        last = {s: vals[s][-1] for s in vals if s in vals}
        pos = {}
        for h, ys in zip(hold, ysym):
            if ys in last:
                pos.setdefault(ys, {"symbol": h["symbol"].upper(), "kind": h["kind"], "value": 0.0, "cost": 0.0})
                pos[ys]["value"] += last[ys] * h["qty"]; pos[ys]["cost"] += h["cost_basis"] * h["qty"]
        total = sum(p["value"] for p in pos.values())
        if not total:
            raise HTTPException(status_code=404, detail="no priced positions")
        w = {ys: p["value"] / total for ys, p in pos.items()}
        ret = lambda s: [(vals[s][i] / vals[s][i - 1] - 1) for i in range(1, len(dates))]  # noqa: E731
        R = {s: ret(s) for s in vals}
        port = [sum(w[ys] * R[ys][i] for ys in w) for i in range(len(dates) - 1)]
        eq = [100.0]
        for r_ in port:
            eq.append(eq[-1] * (1 + r_))
        peak, mdd = eq[0], 0.0
        for x in eq:
            peak = max(peak, x); mdd = min(mdd, x / peak - 1)
        vol = statistics.pstdev(port) * math.sqrt(252) if len(port) > 2 else None
        ann = (eq[-1] / 100) ** (252 / max(1, len(port))) - 1
        rf = (_safe(lambda: inv._fred("DGS3MO")[-1][1], 4.0) or 4.0) / 100
        srt = sorted(port)
        var95 = -srt[int(len(srt) * 0.05)] if len(srt) > 20 else None
        var99 = -srt[max(0, int(len(srt) * 0.01))] if len(srt) > 20 else None
        cvar95 = -statistics.mean(srt[: max(1, int(len(srt) * 0.05))]) if len(srt) > 20 else None
        betas = {b: _beta(port, R[b]) for b in (benchmark, "QQQ", "BTC-USD") if b in R}
        # sector exposure
        exposure: dict[str, float] = {}
        for ys, p in pos.items():
            sector = "Crypto" if p["kind"] == "crypto" else (_safe(lambda: (inv._qs(ys, "assetProfile", 86400).get("assetProfile") or {}).get("sector"), None) or "ETF / other")
            exposure[sector] = exposure.get(sector, 0.0) + p["value"] / total * 100
        # scenarios: shock a factor, impact = each position's beta to that factor
        scen = []
        for label, fac, shock in (("S&P 500 -10%", benchmark, -10), ("Nasdaq -10%", "QQQ", -10), ("Nasdaq -20%", "QQQ", -20), ("Bitcoin -30%", "BTC-USD", -30), ("S&P 500 +10%", benchmark, 10)):
            if fac not in R:
                continue
            per = []
            for ys, p in pos.items():
                b = _beta(R[ys], R[fac]) if ys != fac else 1.0
                per.append({"symbol": p["symbol"], "beta": b, "impact_usd": (b or 0) * shock / 100 * p["value"]})
            scen.append({"label": label, "factor": fac, "shock": shock, "total_usd": sum(x["impact_usd"] for x in per), "total_pct": sum(x["impact_usd"] for x in per) / total * 100, "positions": per})
        bench = {b: [round(x / vals[b][0] * 100, 3) for x in vals[b]] for b in (benchmark, "QQQ") if b in vals}
        return {"value": total, "positions": [{"symbol": p["symbol"], "kind": p["kind"], "weight": p["value"] / total * 100, "value": p["value"], "pnl_pct": ((p["value"] / p["cost"] - 1) * 100) if p["cost"] else None} for p in pos.values()],
                "metrics": {"return_ann": ann * 100, "vol_ann": vol * 100 if vol else None, "sharpe": ((ann - rf) / vol) if vol else None, "max_drawdown": mdd * 100, "var95_pct": var95 * 100 if var95 else None,
                            "var99_pct": var99 * 100 if var99 else None, "var95_usd": var95 * total if var95 else None, "cvar95_pct": cvar95 * 100 if cvar95 else None, "betas": betas, "rf": rf * 100,
                            "days": len(port)},
                "exposure": [{"sector": k, "pct": v} for k, v in sorted(exposure.items(), key=lambda kv: -kv[1])], "scenarios": scen, "dates": dates, "portfolio": [round(x, 3) for x in eq], "benchmarks": bench,
                "note": "Constant current weights over the last year (not your actual trade history)."}
    return cached(f"port:{benchmark}:{hash(json.dumps(_holdings(), sort_keys=True, default=str))}", 300, go)


def warm() -> None:
    def run() -> None:
        _safe(refresh_calendar, None)
        for key, ttl, fn in (("curve", 3600, lambda: api_curve()), ("wirp", 900, lambda: api_wirp())):
            _safe(lambda: fn(), None)
    threading.Thread(target=run, daemon=True, name="market-warm").start()
    threading.Thread(target=snapshot_loop, daemon=True, name="screener-snapshot").start()
