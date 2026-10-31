"""A self-computed Fear & Greed gauge (0-100) for a futures symbol.

There's no reliable free, no-signup "Fear & Greed Index" API for equity index
futures (the well-known CNN index covers cash equities and isn't officially
published as an API). Rather than depend on a fragile unofficial scrape, this
blends three signals we already have from our own price data into one score:

- RSI(14) — already a 0-100 oscillator shaped like fear/greed.
- Momentum vs. 20-day SMA — how far price has run above/below its recent
  average, scaled into 0-100.
- Inverse volatility percentile — ATR(14) relative to its own 60-day range;
  elevated volatility usually accompanies fear, so this is inverted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.data import yfinance_source
from app.strategies import indicators as ind

LABELS = [
    (25, "EXTREME FEAR"),
    (45, "FEAR"),
    (56, "NEUTRAL"),
    (76, "GREED"),
    (101, "EXTREME GREED"),
]


def _classify(value: float) -> str:
    for threshold, label in LABELS:
        if value < threshold:
            return label
    return "EXTREME GREED"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def compute(symbol: str = "NQ") -> dict:
    df = yfinance_source.fetch(symbol, "1d")
    close = df["close"]

    rsi_score = float(ind.rsi(close, 14).iloc[-1])
    if np.isnan(rsi_score):
        rsi_score = 50.0

    sma20 = ind.sma(close, 20)
    pct_from_sma = float((close.iloc[-1] - sma20.iloc[-1]) / sma20.iloc[-1] * 100) if not np.isnan(sma20.iloc[-1]) else 0.0
    momentum_score = _clamp(50 + pct_from_sma * 8)

    atr = ind.atr(df, 14)
    recent_atr = atr.tail(60).dropna()
    if len(recent_atr) >= 5:
        current_atr = float(atr.iloc[-1])
        percentile = float((recent_atr < current_atr).mean() * 100)
        volatility_score = _clamp(100 - percentile)
    else:
        volatility_score = 50.0

    composite = round((rsi_score + momentum_score + volatility_score) / 3)
    composite = int(_clamp(composite))

    return {
        "symbol": symbol.upper(),
        "value": composite,
        "label": _classify(composite),
        "components": {
            "rsi": round(rsi_score, 1),
            "momentum": round(momentum_score, 1),
            "volatility": round(volatility_score, 1),
        },
        "as_of": df.index[-1].isoformat(),
    }
