"""24-hour futures history for the backtest: the hours the main Dukascopy cache skips.

dukascopy_source only stores the New York session (08:00-17:00 ET) - it bounds the download and the trainer's coverage maths. This module downloads
everything else the market trades (Globex: Sunday 18:00 ET -> Friday 17:00 ET, with the daily 17:00-18:00 ET pause) into a SEPARATE cache,
data_cache/dukascopy_24h/, so the trainer's own cache and its coverage checks are never touched. `all_day_bars()` merges both into one 24-hour series.

Resumable: every month keeps a sidecar of the hours already fetched. Reuses dukascopy_source's fetcher and its request pacing (<= 4 requests/second).
usage: python -m app.data.overnight_source download ES 60      # last 60 months of ES overnight bars
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from app.data import dukascopy_source as dk

OUT = Path(__file__).resolve().parent.parent.parent / "data_cache" / "dukascopy_24h"
OUT.mkdir(parents=True, exist_ok=True)
UTC, NY = dk.UTC, dk.NY_TZ


def _wanted(h_utc: dt.datetime) -> bool:
    """An hour the futures market trades that the main cache does not hold."""
    ny = h_utc.astimezone(NY)
    wd, hr = ny.weekday(), ny.hour
    if wd == 5 or (wd == 4 and hr >= 17) or (wd == 6 and hr < 18):
        return False                      # weekend closure (Fri 17:00 ET -> Sun 18:00 ET)
    if wd < 4 and hr == 17:
        return False                      # the daily 17:00-18:00 ET pause
    if wd in (0, 1, 2, 3, 4) and dk.SESSION_START_HOUR_ET <= hr < dk.SESSION_END_HOUR_ET:
        return False                      # already in the main cache
    return True


def _paths(sym: str, y: int, m: int) -> tuple[Path, Path]:
    return OUT / f"{dk.SYMBOL_MAP[sym]}_5m_{y}{m:02d}.parquet", OUT / f"{dk.SYMBOL_MAP[sym]}_5m_{y}{m:02d}.done.json"


def _month_hours(y: int, m: int, now: dt.datetime) -> list[dt.datetime]:
    start = dt.datetime(y, m, 1, tzinfo=UTC)
    end = dt.datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=UTC)
    out, h = [], start
    while h < min(end, now - dt.timedelta(hours=2)):
        if _wanted(h):
            out.append(h)
        h += dt.timedelta(hours=1)
    return out


def download_month(sym: str, y: int, m: int, now: dt.datetime) -> tuple[int, int]:
    """-> (hours fetched now, hours still missing)."""
    bars_path, done_path = _paths(sym, y, m)
    done = set(json.loads(done_path.read_text())) if done_path.exists() else set()
    bars = pd.read_parquet(bars_path) if bars_path.exists() else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    todo = [h for h in _month_hours(y, m, now) if dk._iso(h) not in done]
    got, new_frames = 0, []

    def work(h):
        return h, *dk._fetch_hour_ticks(dk.SYMBOL_MAP[sym], h)

    with ThreadPoolExecutor(dk.FETCH_WORKERS) as pool:
        for h, ticks, status in pool.map(work, todo):
            if status == dk.TRANSIENT:
                continue
            done.add(dk._iso(h))
            got += 1
            if status == dk.OK and not ticks.empty:
                b = dk._bars_from_ticks([ticks], "5m")
                if not b.empty:
                    new_frames.append(b)
    if new_frames:
        bars = pd.concat([bars] + new_frames)
        bars = bars[~bars.index.duplicated(keep="first")].sort_index()
        bars.to_parquet(bars_path)
    done_path.write_text(json.dumps(sorted(done)))
    return got, len([h for h in todo if dk._iso(h) not in done])


def download(sym: str, months: int) -> None:
    now = dt.datetime.now(UTC)
    y, m = now.year, now.month
    plan = []
    for _ in range(months):
        plan.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    t0 = time.time()
    for y, m in reversed(plan):
        got, missing = download_month(sym, y, m, now)
        print(f"{sym} {y}-{m:02d}: fetched {got} hours, {missing} still missing  ({time.time() - t0:.0f}s)", flush=True)


def all_day_bars(sym: str, main: pd.DataFrame | None = None) -> pd.DataFrame:
    """The 24-hour 5-minute series: the main (NY session) cache merged with the overnight cache."""
    if main is None:
        from app.optimizer import promotion

        main = promotion._deep_df(sym, promotion.deep_plan(sym)[0])
    parts = [main]
    for p in sorted(OUT.glob(f"{dk.SYMBOL_MAP[sym]}_5m_*.parquet")):
        parts.append(pd.read_parquet(p))
    df = pd.concat(parts)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df[df.index >= main.index.min()]


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "download":
        download(sys.argv[2].upper(), int(sys.argv[3]))
    else:
        print(__doc__)
