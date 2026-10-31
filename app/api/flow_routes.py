"""MOST / DARK POOLS — money flow and institutional activity (INVESTING › MOST / DARK POOLS).

Sources (all free, no key):
  FINRA Reg SHO daily files   cdn.finra.org/equity/regsho/daily/CNMSshvol<YYYYMMDD>.txt — every off-exchange (FINRA TRF)
                              trade in every NMS stock for the day: total volume and the part reported as short sale.
                              Off-exchange = dark pools + wholesaler internalisation. Published the evening of the trade day.
                              DPI ("dark pool indicator") = short volume / off-exchange volume: market makers selling short
                              to fill buyers is how buying shows up here, so a high DPI has historically leaned bullish.
  FINRA OTC Transparency      api.finra.org …/otcMarket/weeklySummary — shares and trades per ATS (named dark pool) per symbol
                              per week. FINRA publishes it 2 weeks (Tier 1) to 4 weeks (Tier 2) after the week.
  Yahoo                       quotes + 1-minute bars for MOST ACTIVE and the block-like prints scanner.

Honest limits, shown on the page: real-time block/dark prints need a paid SIP/exchange feed. The BLOCK-LIKE PRINTS panel is
a proxy — single 1-minute bars with extreme volume and notional (>= BLOCK_MULT x the median minute and >= BLOCK_MIN_USD).
"""
from __future__ import annotations

import datetime as dt
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/flow")
NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
CACHE = Path(__file__).resolve().parents[2] / "data_cache" / "finra"
FINRA_WEEKLY = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"
DPI_DAYS = 20
BLOCK_MULT = 8.0
BLOCK_MIN_USD = 3_000_000
EXTRA = ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "SMH", "XLF", "XLE", "XLK", "HYG", "ARKK", "IBIT"]
_mem: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(8)


def _cached(key: str, ttl: float, fn):
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _mem[key] = (time.time(), val)
    return val


def _universe() -> list[str]:
    from app.api import market_routes as mr

    return sorted(set(mr.UNIVERSE) | set(EXTRA))


# ------------------------------------------------------------------ FINRA Reg SHO daily (off-exchange)
def _regsho(day: dt.date) -> dict[str, tuple[float, float]] | None:
    """{symbol: (short_volume, total_off_exchange_volume)} for one trade date, disk-cached. None = no file (holiday/not yet)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"CNMSshvol{day:%Y%m%d}.txt"
    if f.exists() and f.stat().st_size > 1000:
        text = f.read_text()
    else:
        try:
            r = requests.get(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day:%Y%m%d}.txt", headers=UA, timeout=25)
        except requests.RequestException:
            return None
        if r.status_code != 200 or len(r.content) < 1000:
            return None
        text = r.text
        f.write_text(text)
    out: dict[str, tuple[float, float]] = {}
    for line in text.splitlines()[1:]:
        p = line.split("|")
        if len(p) >= 5:
            try:
                out[p[1]] = (float(p[2]), float(p[4]))
            except ValueError:
                pass
    return out


def _trade_days(n: int) -> list[tuple[dt.date, dict[str, tuple[float, float]]]]:
    """The newest n trade dates that have a published file, newest first."""
    days, d = [], dt.datetime.now(NY).date()
    tries = 0
    while len(days) < n and tries < n * 2 + 10:
        tries += 1
        if d.weekday() < 5:
            data = _regsho(d)
            if data:
                days.append((d, data))
        d -= dt.timedelta(days=1)
    return days


def _dark_pool() -> dict[str, Any]:
    from app.api import market_routes as mr

    mr._load_snapshot()
    days = _trade_days(DPI_DAYS)
    if not days:
        raise HTTPException(status_code=502, detail="FINRA daily files unavailable right now")
    latest_day, latest = days[0]
    rows = []
    mkt_short = mkt_total = 0.0
    for sym in _universe():
        s, t = latest.get(sym.replace("-", "."), latest.get(sym, (0.0, 0.0)))
        if t <= 0:
            continue
        snap = mr._SNAP.get(sym) or {}
        price = snap.get("price")
        hist = []
        for d, data in reversed(days):
            ss, tt = data.get(sym, (0.0, 0.0))
            hist.append(round(100 * ss / tt, 1) if tt else None)
        dpi = 100 * s / t
        valid = [h for h in hist[:-1] if h is not None]
        avg = statistics.mean(valid) if valid else None
        rows.append({
            "symbol": sym, "name": snap.get("name"), "sector": snap.get("sector"), "price": price, "pct": snap.get("pct"),
            "offex_volume": round(t), "offex_usd": round(t * price) if price else None, "short_volume": round(s),
            "dpi": round(dpi, 1), "dpi_avg": round(avg, 1) if avg is not None else None, "dpi_change": round(dpi - avg, 1) if avg is not None else None,
            "offex_share": None, "dpi_hist": hist,
        })
        if price and sym in mr.UNIVERSE:
            mkt_short += s * price
            mkt_total += t * price
    market_hist = []
    for d, data in reversed(days):
        ss = tt = 0.0
        for sym in mr.UNIVERSE:
            p = (mr._SNAP.get(sym) or {}).get("price")
            if not p:
                continue
            a, b = data.get(sym, (0.0, 0.0))
            ss += a * p; tt += b * p
        market_hist.append({"date": d.isoformat(), "dix": round(100 * ss / tt, 2) if tt else None})
    rows.sort(key=lambda r: -(r["offex_usd"] or 0))
    # off-exchange share of the SAME session's consolidated volume (Yahoo daily bar of that date), for the 60 biggest names
    from app.api import invest_routes as inv

    def cons_vol(sym: str) -> float | None:
        h = inv._safe_history(sym, "1mo", "1d")
        if not h:
            return None
        for c in h["candles"]:
            if dt.datetime.fromtimestamp(c[0], NY).date() == latest_day:
                return float(c[5]) or None
        return None

    top = rows[:60]
    for r, v in zip(top, _pool.map(lambda r: cons_vol(r["symbol"]), top)):
        r["offex_share"] = round(100 * r["offex_volume"] / v, 1) if v and r["offex_volume"] <= v * 1.2 else None
    for r in rows[60:]:
        r["offex_share"] = None
    return {"date": latest_day.isoformat(), "days": len(days), "market_dix": round(100 * mkt_short / mkt_total, 2) if mkt_total else None,
            "market_hist": market_hist, "rows": rows,
            "note": "Off-exchange = FINRA TRF prints (dark pools + internalisation), next-day. DPI = short / off-exchange volume. "
                    "DIX = dollar-weighted DPI of the ~300-stock universe."}


@router.get("/darkpool")
def api_darkpool():
    return _cached("darkpool", 1800, _dark_pool)


# ------------------------------------------------------------------ FINRA ATS weekly (named dark pools)
def _finra(body: dict[str, Any]) -> list[dict[str, Any]]:
    r = requests.post(FINRA_WEEKLY, json=body, headers={**UA, "Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        return []
    try:
        return r.json() or []
    except ValueError:
        return []


def _ats_symbol(sym: str, weeks: int = 6) -> dict[str, Any]:
    sym = sym.upper()
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    found: list[tuple[str, list[dict[str, Any]]]] = []
    for k in range(1, 12):
        if len(found) >= weeks:
            break
        wk = (monday - dt.timedelta(weeks=k)).isoformat()
        rows = [x for x in _finra({"limit": 200, "compareFilters": [
            {"compareType": "EQUAL", "fieldName": "issueSymbolIdentifier", "fieldValue": sym},
            {"compareType": "EQUAL", "fieldName": "weekStartDate", "fieldValue": wk}]}) if x.get("summaryTypeCode") == "ATS_W_SMBL_FIRM"]
        if rows:
            found.append((wk, rows))
    if not found:
        return {"symbol": sym, "weeks": [], "venues": [], "note": "No ATS data published for this symbol yet."}
    latest_wk, latest = found[0]
    total = sum(x.get("totalWeeklyShareQuantity") or 0 for x in latest)
    venues = sorted(({"venue": x.get("marketParticipantName"), "mpid": x.get("MPID"), "shares": x.get("totalWeeklyShareQuantity") or 0,
                      "trades": x.get("totalWeeklyTradeCount") or 0, "notional": x.get("totalNotionalSum") or 0,
                      "avg_size": round((x.get("totalWeeklyShareQuantity") or 0) / max(1, x.get("totalWeeklyTradeCount") or 1))}
                     for x in latest), key=lambda v: -v["shares"])
    for v in venues:
        v["share_pct"] = round(100 * v["shares"] / total, 1) if total else 0
    weeks_out = [{"week": wk, "shares": sum(x.get("totalWeeklyShareQuantity") or 0 for x in rows),
                  "trades": sum(x.get("totalWeeklyTradeCount") or 0 for x in rows),
                  "notional": sum(x.get("totalNotionalSum") or 0 for x in rows)} for wk, rows in reversed(found)]
    for w in weeks_out:
        w["avg_size"] = round(w["shares"] / w["trades"]) if w["trades"] else None
    return {"symbol": sym, "week": latest_wk, "venues": venues[:15], "weeks": weeks_out,
            "note": "FINRA ATS transparency: shares traded in each named dark pool, published 2-4 weeks after the week."}


@router.get("/ats")
def api_ats(symbol: str):
    s = symbol.strip().upper()
    if not s.replace(".", "").replace("-", "").isalnum() or len(s) > 10:
        raise HTTPException(status_code=400, detail="bad symbol")
    return _cached(f"ats:{s}", 6 * 3600, lambda: _ats_symbol(s))


# ------------------------------------------------------------------ MOST active + block-like prints
def _quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    from app.morning.brief import _quotes as q

    return q(symbols)


def _most() -> dict[str, Any]:
    from app.api import market_routes as mr

    mr._load_snapshot()
    syms = _universe()
    q = _quotes(syms)
    rows = []
    for s in syms:
        x = q.get(s) or {}
        px, vol = x.get("regularMarketPrice"), x.get("regularMarketVolume")
        if not px or not vol:
            continue
        avg = x.get("averageDailyVolume10Day") or x.get("averageDailyVolume3Month")
        snap = mr._SNAP.get(s) or {}
        rows.append({"symbol": s, "name": x.get("shortName") or snap.get("name"), "sector": snap.get("sector"), "price": px,
                     "pct": x.get("regularMarketChangePercent"), "volume": vol, "dollar_volume": round(px * vol),
                     "rel_volume": round(vol / avg, 2) if avg else None, "state": x.get("marketState"),
                     "pre_pct": x.get("preMarketChangePercent"), "post_pct": x.get("postMarketChangePercent")})
    top = lambda key, rev=True, n=25: sorted([r for r in rows if r.get(key) is not None], key=lambda r: r[key], reverse=rev)[:n]  # noqa: E731
    return {"at": time.time(), "dollar": top("dollar_volume"), "unusual": top("rel_volume"), "gainers": top("pct"), "losers": top("pct", False),
            "universe": len(rows)}


@router.get("/most")
def api_most():
    return _cached("most", 60, _most)


def _blocks_one(sym: str) -> list[dict[str, Any]]:
    from app.api import invest_routes as inv

    try:
        h = inv._history(sym, "1d", "1m")
    except Exception:  # noqa: BLE001
        return []
    c = h["candles"]
    if len(c) < 30:
        return []
    vols = [x[5] for x in c if x[5]]
    if not vols:
        return []
    med = statistics.median(vols) or 1
    out = []
    for i, x in enumerate(c):
        v, px = x[5] or 0, x[4]
        usd = v * px
        if v >= BLOCK_MULT * med and usd >= BLOCK_MIN_USD:
            prev = c[i - 1][4] if i else x[1]
            out.append({"symbol": sym, "ts": x[0], "price": px, "volume": v, "usd": round(usd), "x_median": round(v / med, 1),
                        "move_pct": round((px / prev - 1) * 100, 3) if prev else None, "dir": "up" if px >= x[1] else "down"})
    return out


def _blocks() -> dict[str, Any]:
    most = _cached("most", 60, _most)
    names = [r["symbol"] for r in most["dollar"][:30]] + [r["symbol"] for r in most["unusual"][:15]]
    names = list(dict.fromkeys(names))
    prints = [p for part in _pool.map(_blocks_one, names) for p in part]
    prints.sort(key=lambda p: -p["ts"])
    return {"at": time.time(), "scanned": len(names), "prints": prints[:150], "mult": BLOCK_MULT, "min_usd": BLOCK_MIN_USD,
            "note": "Proxy for block trades: single 1-minute bars with volume >= 8x the stock's median minute and >= $3M notional "
                    "(true exchange/dark prints need a paid feed)."}


@router.get("/blocks")
def api_blocks():
    return _cached("blocks", 90, _blocks)
