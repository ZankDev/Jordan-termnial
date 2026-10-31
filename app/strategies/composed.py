"""Generated strategies: a base session strategy plus searchable ENTRY FILTERS.

The registry's strategies are single-rule: nothing in the trainer could ask "is this breakout better only before 11:00, or
only on calm days, or only with the daily trend?". `FilteredStrategy(base)` adds those questions as parameters, so the
existing search (random + elite mutation + neighbours + walk-forward + sealed holdout + long-history gate) can discover
which filters help instead of a human guessing. Every filter has an OFF value as its default, so the plain base strategy is
one point of the search space: with all filters off the trade list is identical to the base strategy's (checked in the
verification script).

Parameters:  b_<name>      the base strategy's own parameters, unchanged
             f_start_min   earliest entry, minutes after the 09:30 New York open        (0 = off)
             f_end_min     latest entry, minutes after the open                           (390 = off)
             f_skip_dow    skip one weekday: 1 Mon .. 5 Fri                               (0 = off)
             f_atr_lo/hi   trade only when the daily-range percentile (vs the last 120 days) is inside this band  (0 / 100 = off)
             f_trend       1 = only with the daily trend (prior close vs its 20-day average), 2 = only against it    (0 = off)
             f_gap_max     skip days whose open gap exceeds this many daily-ATRs          (10 = off)

Implementation note: the filters are applied to the base strategy's finished trades. That is exact for strategies that take at
most one trade per session (the three opening-range strategies this is registered for): dropping a trade cannot change which
later trade would have happened the same day. It would not be exact for multi-trade-per-day strategies, so it is not used there.
"""
from __future__ import annotations

import datetime as dt

from typing import Any

import numpy as np
import pandas as pd

from app.strategies.base import ParamSpec, Strategy, Trade

OPEN_MINUTE = 9 * 60 + 30
NY = "America/New_York"

FILTER_SPECS = [
    ParamSpec("f_start_min", default=0, min=0, max=240, step=15, kind="int"),
    ParamSpec("f_end_min", default=390, min=30, max=390, step=15, kind="int"),
    ParamSpec("f_skip_dow", default=0, min=0, max=5, step=1, kind="int"),
    ParamSpec("f_atr_lo", default=0, min=0, max=60, step=10, kind="int"),
    ParamSpec("f_atr_hi", default=100, min=50, max=100, step=10, kind="int"),
    ParamSpec("f_trend", default=0, min=0, max=2, step=1, kind="int"),
    ParamSpec("f_gap_max", default=10.0, min=0.5, max=10.0, step=0.5),
]
_OFF = {"f_start_min": 0, "f_end_min": 390, "f_skip_dow": 0, "f_atr_lo": 0, "f_atr_hi": 100, "f_trend": 0, "f_gap_max": 10.0}

_feat_cache: dict[tuple[int, int, str], dict[str, Any]] = {}


def _ny_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = df.index
    return idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(NY)


def _day_features(df: pd.DataFrame) -> dict[str, Any]:
    """Per New York date: daily-range percentile, gap in daily-ATRs and the daily-trend sign, all from PRIOR days only
    (nothing a trader could not have known at that morning's open)."""
    key = (id(df), len(df), str(df.index[-1]) if len(df) else "")
    hit = _feat_cache.get(key)
    if hit is not None:
        return hit
    idx = _ny_index(df)
    d = pd.DataFrame({"o": df["open"].to_numpy(), "h": df["high"].to_numpy(), "l": df["low"].to_numpy(), "c": df["close"].to_numpy()}, index=idx)
    day = d.groupby(d.index.date).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"))
    rng = day["h"] - day["l"]
    atr14 = rng.rolling(14, min_periods=5).mean().shift(1)                 # prior days only
    pct = rng.shift(1).rolling(120, min_periods=20).apply(lambda w: 100.0 * (w[:-1] < w[-1]).mean() if len(w) > 1 else 50.0, raw=True)
    sma20 = day["c"].rolling(20, min_periods=10).mean().shift(1)
    prev_close = day["c"].shift(1)
    gap = ((day["o"] - prev_close).abs() / atr14).replace([np.inf, -np.inf], np.nan)
    trend = np.sign(prev_close - sma20)                                    # +1 above its 20-day average, -1 below
    feats = {"pct": pct.to_dict(), "gap": gap.to_dict(), "trend": trend.to_dict()}
    if len(_feat_cache) > 12:
        _feat_cache.clear()
    _feat_cache[key] = feats
    return feats


def _passes(t: Trade, feats: dict[str, Any], p: dict[str, Any]) -> bool:
    et = t.entry_time.tz_convert(NY) if t.entry_time.tzinfo is not None else t.entry_time.tz_localize("UTC").tz_convert(NY)
    mins = et.hour * 60 + et.minute - OPEN_MINUTE
    if mins < p["f_start_min"] or mins > p["f_end_min"]:
        return False
    if p["f_skip_dow"] and et.weekday() + 1 == p["f_skip_dow"]:
        return False
    day = et.date()
    if p["f_atr_lo"] > 0 or p["f_atr_hi"] < 100:
        pc = feats["pct"].get(day)
        if pc is not None and not np.isnan(pc) and not (p["f_atr_lo"] <= pc <= p["f_atr_hi"]):
            return False
    if p["f_gap_max"] < 10.0:
        g = feats["gap"].get(day)
        if g is not None and not np.isnan(g) and g > p["f_gap_max"]:
            return False
    if p["f_trend"]:
        tr = feats["trend"].get(day)
        if tr is not None and not np.isnan(tr) and tr != 0:
            with_trend = (t.side == "long") == (tr > 0)
            if (p["f_trend"] == 1) != with_trend:
                return False
    return True


class FilteredStrategy(Strategy):
    def __init__(self, base: Strategy):
        self.base = base
        super().__init__(
            name=f"{base.name}_f",
            description=f"{base.name} with searchable entry filters (time window, weekday, volatility band, daily trend, gap).",
            params=[ParamSpec(f"b_{p.name}", p.default, p.min, p.max, p.step, p.kind) for p in base.params] + FILTER_SPECS,
        )

    def _split(self, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        base = {p.name: params.get(f"b_{p.name}", p.default) for p in self.base.params}
        flt = {k: params.get(k, v) for k, v in _OFF.items()}
        return base, flt

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        base_params, flt = self._split(params)
        trades = self.base.generate_trades(df, base_params)
        if not trades or all(flt[k] == v for k, v in _OFF.items()):
            return trades
        feats = _day_features(df)
        return [t for t in trades if _passes(t, feats, flt)]


class FlippedStrategy(Strategy):
    """The same strategy traded the other way round: every long becomes a short and the reverse, at the same entry price, with the
    stop and the target swapping places, so each flipped trade exits at the very price the original exited at and its P&L is
    the original's with the sign reversed (before costs: -15 becomes +15). Lets the trainer test "this strategy is reliably
    wrong, so do the opposite" as a normal strategy, through the same search, long-history gate and holdout. Costs and slippage
    still hurt both directions, so a flip only works when the original loses by more than the costs."""

    def __init__(self, base: Strategy):
        self.base = base
        super().__init__(
            name=f"{base.name}_flip",
            description=f"{base.name} traded in reverse (longs become shorts, stops and targets swap).",
            params=list(base.params),
        )

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        out: list[Trade] = []
        for t in self.base.generate_trades(df, params):
            reason = {"stop": "target", "target": "stop"}.get(t.exit_reason, t.exit_reason)
            out.append(Trade(
                entry_time=t.entry_time, exit_time=t.exit_time, side="short" if t.side == "long" else "long",
                entry_price=t.entry_price, exit_price=t.exit_price, stop_price=t.target_price, target_price=t.stop_price,
                exit_reason=reason, mfe_price=t.mae_price, mae_price=t.mfe_price,
            ))
        return out


class CombinedStrategy(Strategy):
    """Several strategies traded as ONE strategy on one account: their trades are merged in time order, one position at a time (a
    trade that would start while another is still open is skipped, exactly as a single account would experience it). Parameters are the
    members' own, prefixed a_, b_, c_ in member order. Lets one real account take, say, the opening-range retest AND the NY-open
    breakout, so it trades more often than either alone. More trades only help if every member has an edge of its own."""

    def __init__(self, name: str, members: list[Strategy]):
        self.members = members
        super().__init__(
            name=name,
            description="Combined: " + " + ".join(m.name for m in members) + " (merged in time order, one position at a time).",
            params=[ParamSpec(f"{chr(97 + i)}_{p.name}", p.default, p.min, p.max, p.step, p.kind) for i, m in enumerate(members) for p in m.params],
        )

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        allt: list[Trade] = []
        for i, m in enumerate(self.members):
            pre = f"{chr(97 + i)}_"
            mp = {p.name: params.get(pre + p.name, p.default) for p in m.params}
            allt += m.generate_trades(df, mp)
        allt.sort(key=lambda t: (t.entry_time, t.exit_time))
        out: list[Trade] = []
        for t in allt:
            if out and t.entry_time < out[-1].exit_time:
                continue
            out.append(t)
        return out


class AllDayStrategy(Strategy):
    """A signal strategy traded around the whole futures day, flat before the Apex/Israel day end.
    The user's rule: trade from 00:01 to 23:59 Israel time and close everything at 00:00 Israel (= 17:00 New York). The market itself pauses
    17:00-18:00 New York, so entries run from 18:00 ET to 16:30 ET the next day, and a trade still open at 16:55 ET is closed at that bar's close.
    The base strategy's own stop/target exits are untouched: a trade that was still open at the cutoff had hit neither, so closing it there is exact."""

    ENTRY_LAST = dt.time(16, 30)
    FLAT_AT = dt.time(16, 55)
    REOPEN = dt.time(18, 0)

    def __init__(self, base: Strategy):
        self.base = base
        super().__init__(name=f"{base.name}_day", description=f"{base.name} traded all day, flat at 17:00 New York (00:00 Israel).", params=list(base.params))

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        out: list[Trade] = []
        idx = df.index
        for t in self.base.generate_trades(df, params):
            et = t.entry_time
            tod = et.time()
            if self.ENTRY_LAST < tod < self.REOPEN:
                continue                                  # no new trades in the last half hour or in the closed hour
            # the cutoff bar: 16:55 ET of the entry's session (same day before 18:00, next day after)
            day = et.normalize() if tod < self.REOPEN else (et.normalize() + pd.Timedelta(days=1))
            cutoff = day + pd.Timedelta(hours=16, minutes=55)
            if t.exit_time <= cutoff:
                out.append(t)
                continue
            pos = idx.searchsorted(cutoff, side="right") - 1      # last bar at or before the cutoff
            if pos < 0 or idx[pos] <= et:
                continue
            out.append(Trade(entry_time=t.entry_time, exit_time=idx[pos], side=t.side, entry_price=t.entry_price, exit_price=float(df["close"].iloc[pos]),
                             stop_price=t.stop_price, target_price=t.target_price, exit_reason="session_close"))
        return out
