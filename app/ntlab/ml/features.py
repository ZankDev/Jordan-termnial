"""Alpha factors for every 5-minute bar (Jansen ch. 8-9), computed only from data known at that bar's close.

Price-scale-free on purpose (returns, ATR multiples, z-scores) so one model works across years and price levels.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

FEATURES = ["ret_1", "ret_3", "ret_6", "ret_12", "ret_24", "ret_78", "vol_12", "vol_48", "vol_ratio", "atr_pct", "rsi_14", "rsi_3",
            "d_sma20", "d_sma50", "d_sma200", "d_vwap", "day_ret", "range_pos", "gap", "or_width", "vol_z", "slope_12", "slope_48",
            "prev_day_ret", "prev_day_range", "tod", "dow"]


def _rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _slope(c: pd.Series, n: int) -> pd.Series:
    """Least-squares slope of the last n closes (trend-scanning style), per bar."""
    x = np.arange(n) - (n - 1) / 2
    w = x / (x ** 2).sum()
    return c.rolling(n).apply(lambda a: float(np.dot(w, a)), raw=True)


@lru_cache(maxsize=4)
def frame(symbol: str = "ES") -> pd.DataFrame:
    from app.ntlab import data
    return compute(data.bars(symbol))


def leak_check(symbol: str = "ES", cuts: int = 3, seed: int = 1) -> dict:
    """Look-ahead guard: recompute every feature with all data AFTER a cut point deleted; the rows before the cut must be
    identical to the full computation. Any difference = the feature used the future. Run by the search before it starts."""
    from app.ntlab import data
    b, full = data.bars(symbol), frame(symbol)
    rng = np.random.default_rng(seed)
    bad: dict[str, int] = {}
    for cut in rng.integers(int(len(full) * 0.3), int(len(full) * 0.95), size=cuts):
        part = compute({k: (v[:cut] if hasattr(v, "__len__") and not isinstance(v, str) else v) for k, v in b.items()})
        a, z = full.iloc[: cut - 1].to_numpy(np.float64), part.iloc[: cut - 1].to_numpy(np.float64)
        diff = ~(np.isclose(a, z, rtol=1e-4, atol=1e-6, equal_nan=True))
        for j, col in enumerate(FEATURES):
            if diff[:, j].any():
                bad[col] = bad.get(col, 0) + int(diff[:, j].sum())
    return {"ok": not bad, "leaking": bad}


def compute(b: dict) -> pd.DataFrame:
    t = b["time"]
    df = pd.DataFrame({"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}, index=t)
    c = df["c"]
    lr = np.log(c).diff()
    tr = pd.concat([df["h"] - df["l"], (df["h"] - c.shift()).abs(), (df["l"] - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    f = pd.DataFrame(index=t)
    for n in (1, 3, 6, 12, 24, 78):
        f[f"ret_{n}"] = np.log(c / c.shift(n)) / (atr / c)            # returns in ATR units
    f["vol_12"] = lr.rolling(12).std() * 1e4
    f["vol_48"] = lr.rolling(48).std() * 1e4
    f["vol_ratio"] = f["vol_12"] / f["vol_48"]
    f["atr_pct"] = atr / c * 100
    f["rsi_14"] = _rsi(c, 14)
    f["rsi_3"] = _rsi(c, 3)
    for n in (20, 50, 200):
        f[f"d_sma{n}"] = (c - c.rolling(n).mean()) / atr
    day = pd.Series(b["day"], index=t)
    minute = pd.Series(b["minute"], index=t)
    g = df.groupby(day.values)
    rth = minute > 570                                                # session from 09:30
    pv = ((df["h"] + df["l"] + c) / 3 * df["v"]).where(rth, 0.0)
    vv = df["v"].where(rth, 0.0)
    vwap = pv.groupby(day.values).cumsum() / vv.groupby(day.values).cumsum().replace(0, np.nan)
    f["d_vwap"] = (c - vwap) / atr
    first_open = g["o"].transform("first")
    f["day_ret"] = (c - first_open) / atr
    hi_sofar, lo_sofar = g["h"].cummax(), g["l"].cummin()
    f["range_pos"] = (c - lo_sofar) / (hi_sofar - lo_sofar).replace(0, np.nan)
    daily = df.groupby(day.values).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"))
    # yesterday's return: pct_change() at day d is d's OWN close-to-close return (future at signal time) -> shift one day
    prev_c, prev_ret = daily["c"].shift(), daily["c"].pct_change().shift() * 100
    prev_rng = (daily["h"] - daily["l"]) / daily["c"] * 100
    f["gap"] = ((first_open - day.map(prev_c)) / atr).values
    f["prev_day_ret"] = day.map(prev_ret).values
    f["prev_day_range"] = day.map(prev_rng.shift()).values
    in_or = rth & (minute <= 600)                                     # opening range 09:30-10:00
    or_w = (df["h"].where(in_or).groupby(day.values).cummax() - df["l"].where(in_or).groupby(day.values).cummin())
    f["or_width"] = (or_w.groupby(day.values).ffill() / atr).where(minute > 600)
    f["vol_z"] = np.log(df["v"] / df["v"].rolling(48 * 5).mean())
    f["slope_12"] = _slope(c, 12) / atr
    f["slope_48"] = _slope(c, 48) / atr
    f["tod"] = minute.values
    f["dow"] = t.dayofweek
    return f[FEATURES].replace([np.inf, -np.inf], np.nan).astype("float32")


def at_signal(symbol: str, entry_times: pd.Series) -> pd.DataFrame:
    """Feature rows of the SIGNAL bar = the bar before each entry fill (entries fill at the next bar's open)."""
    f = frame(symbol)
    idx = f.index.searchsorted(pd.DatetimeIndex(entry_times).tz_localize("America/New_York") if pd.DatetimeIndex(entry_times).tz is None else entry_times) - 1
    idx = np.clip(idx, 0, len(f) - 1)
    return f.iloc[idx].reset_index(drop=True)
