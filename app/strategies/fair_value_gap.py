"""ICT Fair Value Gap (FVG) / inverse FVG (iFVG) strategy.

A Fair Value Gap is the 3-candle imbalance ICT/SMC traders watch: candle 1 and
candle 3 don't overlap, leaving a gap candle 2's impulsive move punched through.
The zone between candle 1's high and candle 3's low (bullish FVG) or candle 1's
low and candle 3's high (bearish FVG) is read as an area price is likely to
return to and react from.

Two ways it plays out, both handled here:
- Fill & hold: price retraces back into an unfilled gap and respects it as
  support/resistance -> trade the continuation of the original impulse.
- Inverse FVG (iFVG): price trades cleanly THROUGH the gap and closes beyond
  its far edge before ever getting a "hold" reaction -> the zone flips polarity
  (support becomes resistance or vice versa) -> trade the reversal instead.

Each detected gap is tracked until it reacts (fill or inversion) or ages out
past max_age_bars, whichever comes first.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.strategies import indicators as ind
from app.strategies.base import ParamSpec, Strategy, Trade
from app.strategies.engine_utils import simulate_trades

_PARAMS = [
    ParamSpec("min_gap_atr_mult", default=0.15, min=0.0, max=1.0, step=0.05),
    ParamSpec("max_age_bars", default=60, min=10, max=200, step=10, kind="int"),
    ParamSpec("atr_period", default=14, min=7, max=30, step=1, kind="int"),
    ParamSpec("sl_mult", default=1.25, min=0.5, max=4.0, step=0.25),
    ParamSpec("tp_mult", default=2.0, min=0.5, max=6.0, step=0.25),
    ParamSpec("max_hold_bars", default=48, min=6, max=150, step=6, kind="int"),
]


def _fvg_entries(df: pd.DataFrame, atr: pd.Series, min_gap_atr_mult: float, max_age_bars: int) -> pd.Series:
    n = len(df)
    highs, lows, closes = df["high"].values, df["low"].values, df["close"].values
    atr_vals = atr.values
    entries = np.zeros(n)

    active_bull: list[tuple[int, float, float]] = []  # (formed_idx, gap_low, gap_high)
    active_bear: list[tuple[int, float, float]] = []

    for i in range(2, n):
        # 1. React to already-active gaps using this bar's price action (a gap formed
        # AT bar i below is only appended after this check, so it's never tested
        # against its own formation bar).
        still_bull = []
        for formed_idx, g_low, g_high in active_bull:
            if i - formed_idx > max_age_bars:
                continue  # expired, unused
            if closes[i] < g_low:
                entries[i] = -1  # traded clean through -> inversion, bearish continuation
            elif lows[i] <= g_high and highs[i] >= g_low:
                entries[i] = 1  # retraced into the zone and held -> bullish continuation
            else:
                still_bull.append((formed_idx, g_low, g_high))
        active_bull = still_bull

        still_bear = []
        for formed_idx, g_low, g_high in active_bear:
            if i - formed_idx > max_age_bars:
                continue
            if closes[i] > g_high:
                entries[i] = 1  # traded clean through -> inversion, bullish continuation
            elif lows[i] <= g_high and highs[i] >= g_low:
                entries[i] = -1  # retraced into the zone and held -> bearish continuation
            else:
                still_bear.append((formed_idx, g_low, g_high))
        active_bear = still_bear

        # 2. Detect a new gap formed by candles i-2, i-1, i (only reacted to from bar i+1 on)
        a = atr_vals[i]
        min_size = a * min_gap_atr_mult if a == a and a > 0 else 0.0  # NaN-safe
        if lows[i] > highs[i - 2] and (lows[i] - highs[i - 2]) >= min_size:
            active_bull.append((i, highs[i - 2], lows[i]))
        if highs[i] < lows[i - 2] and (lows[i - 2] - highs[i]) >= min_size:
            active_bear.append((i, highs[i], lows[i - 2]))

    return pd.Series(entries, index=df.index)


class FairValueGap(Strategy):
    def __init__(self):
        super().__init__(
            name="fair_value_gap",
            description="ICT Fair Value Gap: trades a retrace into an unfilled 3-candle "
            "imbalance as continuation, or its inversion (iFVG) as a reversal when price "
            "instead closes clean through it.",
            params=list(_PARAMS),
        )

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        atr = ind.atr(df, int(params["atr_period"]))
        entries = _fvg_entries(df, atr, float(params["min_gap_atr_mult"]), int(params["max_age_bars"]))
        return simulate_trades(
            df, entries, atr,
            sl_mult=float(params["sl_mult"]),
            tp_mult=float(params["tp_mult"]),
            max_hold_bars=int(params["max_hold_bars"]),
        )
