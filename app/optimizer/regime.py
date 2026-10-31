"""Classifies each trading day as 'trending' or 'choppy' using a simple efficiency
ratio (net directional move / total range covered that day) — a cheap, well-known
proxy related to Kaufman's Efficiency Ratio. Used only as an extra robustness check
on top of the existing walk-forward folds (see search.py's regime_inconsistent
gate): a strategy that only actually works in one regime type isn't a robust edge,
even if its blended aggregate numbers look fine.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

TRENDING_EFFICIENCY_THRESHOLD = 0.35


def classify_days(df: pd.DataFrame) -> dict[dt.date, str]:
    """Returns {date: 'trending' | 'choppy'} for every day present in df."""
    labels: dict[dt.date, str] = {}
    for day, day_df in df.groupby(df.index.date):
        day_range = float(day_df["high"].max() - day_df["low"].min())
        if day_range <= 0:
            continue
        net_move = abs(float(day_df["close"].iloc[-1]) - float(day_df["open"].iloc[0]))
        efficiency = net_move / day_range
        labels[day] = "trending" if efficiency >= TRENDING_EFFICIENCY_THRESHOLD else "choppy"
    return labels
