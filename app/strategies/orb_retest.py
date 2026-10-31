"""ORB Retest Breakout: an opening-range breakout that waits for a retest.

Same opening range as ny_open_breakout, but instead of entering the instant
price breaks it, this waits for price to break out AND THEN pull back to
retest the broken level before entering — filters out the whipsaw breakouts
that immediately reverse (ny_open_breakout's biggest failure mode), at the
cost of sometimes missing a clean move that never comes back to retest.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from app.strategies.engine_utils import day_ok
from app.strategies.base import ParamSpec, Strategy, Trade

SESSION_OPEN = dt.time(9, 30)


class OrbRetest(Strategy):
    def __init__(self):
        super().__init__(
            name="orb_retest",
            description="Breaks the first N-minute NY-open range, then waits for a retest "
            "of the broken level before entering — fewer, higher-quality breakout entries.",
            params=[
                ParamSpec("opening_range_minutes", default=15, min=5, max=60, step=5, kind="int"),
                ParamSpec("breakout_buffer_pct", default=0.05, min=0.0, max=0.5, step=0.05),
                ParamSpec("retest_tolerance_pct", default=15.0, min=0.0, max=50.0, step=5.0),
                ParamSpec("max_retest_wait_bars", default=12, min=3, max=40, step=1, kind="int"),
                ParamSpec("sl_mult", default=1.0, min=0.5, max=3.0, step=0.25),
                ParamSpec("tp_mult", default=2.0, min=0.5, max=5.0, step=0.25),
                ParamSpec("session_end_hour", default=16, min=10, max=16, step=1, kind="int"),
            ],
        )

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        orm = int(params["opening_range_minutes"])
        buffer_pct = float(params["breakout_buffer_pct"]) / 100.0
        retest_tol_pct = float(params["retest_tolerance_pct"]) / 100.0
        max_wait = int(params["max_retest_wait_bars"])
        sl_mult = float(params["sl_mult"])
        tp_mult = float(params["tp_mult"])
        session_end_hour = int(params["session_end_hour"])

        trades: list[Trade] = []
        range_end = (dt.datetime.combine(dt.date.today(), SESSION_OPEN) + dt.timedelta(minutes=orm)).time()

        for _, day_df in df.groupby(df.index.date):
            day_df = day_df.between_time(SESSION_OPEN, dt.time(session_end_hour, 0))
            if day_df.empty:
                continue
            if not day_ok(day_df):
                continue

            opening = day_df.between_time(SESSION_OPEN, range_end, inclusive="left")
            if opening.empty:
                continue

            range_high = float(opening["high"].max())
            range_low = float(opening["low"].min())
            range_size = range_high - range_low
            if range_size <= 0:
                continue

            after = day_df[day_df.index > opening.index[-1]]
            if after.empty:
                continue

            trade = self._find_retest_entry(
                after, range_high, range_low, range_size, buffer_pct, retest_tol_pct, max_wait, sl_mult, tp_mult
            )
            if trade:
                trades.append(trade)

        return trades

    @staticmethod
    def _find_retest_entry(
        after: pd.DataFrame,
        range_high: float,
        range_low: float,
        range_size: float,
        buffer_pct: float,
        retest_tol_pct: float,
        max_wait: int,
        sl_mult: float,
        tp_mult: float,
    ) -> Trade | None:
        long_trigger = range_high * (1 + buffer_pct)
        short_trigger = range_low * (1 - buffer_pct)
        tolerance = range_size * retest_tol_pct

        side = None
        breakout_pos = None
        for i, (_, bar) in enumerate(after.iterrows()):
            if bar["high"] >= long_trigger:
                side, breakout_pos = "long", i
                break
            if bar["low"] <= short_trigger:
                side, breakout_pos = "short", i
                break

        if side is None:
            return None

        level = range_high if side == "long" else range_low
        watch = after.iloc[breakout_pos + 1 : breakout_pos + 1 + max_wait]

        entry_time = entry_price = None
        # The retest order rests at the EDGE of the tolerance zone (level +/- tolerance) and is filled there when price pulls back to it (at the bar open if the bar
        # opens beyond it). It used to be credited at the range level itself even when price only came back to within the tolerance, i.e. a price that was never
        # traded: entries up to `tolerance` better than anything the market offered, which inflated every result (worst for large retest_tolerance_pct).
        for ts, bar in watch.iterrows():
            if side == "long" and bar["low"] <= level + tolerance:
                edge = level + tolerance
                entry_time, entry_price = ts, (edge if float(bar["open"]) > edge else float(bar["open"]))
                break
            if side == "short" and bar["high"] >= level - tolerance:
                edge = level - tolerance
                entry_time, entry_price = ts, (edge if float(bar["open"]) < edge else float(bar["open"]))
                break

        if entry_time is None:
            return None  # broke out but never retested in time -> no trade this day

        if side == "long":
            stop_price = entry_price - sl_mult * range_size
            target_price = entry_price + tp_mult * range_size
        else:
            stop_price = entry_price + sl_mult * range_size
            target_price = entry_price - tp_mult * range_size

        remaining = after[after.index > entry_time]
        exit_time, exit_price, exit_reason = None, None, "session_close"

        # The retest bar itself can also run through the stop after the entry fills (the fill and the stop are inside the same 5-minute bar). The pessimistic
        # choice is the stop; it used to be ignored, crediting a trade that was already stopped out.
        eb = after.loc[entry_time]
        if (float(eb["low"]) <= stop_price) if side == "long" else (float(eb["high"]) >= stop_price):
            return Trade(entry_time=entry_time, exit_time=entry_time, side=side, entry_price=entry_price, exit_price=stop_price, stop_price=stop_price,
                         target_price=target_price, exit_reason="stop")
        for ts, bar in remaining.iterrows():
            hit_stop = bar["low"] <= stop_price if side == "long" else bar["high"] >= stop_price
            hit_target = bar["high"] >= target_price if side == "long" else bar["low"] <= target_price
            if hit_stop:
                # gap through the stop fills at the bar open (stop = market order); same-bar stop+target -> stop first
                exit_time, exit_price, exit_reason = ts, (min(stop_price, float(bar["open"])) if side == "long" else max(stop_price, float(bar["open"]))), "stop"
                break
            if hit_target:
                exit_time, exit_price, exit_reason = ts, target_price, "target"
                break

        if exit_time is None:
            exit_time, exit_price, exit_reason = after.index[-1], float(after.iloc[-1]["close"]), "session_close"

        return Trade(
            entry_time=entry_time,
            exit_time=exit_time,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_price=stop_price,
            target_price=target_price,
            exit_reason=exit_reason,
        )
