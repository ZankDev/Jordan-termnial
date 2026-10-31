"""Pure-pandas technical indicators shared by the signal-based strategies.

No ta-lib / extra dependency — these are short enough to just compute directly.
"""
from __future__ import annotations

import functools
from collections import OrderedDict

import numpy as np
import pandas as pd

# The trainer re-evaluates the same strategy against the same fold dataframes many
# times per search (up to 80 candidates x k folds), and a lot of those candidates
# share the same period/window params (especially the mutation-based half of the
# search, which perturbs only a few params off a known-good parent) — so the same
# indicator gets recomputed on the same data over and over. This is a small,
# bounded, in-memory memoization keyed off a cheap content fingerprint (not just
# id(), which a long-running 24/7 process could eventually reuse after garbage
# collection) rather than a full dataframe hash, so it stays cheap to check on
# every call. Nothing in this codebase mutates an indicator's returned Series in
# place (checked), so caching by reference is safe.
_CACHE: "OrderedDict" = OrderedDict()
_CACHE_MAX = 4000


def _cached(fn):
    name = fn.__name__

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            data = args[0]
            idx = data.index
            key = (
                name, id(data), len(data),
                idx[0].value if len(idx) else None,
                idx[-1].value if len(idx) else None,
                args[1:], tuple(sorted(kwargs.items())),
            )
        except Exception:
            return fn(*args, **kwargs)  # unhashable/unexpected input: just skip caching

        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

        result = fn(*args, **kwargs)
        _CACHE[key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        return result

    return wrapper


@_cached
def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


@_cached
def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period).mean()


@_cached
def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


@_cached
def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(s, fast) - ema(s, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


@_cached
def bollinger(s: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = sma(s, period)
    std = s.rolling(period).std()
    return mid, mid + num_std * std, mid - num_std * std


@_cached
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


@_cached
def donchian(df: pd.DataFrame, period: int = 20):
    return df["high"].rolling(period).max(), df["low"].rolling(period).min()


@_cached
def session_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    day = df.index.date
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = df["volume"].groupby(day).cumsum()
    return cum_pv / cum_vol.replace(0, np.nan)


@_cached
def prev_day_high_low(df: pd.DataFrame):
    daily = df.groupby(df.index.date).agg(day_high=("high", "max"), day_low=("low", "min"))
    prev = daily.shift(1)
    day_keys = pd.Index(df.index.date)
    prev_high = prev["day_high"].reindex(day_keys)
    prev_low = prev["day_low"].reindex(day_keys)
    return pd.Series(prev_high.values, index=df.index), pd.Series(prev_low.values, index=df.index)
