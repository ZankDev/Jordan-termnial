"""Eight indicator-driven strategies sharing the generic bar-by-bar trade engine
(app.strategies.engine_utils.simulate_trades). Each just needs to build an
`entries` series (+1 long / -1 short / 0 none) and hand it, plus an ATR series
for stop/target sizing, to the shared simulator.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.strategies import indicators as ind
from app.strategies.base import ParamSpec, Strategy, Trade
from app.strategies.engine_utils import simulate_trades

# params shared by every signal strategy for ATR-based risk sizing
_RISK_PARAMS = [
    ParamSpec("atr_period", default=14, min=7, max=30, step=1, kind="int"),
    ParamSpec("sl_mult", default=1.5, min=0.5, max=4.0, step=0.25),
    ParamSpec("tp_mult", default=2.5, min=0.5, max=6.0, step=0.25),
    ParamSpec("max_hold_bars", default=48, min=6, max=150, step=6, kind="int"),
]


def _run(df: pd.DataFrame, params: dict[str, Any], entries: pd.Series) -> list[Trade]:
    atr = ind.atr(df, int(params["atr_period"]))
    return simulate_trades(
        df, entries, atr,
        sl_mult=float(params["sl_mult"]),
        tp_mult=float(params["tp_mult"]),
        max_hold_bars=int(params["max_hold_bars"]),
    )


class VwapReversion(Strategy):
    def __init__(self):
        super().__init__(
            name="vwap_reversion",
            description="Fades large intraday deviations from session VWAP back toward it.",
            params=[ParamSpec("dev_mult", default=1.5, min=0.5, max=4.0, step=0.25), *_RISK_PARAMS],
        )

    def generate_trades(self, df, params):
        vwap = ind.session_vwap(df)
        atr = ind.atr(df, int(params["atr_period"]))
        dev = (df["close"] - vwap) / atr.replace(0, np.nan)
        dev_mult = float(params["dev_mult"])
        entries = pd.Series(0, index=df.index)
        entries[dev <= -dev_mult] = 1   # far below VWAP -> long, expect reversion up
        entries[dev >= dev_mult] = -1   # far above VWAP -> short, expect reversion down
        return _run(df, params, entries)


class EmaCrossover(Strategy):
    def __init__(self):
        super().__init__(
            name="ema_crossover",
            description="Trend-following: fast EMA crossing slow EMA.",
            params=[
                ParamSpec("fast_period", default=9, min=5, max=25, step=1, kind="int"),
                ParamSpec("slow_period", default=21, min=15, max=60, step=5, kind="int"),
                *_RISK_PARAMS,
            ],
        )

    def generate_trades(self, df, params):
        fast = ind.ema(df["close"], int(params["fast_period"]))
        slow = ind.ema(df["close"], int(params["slow_period"]))
        diff_sign = np.sign(fast - slow)
        cross = diff_sign.diff()
        entries = pd.Series(0, index=df.index)
        entries[cross > 0] = 1
        entries[cross < 0] = -1
        return _run(df, params, entries)


class DonchianBreakout(Strategy):
    def __init__(self):
        super().__init__(
            name="donchian_breakout",
            description="Trend breakout of an N-bar Donchian channel.",
            params=[ParamSpec("channel_period", default=20, min=10, max=60, step=5, kind="int"), *_RISK_PARAMS],
        )

    def generate_trades(self, df, params):
        upper, lower = ind.donchian(df, int(params["channel_period"]))
        prev_upper, prev_lower = upper.shift(1), lower.shift(1)
        entries = pd.Series(0, index=df.index)
        entries[df["close"] > prev_upper] = 1
        entries[df["close"] < prev_lower] = -1
        return _run(df, params, entries)


class RsiReversal(Strategy):
    def __init__(self):
        super().__init__(
            name="rsi_reversal",
            description="Mean reversion when RSI is oversold/overbought.",
            params=[
                ParamSpec("rsi_period", default=14, min=7, max=28, step=1, kind="int"),
                ParamSpec("oversold", default=30, min=10, max=40, step=5, kind="int"),
                ParamSpec("overbought", default=70, min=60, max=90, step=5, kind="int"),
                *_RISK_PARAMS,
            ],
        )

    def generate_trades(self, df, params):
        r = ind.rsi(df["close"], int(params["rsi_period"]))
        entries = pd.Series(0, index=df.index)
        entries[r <= float(params["oversold"])] = 1
        entries[r >= float(params["overbought"])] = -1
        return _run(df, params, entries)


class BollingerBreakout(Strategy):
    def __init__(self):
        super().__init__(
            name="bollinger_breakout",
            description="Momentum continuation when price closes outside its Bollinger Band.",
            params=[
                ParamSpec("bb_period", default=20, min=10, max=40, step=2, kind="int"),
                ParamSpec("bb_std", default=2.0, min=1.0, max=3.5, step=0.25),
                *_RISK_PARAMS,
            ],
        )

    def generate_trades(self, df, params):
        _, upper, lower = ind.bollinger(df["close"], int(params["bb_period"]), float(params["bb_std"]))
        entries = pd.Series(0, index=df.index)
        entries[df["close"] > upper] = 1
        entries[df["close"] < lower] = -1
        return _run(df, params, entries)


class PrevDayRangeBreakout(Strategy):
    def __init__(self):
        super().__init__(
            name="prev_day_range_breakout",
            description="Breakout of the previous session's high/low.",
            params=[ParamSpec("buffer_pct", default=0.05, min=0.0, max=0.5, step=0.05), *_RISK_PARAMS],
        )

    def generate_trades(self, df, params):
        prev_high, prev_low = ind.prev_day_high_low(df)
        buffer = float(params["buffer_pct"]) / 100.0
        entries = pd.Series(0, index=df.index)
        entries[df["close"] > prev_high * (1 + buffer)] = 1
        entries[df["close"] < prev_low * (1 - buffer)] = -1
        return _run(df, params, entries)


class MacdCross(Strategy):
    def __init__(self):
        super().__init__(
            name="macd_cross",
            description="Trend-following: MACD line crossing its signal line.",
            params=[
                ParamSpec("fast", default=12, min=5, max=20, step=1, kind="int"),
                ParamSpec("slow", default=26, min=15, max=40, step=2, kind="int"),
                ParamSpec("signal", default=9, min=5, max=20, step=1, kind="int"),
                *_RISK_PARAMS,
            ],
        )

    def generate_trades(self, df, params):
        macd_line, signal_line = ind.macd(df["close"], int(params["fast"]), int(params["slow"]), int(params["signal"]))
        diff_sign = np.sign(macd_line - signal_line)
        cross = diff_sign.diff()
        entries = pd.Series(0, index=df.index)
        entries[cross > 0] = 1
        entries[cross < 0] = -1
        return _run(df, params, entries)


class LiquiditySweep(Strategy):
    def __init__(self):
        super().__init__(
            name="liquidity_sweep",
            description="ICT/SMC liquidity sweep: fades a stop-hunt wick beyond a recent "
            "swing high/low that closes back inside the prior range.",
            params=[ParamSpec("lookback_bars", default=20, min=5, max=100, step=5, kind="int"), *_RISK_PARAMS],
        )

    def generate_trades(self, df, params):
        lookback = int(params["lookback_bars"])
        swing_high = df["high"].rolling(lookback).max().shift(1)
        swing_low = df["low"].rolling(lookback).min().shift(1)
        swept_high = (df["high"] > swing_high) & (df["close"] < swing_high)
        swept_low = (df["low"] < swing_low) & (df["close"] > swing_low)
        entries = pd.Series(0, index=df.index)
        entries[swept_low] = 1    # swept liquidity below -> stop hunt -> long reversal
        entries[swept_high] = -1  # swept liquidity above -> stop hunt -> short reversal
        return _run(df, params, entries)


class InsideBarBreakout(Strategy):
    def __init__(self):
        super().__init__(
            name="inside_bar_breakout",
            description="Breakout of an inside-bar consolidation pattern.",
            params=list(_RISK_PARAMS),
        )

    def generate_trades(self, df, params):
        prev_high, prev_low = df["high"].shift(1), df["low"].shift(1)
        is_inside = (df["high"] <= prev_high) & (df["low"] >= prev_low)
        inside_high = prev_high.where(is_inside)
        inside_low = prev_low.where(is_inside)
        was_inside_prev = is_inside.shift(1).fillna(False)
        entries = pd.Series(0, index=df.index)
        entries[was_inside_prev & (df["close"] > inside_high.shift(1))] = 1
        entries[was_inside_prev & (df["close"] < inside_low.shift(1))] = -1
        return _run(df, params, entries)
