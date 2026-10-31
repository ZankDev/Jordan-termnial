"""ICT Order Block strategy.

An order block is the last opposite-colored candle before a strong impulsive
move — read as the footprint of the orders that fueled that move. A bullish
order block is the last down-close candle before a strong up-thrust; a bearish
order block is the last up-close candle before a strong down-thrust. When price
later retraces back into that candle's range, it's read as a high-probability
zone for the original move to resume, so that's the entry.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.strategies import indicators as ind
from app.strategies.base import ParamSpec, Strategy, Trade
from app.strategies.engine_utils import simulate_trades

_PARAMS = [
    ParamSpec("impulse_atr_mult", default=2.0, min=1.0, max=4.0, step=0.25),
    ParamSpec("max_age_bars", default=60, min=10, max=200, step=10, kind="int"),
    ParamSpec("atr_period", default=14, min=7, max=30, step=1, kind="int"),
    ParamSpec("sl_mult", default=1.25, min=0.5, max=4.0, step=0.25),
    ParamSpec("tp_mult", default=2.0, min=0.5, max=6.0, step=0.25),
    ParamSpec("max_hold_bars", default=48, min=6, max=150, step=6, kind="int"),
]


def _order_block_entries(df: pd.DataFrame, atr: pd.Series, impulse_atr_mult: float, max_age_bars: int) -> pd.Series:
    n = len(df)
    opens, closes = df["open"].values, df["close"].values
    highs, lows = df["high"].values, df["low"].values
    atr_vals = atr.values
    entries = np.zeros(n)

    active_bull: list[tuple[int, float, float]] = []  # (formed_idx, block_low, block_high)
    active_bear: list[tuple[int, float, float]] = []

    for i in range(1, n):
        # 1. React to already-active blocks (a block formed at bar i is appended
        # below, only reacted to from bar i+1 on — never against its own formation bar).
        still_bull = []
        for formed_idx, b_low, b_high in active_bull:
            if i - formed_idx > max_age_bars:
                continue
            if lows[i] <= b_high and highs[i] >= b_low:
                entries[i] = 1
            else:
                still_bull.append((formed_idx, b_low, b_high))
        active_bull = still_bull

        still_bear = []
        for formed_idx, b_low, b_high in active_bear:
            if i - formed_idx > max_age_bars:
                continue
            if lows[i] <= b_high and highs[i] >= b_low:
                entries[i] = -1
            else:
                still_bear.append((formed_idx, b_low, b_high))
        active_bear = still_bear

        # 2. Detect a new block: bar i is a strong impulse, bar i-1 is the opposite color
        a = atr_vals[i]
        if a != a or a <= 0:  # NaN-safe
            continue
        impulse = closes[i] - opens[i]
        prior_down = closes[i - 1] < opens[i - 1]
        prior_up = closes[i - 1] > opens[i - 1]
        if impulse >= impulse_atr_mult * a and prior_down:
            active_bull.append((i, lows[i - 1], highs[i - 1]))
        elif -impulse >= impulse_atr_mult * a and prior_up:
            active_bear.append((i, lows[i - 1], highs[i - 1]))

    return pd.Series(entries, index=df.index)


class OrderBlock(Strategy):
    def __init__(self):
        super().__init__(
            name="order_block",
            description="ICT order block: trades a retrace into the last opposite-colored "
            "candle before a strong impulsive move, expecting the move to resume.",
            params=list(_PARAMS),
        )

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        atr = ind.atr(df, int(params["atr_period"]))
        entries = _order_block_entries(df, atr, float(params["impulse_atr_mult"]), int(params["max_age_bars"]))
        return simulate_trades(
            df, entries, atr,
            sl_mult=float(params["sl_mult"]),
            tp_mult=float(params["tp_mult"]),
            max_hold_bars=int(params["max_hold_bars"]),
        )
