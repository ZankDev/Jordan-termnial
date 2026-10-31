"""Free multi-year historical bars from Dukascopy, for the long-horizon trainer.

Yahoo Finance's free API (yfinance_source.py) hard-caps 5m/15m/30m requests at
the last 60 days — confirmed directly against their server
("15m data not available... must be within the last 60 days"). Dukascopy, a
real Swiss forex/CFD broker, publishes historical tick data for its own index
CFDs at a public, unauthenticated URL going back years — no signup, no key.

Important: this is Dukascopy's own index-CFD instrument (tracks the
underlying cash index), NOT the CME futures contract used everywhere else in
this app via yfinance_source (NQ=F/ES=F). Price action is closely correlated
but not tick-identical — no futures-specific overnight/rollover behavior.
Used only by the long-horizon walk-forward trainer, never the fast/live paths.

Tick format verified empirically against real downloaded files: each .bi5 file
is one hour of ticks, LZMA-compressed (old-style "alone" format), 20 bytes per
tick (big-endian: uint32 ms-offset, uint32 ask, uint32 bid, float32 ask-vol,
float32 bid-vol), prices scaled by /1000 for both instruments used here.

Cache integrity: every month has a sidecar (.meta.json) recording exactly which hours
were fetched OK, confirmed empty by Dukascopy (404 / empty file - final), or failed
transiently (network, 429, 5xx - NOT proof of missing data, retried later). An earlier
version cached any failure as "no data" forever, which left the multi-year history
~90% empty; only still-missing hours are refetched now, and coverage() reports how
complete the cache really is.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import lzma
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

SYMBOL_MAP = {"NQ": "USATECHIDXUSD", "ES": "USA500IDXUSD"}
PRICE_SCALE = 1000.0
DATAFEED_URL = "https://datafeed.dukascopy.com/datafeed/{symbol}/{year:04d}/{month0:02d}/{day:02d}/{hour:02d}h_ticks.bi5"
HEADERS = {"User-Agent": "Mozilla/5.0"}
TICK_DTYPE = np.dtype([("ms", ">u4"), ("ask", ">u4"), ("bid", ">u4"), ("askv", ">f4"), ("bidv", ">f4")])

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache" / "dukascopy"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# One shared session (connection pooling / keep-alive) reused across every
# request — a fresh requests.get() per call re-does the TLS handshake each
# time, which dominates wall-clock time when fetching thousands of small
# hourly files.
_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)
_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16)
_SESSION.mount("https://", _ADAPTER)

# Kept deliberately low: 48 concurrent workers looked like an attack to Dukascopy's edge
# and produced waves of HTTP 503s that the old code silently cached as "no data".
FETCH_WORKERS = 8
RETRIES = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
TRANSIENT_RETRY_SECONDS = 1800  # retry hours that failed transiently after 30 min (was 6 h: kept ES stuck at 28% while the archive was healthy again)
OUTAGE_BACKOFF_SECONDS = 1800  # after a failed probe, skip the network entirely for this long
MIN_LEGACY_DENSITY = 0.5  # a pre-sidecar cache month below this fraction of expected bars is untrusted

# Confirmed directly against the live endpoint (not a guess): a handful of plain sequential
# GETs to this host, ~1.5s apart, on a single thread, produced one HTTP 503 and then repeated
# full TCP *connect* timeouts - not "content missing", not one bad request, but the kind of
# pattern a CDN edge shows when it is actively rate-limiting a client, not merely unavailable.
# FETCH_WORKERS's concurrency cap alone does not prevent 8 threads from all opening their first
# connection within the same instant, which is exactly the burst shape that pattern points at.
# This makes every worker wait its turn for a shared minimum gap between connection *attempts*
# (not full round-trips - a slow response from one worker never blocks another's turn to start),
# spreading the same request volume out instead of bursting it, before deciding whether raising
# concurrency again is even worth trying.
MIN_REQUEST_GAP_SECONDS = 0.25  # ceiling ~4 new connection attempts/second across ALL workers combined
_rate_lock = threading.Lock()
_last_request_started_at = 0.0


def _pace_request() -> None:
    global _last_request_started_at
    with _rate_lock:
        wait = _last_request_started_at + MIN_REQUEST_GAP_SECONDS - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_request_started_at = time.time()

NY_TZ = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Restrict backfill to the NY trading session window to bound download volume —
# these strategies only care about NY-session price action anyway.
SESSION_START_HOUR_ET = 8
SESSION_END_HOUR_ET = 17

INTERVAL_PANDAS = {"5m": "5min", "15m": "15min", "30m": "30min"}
_BARS_PER_HOUR = {"5m": 12, "15m": 4, "30m": 2}

_last_outage_at = 0.0
_last_repair_at: dict[str, float] = {}

# Per-hour outcome of a fetch.
OK, EMPTY, TRANSIENT = "ok", "empty", "transient"


def _fetch_hour_ticks(duka_symbol: str, hour_utc: dt.datetime) -> tuple[pd.DataFrame, str]:
    url = DATAFEED_URL.format(
        symbol=duka_symbol, year=hour_utc.year, month0=hour_utc.month - 1, day=hour_utc.day, hour=hour_utc.hour
    )
    resp = None
    for attempt in range(RETRIES):
        _pace_request()
        try:
            resp = _SESSION.get(url, timeout=15)
            if resp.status_code == 200:
                break
            if resp.status_code == 404:
                return pd.DataFrame(), EMPTY
        except requests.RequestException:
            pass
        resp = None
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_SECONDS[attempt])
    if resp is None:
        return pd.DataFrame(), TRANSIENT
    if not resp.content:
        return pd.DataFrame(), EMPTY
    try:
        raw = lzma.decompress(resp.content, format=lzma.FORMAT_ALONE)
    except lzma.LZMAError:
        return pd.DataFrame(), TRANSIENT

    n = len(raw) // 20
    if n == 0:
        return pd.DataFrame(), EMPTY
    arr = np.frombuffer(raw[: n * 20], dtype=TICK_DTYPE)

    mid = (arr["ask"].astype(np.float64) + arr["bid"].astype(np.float64)) / 2.0 / PRICE_SCALE
    volume = arr["askv"].astype(np.float64) + arr["bidv"].astype(np.float64)
    timestamps = pd.to_datetime(hour_utc) + pd.to_timedelta(arr["ms"].astype(np.int64), unit="ms")
    return pd.DataFrame({"price": mid, "volume": volume}, index=pd.DatetimeIndex(timestamps, tz="UTC")), OK


def _session_hours_for_month(year: int, month: int, only_past: bool = True) -> list[dt.datetime]:
    """NY-session hours (UTC-aligned) of a month. With only_past, hours not yet fully
    published (within the last 2 hours) are excluded so an in-progress month is never
    reported as missing data that doesn't exist yet."""
    hours: list[dt.datetime] = []
    days_in_month = calendar.monthrange(year, month)[1]
    cutoff = dt.datetime.now(UTC) - dt.timedelta(hours=2)
    for day in range(1, days_in_month + 1):
        date = dt.date(year, month, day)
        if date.weekday() >= 5:
            continue
        ny_start = dt.datetime.combine(date, dt.time(SESSION_START_HOUR_ET), tzinfo=NY_TZ)
        ny_end = dt.datetime.combine(date, dt.time(SESSION_END_HOUR_ET), tzinfo=NY_TZ)
        h = ny_start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        end_utc = ny_end.astimezone(UTC)
        while h < end_utc:
            if not only_past or h < cutoff:
                hours.append(h)
            h += dt.timedelta(hours=1)
    return hours


def _month_cache_path(duka_symbol: str, year: int, month: int, interval: str) -> Path:
    return CACHE_DIR / f"{duka_symbol}_{interval}_{year}{month:02d}.parquet"


def _meta_path(duka_symbol: str, year: int, month: int, interval: str) -> Path:
    return CACHE_DIR / f"{duka_symbol}_{interval}_{year}{month:02d}.meta.json"


def _load_meta(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_meta(path: Path, meta: dict) -> None:
    path.write_text(json.dumps(meta), encoding="utf-8")


def _iso(h: dt.datetime) -> str:
    return h.strftime("%Y-%m-%dT%H")


def _bars_from_ticks(tick_frames: list[pd.DataFrame], interval: str) -> pd.DataFrame:
    if not tick_frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    ticks = pd.concat(tick_frames).sort_index()
    pandas_interval = INTERVAL_PANDAS[interval]
    bars = ticks["price"].resample(pandas_interval).ohlc()
    bars["volume"] = ticks["volume"].resample(pandas_interval).sum()
    bars = bars.dropna(subset=["open"])
    bars.index = bars.index.tz_convert(NY_TZ)
    bars.index.name = "datetime"
    return bars


def _month_list(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    cursor = dt.date(start.year, start.month, 1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = dt.date(cursor.year + (cursor.month // 12), (cursor.month % 12) + 1, 1)
    return months


def _load_month(duka_symbol: str, y: int, m: int, interval: str) -> tuple[pd.DataFrame | None, dict | None]:
    path = _month_cache_path(duka_symbol, y, m, interval)
    bars = None
    if path.exists():
        try:
            bars = pd.read_parquet(path)
        except Exception:
            bars = None
        if bars is not None and not bars.empty and not isinstance(bars.index, pd.DatetimeIndex):
            # defensive: a malformed cache entry should never silently corrupt a downstream
            # strategy's df.index.date grouping
            path.unlink(missing_ok=True)
            bars = None
    return bars, _load_meta(_meta_path(duka_symbol, y, m, interval))


def _hours_to_fetch(
    bars: pd.DataFrame | None, meta: dict | None, y: int, m: int, interval: str, now: float
) -> list[dt.datetime]:
    """Which session hours of this month still need a network fetch (empty = settled)."""
    expected = _session_hours_for_month(y, m)
    if not expected:
        return []
    if bars is None:
        return expected
    if meta is None:
        # cache written before per-hour tracking existed: trust it only if it is dense
        density = len(bars) / (len(expected) * _BARS_PER_HOUR[interval])
        return expected if density < MIN_LEGACY_DENSITY else []
    settled = set(meta.get("ok", [])) | set(meta.get("empty", []))
    missing = [h for h in expected if _iso(h) not in settled]
    if not missing:
        return []
    failed_recently = (now - meta.get("fetched_at", 0)) < TRANSIENT_RETRY_SECONDS
    known_transient = set(meta.get("transient", []))
    if failed_recently and all(_iso(h) in known_transient for h in missing):
        return []  # every missing hour already failed a moment ago; back off
    return missing


def coverage(symbol: str, start: dt.date, end: dt.date, interval: str = "5m") -> float:
    """Fraction (0-1) of expected NY-session hours in [start, end] that are actually
    settled in the cache (fetched OK, or confirmed empty by Dukascopy). A month cached
    before per-hour tracking existed is judged by bar density instead. This gates deep
    validation: 0.09 means the 'multi-year' history is ~9% real data."""
    duka_symbol = SYMBOL_MAP.get(symbol.upper())
    if not duka_symbol:
        return 0.0
    covered = total = 0.0
    for y, m in _month_list(start, end):
        expected = _session_hours_for_month(y, m)
        if not expected:
            continue
        bars, meta = _load_month(duka_symbol, y, m, interval)
        total += len(expected)
        if bars is None:
            continue
        if meta is None:
            covered += min(1.0, len(bars) / (len(expected) * _BARS_PER_HOUR[interval])) * len(expected)
        else:
            settled = set(meta.get("ok", [])) | set(meta.get("empty", []))
            covered += sum(1 for h in expected if _iso(h) in settled)
    return covered / total if total else 0.0


def coverage_by_month(symbol: str, start: dt.date, end: dt.date, interval: str = "5m") -> list[tuple[int, int, float, int]]:
    """[(year, month, settled_fraction, expected_hours)] newest month first. Same rule as
    coverage() but one pass, so a caller can judge several look-back windows from one read."""
    duka_symbol = SYMBOL_MAP.get(symbol.upper())
    out: list[tuple[int, int, float, int]] = []
    if not duka_symbol:
        return out
    for y, m in reversed(_month_list(start, end)):
        expected = _session_hours_for_month(y, m)
        if not expected:
            continue
        bars, meta = _load_month(duka_symbol, y, m, interval)
        if bars is None:
            frac = 0.0
        elif meta is None:
            frac = min(1.0, len(bars) / (len(expected) * _BARS_PER_HOUR[interval]))
        else:
            settled = set(meta.get("ok", [])) | set(meta.get("empty", []))
            frac = sum(1 for h in expected if _iso(h) in settled) / len(expected)
        out.append((y, m, frac, len(expected)))
    return out


def fetch_bars(
    symbol: str, start: dt.date, end: dt.date, interval: str, max_repair_months: int | None = None
) -> pd.DataFrame:
    """OHLCV bars for `symbol` ('NQ'/'ES') between start/end dates, at 5m/15m/30m,
    NY-session hours only, cached per (symbol, interval, month) with a sidecar recording
    which hours were fetched OK, confirmed empty, or failed transiently. Only still-missing
    hours are ever refetched, and a transient failure is never cached as 'no data'.
    max_repair_months caps how many months one call will touch (newest first) so a
    trainer cycle can't stall on a huge backfill; None = repair everything."""
    global _last_outage_at
    if interval not in INTERVAL_PANDAS:
        raise ValueError(f"Unsupported interval {interval!r}; use one of {list(INTERVAL_PANDAS)}")
    duka_symbol = SYMBOL_MAP.get(symbol.upper())
    if not duka_symbol:
        raise ValueError(f"No Dukascopy mapping for symbol {symbol!r}")

    months = _month_list(start, end)
    now = time.time()
    state: dict[tuple[int, int], tuple[pd.DataFrame | None, dict | None]] = {}
    work: list[tuple[int, int, list[dt.datetime]]] = []
    for y, m in months:
        bars, meta = _load_month(duka_symbol, y, m, interval)
        state[(y, m)] = (bars, meta)
        need = _hours_to_fetch(bars, meta, y, m, interval, now)
        if need:
            work.append((y, m, need))

    if work and max_repair_months != 0 and now - _last_outage_at >= OUTAGE_BACKOFF_SECONDS:
        work.sort(key=lambda w: (w[0], w[1]), reverse=True)  # newest months first
        if max_repair_months is not None:
            work = work[:max_repair_months]
        # Probe one hour first: if the archive is down, don't burn minutes retrying thousands.
        first_y, first_m, first_hours = work[0]
        probe_df, probe_status = _fetch_hour_ticks(duka_symbol, first_hours[0])
        if probe_status == TRANSIENT:
            _last_outage_at = now
        else:
            results: dict[tuple[int, int], dict] = {
                (y, m): {"frames": [], "ok": [], "empty": [], "transient": []} for y, m, _ in work
            }
            if probe_status == OK:
                results[(first_y, first_m)]["frames"].append(probe_df)
            results[(first_y, first_m)][probe_status].append(_iso(first_hours[0]))
            todo = [
                (y, m, h)
                for y, m, hours in work
                for h in hours
                if not (y == first_y and m == first_m and h == first_hours[0])
            ]
            good = bad = 0
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
                futures = {ex.submit(_fetch_hour_ticks, duka_symbol, h): (y, m, h) for y, m, h in todo}
                for fut in as_completed(futures):
                    if fut.cancelled():
                        continue
                    y, m, h = futures[fut]
                    df, status = fut.result()
                    if status == OK and not df.empty:
                        results[(y, m)]["frames"].append(df)
                    results[(y, m)][status].append(_iso(h))
                    good, bad = (good + 1, bad) if status != TRANSIENT else (good, bad + 1)
                    # Circuit breaker: an archive that is mostly failing (as Dukascopy's edge was)
                    # is not worth minutes of retries per cycle - stop, keep what we got, back off.
                    if bad >= 16 and bad > 3 * good:
                        _last_outage_at = time.time()
                        for f in futures:
                            f.cancel()
                        break

            for (y, m), r in results.items():
                old_bars, old_meta = state[(y, m)]
                new_bars = _bars_from_ticks(r["frames"], interval)
                if old_bars is not None and not old_bars.empty and not new_bars.empty:
                    # hours are disjoint 5m-aligned buckets, so concat is safe; newest fetch wins on overlap
                    merged = pd.concat([old_bars, new_bars]).sort_index()
                    merged = merged[~merged.index.duplicated(keep="last")]
                elif not new_bars.empty:
                    merged = new_bars
                else:
                    merged = old_bars if old_bars is not None else new_bars
                prior = old_meta or {"ok": [], "empty": [], "transient": []}
                ok = sorted(set(prior.get("ok", [])) | set(r["ok"]))
                empty = sorted(set(prior.get("empty", [])) | set(r["empty"]))
                settled = set(ok) | set(empty)
                transient = sorted(h for h in set(prior.get("transient", [])) | set(r["transient"]) if h not in settled)
                merged.to_parquet(_month_cache_path(duka_symbol, y, m, interval))
                meta = {"ok": ok, "empty": empty, "transient": transient, "fetched_at": time.time()}
                _save_meta(_meta_path(duka_symbol, y, m, interval), meta)
                state[(y, m)] = (merged, meta)

    frames = [state[ym][0] for ym in months if state[ym][0] is not None and not state[ym][0].empty]
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    bars = pd.concat(frames).sort_index()
    bars = bars[~bars.index.duplicated(keep="first")]
    start_ts = pd.Timestamp(start, tz=NY_TZ)
    end_ts = pd.Timestamp(end, tz=NY_TZ) + pd.Timedelta(days=1)
    return bars[(bars.index >= start_ts) & (bars.index < end_ts)]


def repair_step(
    symbol: str, years: int = 8, interval: str = "5m", max_months: int = 2, min_gap_seconds: int = 1800
) -> float:
    """Background-safe incremental repair: refetches at most `max_months` of the newest
    incomplete months, at most once per `min_gap_seconds`. Returns current coverage.
    Called from the trainer so the deep-validation history heals a little each cycle
    instead of blocking one cycle on a multi-year download."""
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * years + 5)
    key = symbol.upper()
    if time.time() - _last_repair_at.get(key, 0.0) >= min_gap_seconds and coverage(symbol, start, end, interval) < 0.98:
        _last_repair_at[key] = time.time()
        fetch_bars(symbol, start, end, interval, max_repair_months=max_months)
    return coverage(symbol, start, end, interval)
