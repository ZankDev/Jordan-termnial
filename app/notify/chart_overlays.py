"""Maps each strategy's own entry logic to a chart overlay, reusing the exact same
indicator math the strategy used to decide to enter (app/strategies/indicators.py,
or the opening-range/inside-bar math from the strategy files themselves) — so the
Telegram chart shows the real reason a trade fired, not a generic decoration.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.strategies import indicators as ind

SESSION_OPEN = dt.time(9, 30)

VWAP_COLOR = "#3fd1ff"
FAST_COLOR = "#f5a623"
SLOW_COLOR = "#8e6bff"
BAND_COLOR = "#8993a1"

_EMPTY: dict = {"lines": [], "hlines": [], "panel": None}


def _opening_range(df: pd.DataFrame, entry_time: pd.Timestamp, minutes: int) -> tuple[float, float] | None:
    day = entry_time.date()
    day_df = df[df.index.date == day]
    range_end = (dt.datetime.combine(dt.date.today(), SESSION_OPEN) + dt.timedelta(minutes=minutes)).time()
    opening = day_df.between_time(SESSION_OPEN, range_end, inclusive="left")
    if opening.empty:
        return None
    return float(opening["high"].max()), float(opening["low"].min())


def compute_overlay(strategy_name: str, df: pd.DataFrame, params: dict, entry_time: pd.Timestamp) -> dict:
    close = df["close"]

    if strategy_name == "vwap_reversion":
        return {"lines": [("VWAP", ind.session_vwap(df), VWAP_COLOR)], "hlines": [], "panel": None}

    if strategy_name == "ema_crossover":
        fast_p, slow_p = int(params["fast_period"]), int(params["slow_period"])
        return {
            "lines": [
                (f"EMA {fast_p}", ind.ema(close, fast_p), FAST_COLOR),
                (f"EMA {slow_p}", ind.ema(close, slow_p), SLOW_COLOR),
            ],
            "hlines": [], "panel": None,
        }

    if strategy_name == "donchian_breakout":
        period = int(params["channel_period"])
        upper, lower = ind.donchian(df, period)
        return {
            "lines": [(f"Donchian {period} hi", upper, FAST_COLOR), (f"Donchian {period} lo", lower, SLOW_COLOR)],
            "hlines": [], "panel": None,
        }

    if strategy_name == "bollinger_breakout":
        mid, upper, lower = ind.bollinger(close, int(params["bb_period"]), float(params["bb_std"]))
        return {
            "lines": [("BB mid", mid, BAND_COLOR), ("BB upper", upper, FAST_COLOR), ("BB lower", lower, SLOW_COLOR)],
            "hlines": [], "panel": None,
        }

    if strategy_name == "prev_day_range_breakout":
        prev_high, prev_low = ind.prev_day_high_low(df)
        return {
            "lines": [("Prev day high", prev_high, FAST_COLOR), ("Prev day low", prev_low, SLOW_COLOR)],
            "hlines": [], "panel": None,
        }

    if strategy_name in ("ny_open_breakout", "orb_fade", "orb_retest"):
        rng = _opening_range(df, entry_time, int(params["opening_range_minutes"]))
        if rng is None:
            return _EMPTY
        high, low = rng
        return {
            "lines": [],
            "hlines": [("Opening range high", high, FAST_COLOR), ("Opening range low", low, SLOW_COLOR)],
            "panel": None,
        }

    if strategy_name == "inside_bar_breakout":
        pos = df.index.get_indexer([entry_time], method="pad")
        if len(pos) == 0 or pos[0] <= 0:
            return _EMPTY
        prior = df.iloc[pos[0] - 1]
        return {
            "lines": [],
            "hlines": [
                ("Inside bar high", float(prior["high"]), FAST_COLOR),
                ("Inside bar low", float(prior["low"]), SLOW_COLOR),
            ],
            "panel": None,
        }

    if strategy_name == "liquidity_sweep":
        lookback = int(params["lookback_bars"])
        swing_high = df["high"].rolling(lookback).max().shift(1)
        swing_low = df["low"].rolling(lookback).min().shift(1)
        return {
            "lines": [
                (f"{lookback}-bar swing high", swing_high, FAST_COLOR),
                (f"{lookback}-bar swing low", swing_low, SLOW_COLOR),
            ],
            "hlines": [], "panel": None,
        }

    if strategy_name == "fair_value_gap":
        pos = df.index.get_indexer([entry_time], method="pad")
        if len(pos) == 0 or pos[0] < 2:
            return _EMPTY
        i = pos[0]
        max_age = int(params["max_age_bars"])
        highs, lows = df["high"].values, df["low"].values
        zone = None
        for j in range(i, max(1, i - max_age), -1):
            if lows[j] > highs[j - 2]:
                zone = ("Bullish FVG top", "Bullish FVG bottom", float(lows[j]), float(highs[j - 2]))
                break
            if highs[j] < lows[j - 2]:
                zone = ("Bearish FVG top", "Bearish FVG bottom", float(lows[j - 2]), float(highs[j]))
                break
        if zone is None:
            return _EMPTY
        top_label, bot_label, top, bottom = zone
        return {"lines": [], "hlines": [(top_label, top, FAST_COLOR), (bot_label, bottom, SLOW_COLOR)], "panel": None}

    if strategy_name == "order_block":
        pos = df.index.get_indexer([entry_time], method="pad")
        if len(pos) == 0 or pos[0] < 1:
            return _EMPTY
        i = pos[0]
        max_age = int(params["max_age_bars"])
        opens, closes = df["open"].values, df["close"].values
        highs, lows = df["high"].values, df["low"].values
        atr_series = ind.atr(df, int(params["atr_period"]))
        impulse_mult = float(params["impulse_atr_mult"])
        zone = None
        for j in range(i, max(1, i - max_age), -1):
            a = atr_series.iloc[j]
            if a != a or a <= 0:
                continue
            impulse = closes[j] - opens[j]
            if impulse >= impulse_mult * a and closes[j - 1] < opens[j - 1]:
                zone = ("Bullish order block high", "Bullish order block low", float(highs[j - 1]), float(lows[j - 1]))
                break
            if -impulse >= impulse_mult * a and closes[j - 1] > opens[j - 1]:
                zone = ("Bearish order block high", "Bearish order block low", float(highs[j - 1]), float(lows[j - 1]))
                break
        if zone is None:
            return _EMPTY
        hi_label, lo_label, hi, lo = zone
        return {"lines": [], "hlines": [(hi_label, hi, FAST_COLOR), (lo_label, lo, SLOW_COLOR)], "panel": None}

    if strategy_name == "rsi_reversal":
        period = int(params["rsi_period"])
        return {
            "lines": [], "hlines": [],
            "panel": {
                "ylabel": "RSI",
                "lines": [(f"RSI {period}", ind.rsi(close, period), VWAP_COLOR)],
                "hlines": [
                    ("Overbought", float(params["overbought"]), SLOW_COLOR),
                    ("Oversold", float(params["oversold"]), FAST_COLOR),
                ],
            },
        }

    if strategy_name == "macd_cross":
        macd_line, signal_line = ind.macd(close, int(params["fast"]), int(params["slow"]), int(params["signal"]))
        return {
            "lines": [], "hlines": [],
            "panel": {
                "ylabel": "MACD",
                "lines": [("MACD", macd_line, FAST_COLOR), ("Signal", signal_line, SLOW_COLOR)],
                "hlines": [("Zero", 0.0, BAND_COLOR)],
            },
        }

    return _EMPTY
