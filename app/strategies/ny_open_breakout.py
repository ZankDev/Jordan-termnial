"""NY-Open Breakout: the strategy the user described directly.

Take the first N minutes after the 9:30am NY open as an "opening range". If price
breaks above the range high (by a small buffer) go long; break below the range low,
go short. One trade per session, managed with a stop/target sized off the range,
force-closed at a session cutoff if neither is hit.

Every knob below (range length, breakout buffer, stop/target sizing, session cutoff)
is exactly what the optimizer searches over to "train" this strategy.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from app.strategies.engine_utils import day_ok
from app.strategies.base import ParamSpec, Strategy, Trade

SESSION_OPEN = dt.time(9, 30)


class NyOpenBreakout(Strategy):
    def __init__(self):
        super().__init__(
            name="ny_open_breakout",
            description="Breakout of the first N-minute range after the 9:30am NY open.",
            params=[
                ParamSpec("opening_range_minutes", default=15, min=5, max=60, step=5, kind="int"),
                ParamSpec("breakout_buffer_pct", default=0.05, min=0.0, max=0.5, step=0.05),
                ParamSpec("sl_mult", default=1.0, min=0.5, max=3.0, step=0.25),
                ParamSpec("tp_mult", default=2.0, min=0.5, max=5.0, step=0.25),
                ParamSpec("session_end_hour", default=16, min=10, max=16, step=1, kind="int"),
            ],
        )

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        orm = int(params["opening_range_minutes"])
        buffer_pct = float(params["breakout_buffer_pct"]) / 100.0
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

            long_trigger = range_high * (1 + buffer_pct)
            short_trigger = range_low * (1 - buffer_pct)

            after = day_df[day_df.index > opening.index[-1]]
            if after.empty:
                continue

            trade = self._find_entry_and_manage(
                after, long_trigger, short_trigger, range_size, sl_mult, tp_mult
            )
            if trade:
                trades.append(trade)

        return trades

    @staticmethod
    def _find_entry_and_manage(
        after: pd.DataFrame,
        long_trigger: float,
        short_trigger: float,
        range_size: float,
        sl_mult: float,
        tp_mult: float,
    ) -> Trade | None:
        side = None
        entry_time = entry_price = None
        entry_idx = None

        for i, (ts, bar) in enumerate(after.iterrows()):
            if bar["high"] >= long_trigger:
                side, entry_time, entry_price, entry_idx = "long", ts, long_trigger, i
                break
            if bar["low"] <= short_trigger:
                side, entry_time, entry_price, entry_idx = "short", ts, short_trigger, i
                break

        if side is None:
            return None

        if side == "long":
            stop_price = entry_price - sl_mult * range_size
            target_price = entry_price + tp_mult * range_size
        else:
            stop_price = entry_price + sl_mult * range_size
            target_price = entry_price - tp_mult * range_size

        remaining = after.iloc[entry_idx + 1 :]
        exit_time, exit_price, exit_reason = None, None, "session_close"

        # The entry bar itself can also reach the stop (price breaks the trigger, then reverses through the stop inside the same 5-minute bar). Bar data cannot say
        # which came first, so the pessimistic choice is the stop - it used to be ignored, crediting a trade that was already stopped out.
        eb = after.iloc[entry_idx]
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
            last = after.iloc[-1]
            exit_time, exit_price, exit_reason = after.index[-1], float(last["close"]), "session_close"

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
