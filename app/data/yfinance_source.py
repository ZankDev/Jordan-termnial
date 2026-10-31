"""Free historical OHLCV data for NQ/ES futures, cached locally as parquet.

Talks directly to Yahoo Finance's public chart endpoint (the same one the `yfinance`
package wraps) rather than depending on that package, which has been unreliable
about keeping up with Yahoo's anti-bot changes. No API key needed.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

SYMBOLS = {
    "NQ": "NQ=F",
    "ES": "ES=F",
}

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Yahoo's allowed `range` per intraday `interval` (enforced server-side)
INTRADAY_RANGE = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "1h": "730d",
}

NY_TZ = "America/New_York"


def _cache_path(symbol: str, interval: str) -> Path:
    return CACHE_DIR / f"{symbol}_{interval}.parquet"


def _fetch_chart(ticker: str, interval: str) -> pd.DataFrame:
    rng = "20y" if interval == "1d" else INTRADAY_RANGE.get(interval, "60d")
    resp = requests.get(
        CHART_URL.format(symbol=ticker),
        params={"range": rng, "interval": interval, "includePrePost": "false"},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Finance error for {ticker}: {chart['error']}")

    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"No chart data returned for {ticker} ({interval}).")

    result = results[0]
    timestamps = result.get("timestamp")
    if not timestamps:
        raise RuntimeError(f"No bars returned for {ticker} ({interval}).")

    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame(
        {
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "volume": quote["volume"],
        },
        index=pd.to_datetime(timestamps, unit="s", utc=True),
    )
    df.index.name = "datetime"
    df = df.dropna(subset=["open", "high", "low", "close"])
    df.index = df.index.tz_convert(NY_TZ)
    return df.sort_index()


_fetch_refreshing: set[str] = set()  # symbol_interval keys with a background refresh already in flight


def _read_cache(path: Path) -> pd.DataFrame | None:
    # the live engine, trainer and pages read this file from different threads; a corrupt/partial file (e.g. from a
    # crash mid-write before writes were atomic) is treated as missing rather than failing every account every tick
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    # atomic write: readers only ever see the old complete file or the new complete file, never a half-written one
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        df.to_parquet(tmp)
        # Windows refuses to replace a file another thread is reading right now: retry briefly, then give up on caching
        # this copy (the caller still gets the fresh data; the next fetch rewrites the cache)
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
    except Exception:  # noqa: BLE001 - caching is an optimisation, never a reason to fail a fetch
        pass
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def fetch(symbol: str, interval: str = "5m", max_age_seconds: int = 3600) -> pd.DataFrame:
    """Return OHLCV for `symbol` ('NQ' or 'ES') at `interval`, using a local cache.

    Refetches from Yahoo Finance if the cache is missing or older than max_age_seconds - but
    when a cache already exists and Yahoo is slow (seen taking 100+ seconds under load/
    throttling, well past the ~15s request timeout actually enforces for a slow-trickling
    response), the live engine must never block on it: a stale-but-present cache is returned
    immediately and the refresh happens in a background thread instead. This matters even
    more now that real orders are sent from this same tick loop - a multi-minute stall here
    would also delay stop/target/EOD-flatten checks on an account with real money linked.
    """
    ticker = SYMBOLS.get(symbol.upper(), symbol)
    path = _cache_path(symbol.upper(), interval)
    key = f"{symbol.upper()}_{interval}"

    def _refetch_and_cache() -> pd.DataFrame:
        df = _fetch_chart(ticker, interval)
        _write_cache(path, df)
        return df

    def _background_refresh() -> None:
        try:
            _refetch_and_cache()
        except Exception:  # noqa: BLE001 - next call just tries again
            pass
        finally:
            _fetch_refreshing.discard(key)

    has_cache = path.exists()
    if has_cache and (time.time() - path.stat().st_mtime) < max_age_seconds:
        cached = _read_cache(path)
        if cached is not None:
            return cached
        has_cache = False

    if has_cache:
        # stale but present: don't block the caller on a possibly-very-slow Yahoo response -
        # hand back the stale data now and refresh in the background for next time.
        if key not in _fetch_refreshing:
            _fetch_refreshing.add(key)
            threading.Thread(target=_background_refresh, daemon=True).start()
        cached = _read_cache(path)
        if cached is not None:
            return cached

    # no usable cache at all - this call has no choice but to wait for the real fetch
    return _refetch_and_cache()


_last: dict[str, tuple[float, float, str]] = {}   # symbol -> (cache file mtime, last close, bar time)
_refreshing: set[str] = set()


def last_close(symbol: str, max_age_seconds: int = 150) -> tuple[float, str] | None:
    """Latest 5m close and its bar time WITHOUT waiting on Yahoo: reads the cache the live engine refreshes every tick.
    If the cache is older than max_age_seconds a refresh starts in the background and the (slightly old) cached price is
    returned now; only when there is no cache at all does it fetch synchronously. Pages call this on every refresh."""
    sym = symbol.upper()
    path = _cache_path(sym, "5m")
    if not path.exists():
        df = fetch(sym, "5m")
        return (float(df["close"].iloc[-1]), df.index[-1].isoformat()) if not df.empty else None
    mtime = path.stat().st_mtime
    hit = _last.get(sym)
    if not hit or hit[0] != mtime:
        try:
            df = pd.read_parquet(path, columns=["close"])
            if not df.empty:
                hit = (mtime, float(df["close"].iloc[-1]), df.index[-1].isoformat())
                _last[sym] = hit
        except Exception:  # noqa: BLE001 - file being replaced right now: keep the previous value
            pass
    if time.time() - mtime > max_age_seconds and sym not in _refreshing:
        _refreshing.add(sym)

        def _bg() -> None:
            try:
                fetch(sym, "5m", max_age_seconds=max_age_seconds)
            except Exception:  # noqa: BLE001
                pass
            finally:
                _refreshing.discard(sym)

        threading.Thread(target=_bg, daemon=True, name=f"px-{sym}").start()
    return (hit[1], hit[2]) if hit else None


def available_symbols() -> list[str]:
    return list(SYMBOLS.keys())
