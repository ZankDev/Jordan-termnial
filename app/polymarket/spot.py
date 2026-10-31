"""Underlying crypto spot price + realized volatility for the momo strategy's
flip-risk Monte Carlo, from Binance's public klines (no key).

Volatility convention matches the source bot's src/buzzer.js refreshVolatility()
exactly: stdev of 1-minute simple returns over the trailing 30 one-minute candles,
expressed as a PERCENT (e.g. 0.08 means 0.08%/min) — the `sigmaPerMinPct`
flip_risk.py expects, not a fraction.
"""
from __future__ import annotations

import math
import time

from app.polymarket import http
from app.polymarket.asset_map import binance_symbol

VOL_WINDOW = 30  # trailing 1-min candles, matches the source bot
KLINES_URL = "https://api.binance.com/api/v3/klines"


HIST_INTERVAL = 0.22  # seconds between historical (backfill) Binance calls, across all threads


def klines(symbol: str, interval: str, start_ms: int, limit: int = 1000, historical: bool = False) -> list[list[float]]:
    """One page of klines as [t_seconds, open, high, low, close, volume].
    historical=True paces the call (see http._throttle) — used by backfill only."""
    rows = http.get_json(KLINES_URL, {"symbol": symbol, "interval": interval, "startTime": int(start_ms),
                                       "limit": min(limit, 1000)},
                         # live reads fail fast (a stalled tick freezes every paper account); backfill can be patient
                         timeout=15 if historical else 5, retries=2 if historical else 1,
                         throttle=("binance-hist", HIST_INTERVAL) if historical else None)
    return [[k[0] // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in rows]


# Short-TTL caches for the "give me the CURRENT reading" calls, shared by the radar,
# the live engine and the recorder so N sessions of one asset cost ONE fetch.
_LIVE_TTL = 4.0
_price_cache: dict[str, tuple[float, float]] = {}
_vol_cache: dict[str, tuple[float, float]] = {}
_recent_cache: dict[str, tuple[float, list[tuple[float, float]]]] = {}
_RECENT_TTL = 1.5
_RECENT_SECS = 420


def live_price(asset: str) -> float | None:
    trace = recent_1s(asset)
    if trace:
        return trace[-1][1]
    symbol = binance_symbol(asset)
    if not symbol:
        return None
    now = time.time()
    hit = _price_cache.get(asset)
    if hit and now - hit[0] < _LIVE_TTL:
        return hit[1]
    try:
        price = float(http.get_json("https://api.binance.com/api/v3/ticker/price", {"symbol": symbol}, timeout=8)["price"])
        _price_cache[asset] = (now, price)
        return price
    except Exception:  # noqa: BLE001
        return hit[1] if hit else None


def recent_1s(asset: str) -> list[tuple[float, float]]:
    """The last ~7 minutes of Binance 1-SECOND closes as ascending (unix_seconds,
    close) — the live counterpart of the dense 1 Hz trace the source JS bot keeps.
    ONE call per asset serves every session of that asset (a session is at most
    300 s long). Stale data is served over none on a failed fetch."""
    symbol = binance_symbol(asset)
    if not symbol:
        return []
    now = time.time()
    hit = _recent_cache.get(asset)
    if hit and now - hit[0] < _RECENT_TTL:
        return hit[1]
    try:
        rows = klines(symbol, "1s", int((now - _RECENT_SECS) * 1000), limit=_RECENT_SECS + 10)
        pts = [(float(r[0]), float(r[4])) for r in rows]
        if pts:
            _recent_cache[asset] = (now, pts)
            return pts
    except Exception:  # noqa: BLE001
        pass
    return hit[1] if hit else []


def strike_from(trace: list[tuple[float, float]], start_ts: float) -> float | None:
    """Spot at the session open: the close of the last 1s candle at or before start_ts."""
    best = None
    for t, p in trace:
        if t <= start_ts:
            best = p
        else:
            break
    return best


def price_at(asset: str, ts: int) -> float | None:
    """Nearest 1-minute kline close at-or-before `ts` (coarse fallback when no 1s data)."""
    symbol = binance_symbol(asset)
    if not symbol:
        return None
    try:
        rows = klines(symbol, "1m", (ts - 5 * 60) * 1000, limit=6, historical=True)
        candidates = [r for r in rows if r[0] <= ts + 60]
        return candidates[-1][4] if candidates else None
    except Exception:  # noqa: BLE001
        return None


def session_trace(asset: str, start_ts: int, end_ts: int) -> list[tuple[float, float]]:
    """Intra-session spot trace for backtest replay: 1-second closes when Binance
    still has them, else 1-minute closes (sparse but honest — never fabricated)."""
    symbol = binance_symbol(asset)
    if not symbol:
        return []
    span = int(end_ts - start_ts) + 10
    try:
        rows = klines(symbol, "1s", (start_ts - 5) * 1000, limit=min(1000, span + 5), historical=True)
        pts = [(float(r[0]), float(r[4])) for r in rows if start_ts - 5 <= r[0] <= end_ts + 5]
        if len(pts) >= max(10, span // 3):
            return pts
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = klines(symbol, "1m", (start_ts - 300) * 1000, limit=10, historical=True)
        return [(float(r[0]), float(r[4])) for r in rows if start_ts - 60 <= r[0] <= end_ts + 5]
    except Exception:  # noqa: BLE001
        return []


def realized_vol_per_min(asset: str, at_ts: int | None = None) -> float | None:
    """Stdev of 1-min simple returns over the trailing VOL_WINDOW candles ending at
    `at_ts` (default: now), as a PERCENT. Only the "right now" case is cached."""
    symbol = binance_symbol(asset)
    if not symbol:
        return None
    if at_ts is None:
        hit = _vol_cache.get(asset)
        if hit and time.time() - hit[0] < 20:
            return hit[1]
    end_ms = (at_ts * 1000) if at_ts is not None else int(time.time() * 1000)
    start_ms = end_ms - (VOL_WINDOW + 5) * 60_000
    try:
        rows = klines(symbol, "1m", start_ms, limit=VOL_WINDOW + 5, historical=at_ts is not None)
    except Exception:  # noqa: BLE001
        hit = _vol_cache.get(asset)
        return hit[1] if (at_ts is None and hit) else None
    closes = [r[4] for r in rows if r[0] * 1000 <= end_ms][-VOL_WINDOW:]
    if len(closes) < 5:
        return None
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    mean = sum(rets) / len(rets)
    result = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * 100
    if at_ts is None:
        _vol_cache[asset] = (time.time(), result)
    return result
