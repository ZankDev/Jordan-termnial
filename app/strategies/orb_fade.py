"""Opening Range Fade: bet AGAINST the opening-range breakout instead of with it.

Same opening range as ny_open_breakout, but when price pokes past the range and
fails to hold (touches beyond it, closes back inside), that's read as a false
breakout and faded: short after a failed high-side poke, long after a failed
low-side poke.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from app.strategies.engine_utils import day_ok
from app.strategies.base import ParamSpec, Strategy, Trade

SESSION_OPEN = dt.time(9, 30)


class OrbFade(Strategy):
    def __init__(self):
        super().__init__(
            name="orb_fade",
            description="Fades a failed breakout of the first N-minute NY-open range.",
            params=[
                ParamSpec("opening_range_minutes", default=15, min=5, max=60, step=5, kind="int"),
                ParamSpec("sl_mult", default=1.0, min=0.5, max=3.0, step=0.25),
                ParamSpec("tp_mult", default=1.5, min=0.5, max=4.0, step=0.25),
                ParamSpec("session_end_hour", default=16, min=10, max=16, step=1, kind="int"),
            ],
        )

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        orm = int(params["opening_range_minutes"])
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

            trade = self._find_fade_entry(after, range_high, range_low, range_size, sl_mult, tp_mult)
            if trade:
                trades.append(trade)

        return trades

    @staticmethod
    def _find_fade_entry(after, range_high, range_low, range_size, sl_mult, tp_mult):
        side = None
        entry_time = entry_price = None
        entry_idx = None

        for i, (ts, bar) in enumerate(after.iterrows()):
            poked_high = bar["high"] > range_high and bar["close"] < range_high
            poked_low = bar["low"] < range_low and bar["close"] > range_low
            if poked_high:
                side, entry_time, entry_price, entry_idx = "short", ts, float(bar["close"]), i
                break
            if poked_low:
                side, entry_time, entry_price, entry_idx = "long", ts, float(bar["close"]), i
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
