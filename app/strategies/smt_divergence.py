"""SMT divergence + market structure shift (ICT-style), traded on ES or NQ with the other index as the correlated pair.

1. SMT divergence: the traded index prints a higher high (or lower low) than its previous swing, while the paired index FAILS to confirm it (its high around that
   swing is lower than around the previous swing; mirrored for lows).
2. Market structure shift: after the divergent swing, price breaks the most recent opposite swing (a close below the swing low after a bearish SMT; above the swing
   high after a bullish one).
3. Entry on the next bar's open, stop beyond the divergent swing, target `rr` times the risk, flat by the session end.

No look-ahead: a swing is only known `swing_n` bars after it prints. Fills are pessimistic (stop first when a bar touches both, gap-aware stops).
The pair's bars come from the Dukascopy cache (history) and Yahoo (the recent tail); with no pair data the strategy simply makes no trades.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from app.strategies import indicators as ind
from app.strategies.base import ParamSpec, Strategy, Trade

SESSION_OPEN_MIN = 9 * 60 + 30
_cache: dict[tuple, tuple[str, pd.DataFrame] | None] = {}


def _bars(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
    """Best available 5-minute bars for `symbol` covering [start, end]: the deep Dukascopy cache plus the Yahoo tail."""
    frames = []
    try:
        from app.optimizer import promotion

        months = promotion.deep_plan(symbol)[0]
        if months:
            d = promotion._deep_df(symbol, months)
            if d is not None and not d.empty:
                frames.append(d)
    except Exception:  # noqa: BLE001
        pass
    if not frames or frames[0].index.max() < end - pd.Timedelta(days=1):
        try:
            from app.data import yfinance_source

            y = yfinance_source.fetch(symbol, "5m", max_age_seconds=3600)
            if y is not None and not y.empty:
                frames.append(y)
        except Exception:  # noqa: BLE001
            pass
    if not frames:
        return None
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out


def _pair_of(df: pd.DataFrame) -> pd.DataFrame | None:
    """The other index's bars aligned to df's index (None when unavailable). Which index `df` is gets decided by matching prices."""
    key = (df.index[0], df.index[-1], len(df))
    if key in _cache:
        hit = _cache[key]
        return None if hit is None else hit[1]
    res = None
    try:
        cands = {s: _bars(s, df.index[0], df.index[-1]) for s in ("ES", "NQ")}
        probe = df["close"].iloc[::max(1, len(df) // 400)]
        err = {}
        for s, b in cands.items():
            if b is None:
                continue
            al = b["close"].reindex(probe.index)
            ok = al.notna()
            if ok.sum() >= 20:
                err[s] = float((al[ok] - probe[ok]).abs().mean())
        if len(err) == 2 or ("ES" in cands and "NQ" in cands):
            me = min(err, key=err.get)
            other = "NQ" if me == "ES" else "ES"
            if cands[other] is not None:
                res = (other, cands[other].reindex(df.index))
    except Exception:  # noqa: BLE001
        res = None
    _cache[key] = res
    if len(_cache) > 8:
        _cache.pop(next(iter(_cache)))
    return None if res is None else res[1]


def _pivots(arr: np.ndarray, n: int, high: bool) -> np.ndarray:
    """Boolean array: bar i is a swing high (low) over n bars each side. Only usable from bar i+n on."""
    s = pd.Series(arr)
    w = s.rolling(2 * n + 1, center=True)
    ext = w.max() if high else w.min()
    return (s == ext).fillna(False).values


class SmtDivergence(Strategy):
    def __init__(self):
        super().__init__(
            name="smt_divergence",
            description="SMT divergence between ES and NQ + market-structure shift, entry on the break, target rr x risk.",
            params=[
                ParamSpec("swing_n", default=3, min=2, max=8, step=1, kind="int"),
                ParamSpec("lookback_bars", default=60, min=20, max=150, step=10, kind="int"),
                ParamSpec("max_wait_bars", default=24, min=6, max=60, step=6, kind="int"),
                ParamSpec("rr", default=2.0, min=1.0, max=3.0, step=0.25),
                ParamSpec("stop_buffer_atr", default=0.1, min=0.0, max=0.5, step=0.1),
                ParamSpec("start_min", default=0, min=0, max=240, step=15, kind="int"),
                ParamSpec("end_min", default=330, min=120, max=390, step=30, kind="int"),
                ParamSpec("max_hold_bars", default=48, min=12, max=96, step=12, kind="int"),
                ParamSpec("atr_period", default=14, min=14, max=14, step=1, kind="int"),
            ],
        )

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        if len(df) < 500:
            return []
        pair = _pair_of(df)
        if pair is None:
            return []
        n = int(params["swing_n"]); lookback = int(params["lookback_bars"]); wait = int(params["max_wait_bars"])
        rr = float(params["rr"]); buf = float(params["stop_buffer_atr"])
        start_min, end_min = int(params["start_min"]), int(params["end_min"]); hold = int(params["max_hold_bars"])

        hi, lo, op, cl = (df[k].values.astype(float) for k in ("high", "low", "open", "close"))
        ph, pl = pair["high"].values.astype(float), pair["low"].values.astype(float)
        atr = ind.atr(df, int(params["atr_period"])).values
        idx = df.index
        mins = idx.hour.values * 60 + idx.minute.values - SESSION_OPEN_MIN        # minutes after the 9:30 open
        day = idx.normalize().values
        sh, sl_ = _pivots(hi, n, True), _pivots(lo, n, False)
        N = len(df)
        last_sh: list[int] = []; last_sl: list[int] = []     # confirmed swing indices (self)
        setup = None                                          # (side, level_to_break, stop_ref, armed_at)
        trades: list[Trade] = []
        blocked = -1

        def pair_ext(i: int, high: bool) -> float:
            seg = (ph if high else pl)[max(0, i - n): i + n + 1]
            seg = seg[~np.isnan(seg)]
            if not seg.size:
                return np.nan
            return float(seg.max() if high else seg.min())

        for t in range(2 * n + 1, N - 1):
            p = t - n   # the bar whose swing status was just confirmed
            if sh[p]:
                prev = [q for q in last_sh if p - q <= lookback]
                if prev:
                    q = prev[-1]
                    a, b = pair_ext(p, True), pair_ext(q, True)
                    if hi[p] > hi[q] and a == a and b == b and a < b:           # self HH, pair LH -> bearish SMT
                        lows = [r for r in last_sl if q <= r <= p]
                        if lows:
                            setup = ("short", float(min(lo[r] for r in lows)), float(hi[p]), t)
                last_sh.append(p)
            if sl_[p]:
                prev = [q for q in last_sl if p - q <= lookback]
                if prev:
                    q = prev[-1]
                    a, b = pair_ext(p, False), pair_ext(q, False)
                    if lo[p] < lo[q] and a == a and b == b and a > b:           # self LL, pair HL -> bullish SMT
                        highs = [r for r in last_sh if q <= r <= p]
                        if highs:
                            setup = ("long", float(max(hi[r] for r in highs)), float(lo[p]), t)
                last_sl.append(p)
            if len(last_sh) > 40: last_sh = last_sh[-40:]
            if len(last_sl) > 40: last_sl = last_sl[-40:]

            if setup is None or t <= blocked:
                continue
            side, level, ref, armed = setup
            if t - armed > wait:
                setup = None
                continue
            if not (start_min <= mins[t] <= end_min) or day[t] != day[armed]:
                if day[t] != day[armed]:
                    setup = None
                continue
            broke = cl[t] < level if side == "short" else cl[t] > level
            if not broke:
                continue
            a_ = atr[t]
            if a_ != a_ or a_ <= 0:
                setup = None
                continue
            ei = t + 1
            entry = float(op[ei])
            stop = ref + buf * a_ if side == "short" else ref - buf * a_
            risk = (stop - entry) if side == "short" else (entry - stop)
            if risk <= 0 or risk > 4 * a_ or risk < 0.3 * a_:
                setup = None
                continue
            target = entry - rr * risk if side == "short" else entry + rr * risk
            mx = min(ei + hold, N - 1)
            xi, xp, xr = None, None, "timeout"
            for j in range(ei, mx + 1):
                if mins[j] > end_min + 30 or day[j] != day[ei]:      # flat by the session end / never overnight
                    xi, xp, xr = j - 1 if j > ei else ei, float(cl[j - 1] if j > ei else op[ei]), "session_close"
                    break
                hs = hi[j] >= stop if side == "short" else lo[j] <= stop
                ht = lo[j] <= target if side == "short" else hi[j] >= target
                if hs:
                    xp = max(stop, op[j]) if side == "short" else min(stop, op[j])
                    xi, xr = j, "stop"
                    break
                if ht:
                    xi, xp, xr = j, float(target), "target"
                    break
            if xi is None:
                xi, xp = mx, float(cl[mx])
            trades.append(Trade(entry_time=idx[ei], exit_time=idx[xi], side=side, entry_price=entry, exit_price=float(xp),
                                stop_price=float(stop), target_price=float(target), exit_reason=xr))
            blocked = xi
            setup = None
        return trades
