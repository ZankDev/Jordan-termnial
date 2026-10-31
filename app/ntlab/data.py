"""Historical bars for the local provider: Dukascopy 5-minute index CFDs as proxies for the CME futures
(USA500IDXUSD -> ES, point value 50; USATECHIDXUSD -> NQ, point value 20). Bar times are converted to bar CLOSE times in
New York, like NinjaTrader's. The NinjaTrader provider uses NinjaTrader's own data instead — that is the real test.
"""
from __future__ import annotations

import glob
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2] / "data_cache"
SOURCES = {"ES": ("USA500IDXUSD_5m", 50.0, 0.25), "NQ": ("USATECHIDXUSD_5m", 20.0, 0.25)}


@lru_cache(maxsize=4)
def bars(symbol: str = "ES") -> dict:
    name, pv, tick = SOURCES[symbol.upper()]
    cache = ROOT / "ntlab" / f"{name}.parquet"
    files = sorted(glob.glob(str(ROOT / "dukascopy" / f"{name}_*.parquet")))
    if not files:
        raise FileNotFoundError(f"no Dukascopy files for {name}")
    if cache.exists() and cache.stat().st_mtime >= max(os.path.getmtime(f) for f in files):
        df = pd.read_parquet(cache)
    else:
        df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df.index = df.index.tz_convert("America/New_York") + pd.Timedelta(minutes=5)  # bar close time
        df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache)
    idx = df.index
    return {
        "symbol": symbol.upper(), "point_value": pv, "tick": tick, "time": idx,
        "o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float), "l": df["low"].to_numpy(float), "c": df["close"].to_numpy(float),
        "v": np.maximum(df["volume"].to_numpy(float), 1.0),
        "minute": (idx.hour * 60 + idx.minute).to_numpy(np.int32),
        "day": ((idx.tz_localize(None).normalize() - pd.Timestamp("1970-01-01")).days).to_numpy(np.int32),
    }


def window(b: dict, start: str | None, end: str | None) -> dict:
    """The same arrays cut to [start, end) (dates in New York)."""
    t = b["time"]
    lo = 0 if not start else int(t.searchsorted(pd.Timestamp(start, tz="America/New_York")))
    hi = len(t) if not end else int(t.searchsorted(pd.Timestamp(end, tz="America/New_York")))
    out = {k: (v[lo:hi] if hasattr(v, "__len__") and not isinstance(v, str) else v) for k, v in b.items()}
    return out
