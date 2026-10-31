"""Shared trade simulator for signal-based strategies (EMA cross, RSI, MACD, etc).

A strategy just needs to produce an `entries` series (+1 long / -1 short / 0 none)
and an ATR series for sizing; this walks forward from each signal, enters at the
NEXT bar's open (no lookahead), and manages stop/target/timeout — one position at
a time. The session-based strategies (ny_open_breakout, orb_fade) don't use this;
they manage a single intraday trade directly since their logic is inherently
per-day rather than per-bar.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import Trade


def simulate_trades(
    df: pd.DataFrame,
    entries: pd.Series,
    atr: pd.Series,
    sl_mult: float,
    tp_mult: float,
    max_hold_bars: int,
) -> list[Trade]:
    trades: list[Trade] = []
    n = len(df)
    idx = df.index
    highs, lows, opens, closes = df["high"].values, df["low"].values, df["open"].values, df["close"].values
    entry_vals = entries.values
    atr_vals = atr.values

    signal_positions = [i for i in range(n - 1) if entry_vals[i] != 0]
    blocked_until = -1

    for sig_i in signal_positions:
        if sig_i <= blocked_until:
            continue
        entry_i = sig_i + 1
        a = atr_vals[sig_i]
        if a is None or a != a or a <= 0:  # NaN-safe
            continue

        side = "long" if entry_vals[sig_i] > 0 else "short"
        entry_price = float(opens[entry_i])
        stop_dist = a * sl_mult
        target_dist = a * tp_mult
        if side == "long":
            stop_price = entry_price - stop_dist
            target_price = entry_price + target_dist
        else:
            stop_price = entry_price + stop_dist
            target_price = entry_price - target_dist

        max_i = min(entry_i + max_hold_bars, n - 1)
        exit_i, exit_price, exit_reason = None, None, "timeout"
        hi, lo = -float("inf"), float("inf")
        for j in range(entry_i, max_i + 1):
            # No position is carried across a session break (a new calendar day, or a data gap of an hour or more): the real accounts are flat by 16:59 ET, and a
            # backtest that holds through the break would exit at next day's open - a gap the live account can never trade. Close at the last bar before the break.
            if j > entry_i and (idx[j].date() != idx[j - 1].date() or (idx[j] - idx[j - 1]) >= pd.Timedelta(hours=1)):
                exit_i, exit_price, exit_reason = j - 1, float(closes[j - 1]), "session_close"
                break
            hi, lo = max(hi, highs[j]), min(lo, lows[j])
            hit_stop = lows[j] <= stop_price if side == "long" else highs[j] >= stop_price
            hit_target = highs[j] >= target_price if side == "long" else lows[j] <= target_price
            # stop and target inside the SAME bar: bar data cannot tell which printed first, so assume the stop
            # (the pessimistic choice - never let the bar-path guess flatter the backtest)
            if hit_stop:
                # a bar that OPENS beyond the stop (gap) fills at that open, not at the stop - a stop is a market order
                gap_fill = min(stop_price, float(opens[j])) if side == "long" else max(stop_price, float(opens[j]))
                exit_i, exit_price, exit_reason = j, gap_fill, "stop"
                break
            if hit_target:
                exit_i, exit_price, exit_reason = j, target_price, "target"
                break

        if exit_i is None:
            exit_i = max_i
            exit_price = float(closes[exit_i])
            exit_reason = "timeout"

        trades.append(
            Trade(
                entry_time=idx[entry_i],
                exit_time=idx[exit_i],
                side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                stop_price=stop_price,
                target_price=target_price,
                exit_reason=exit_reason,
                mfe_price=float(hi if side == "long" else lo),
                mae_price=float(lo if side == "long" else hi),
            )
        )
        blocked_until = exit_i

    return trades


def day_ok(day_df) -> bool:
    """A usable session day: it has the 9:30 opening bar and no data hole of more than 15 minutes (a missing opening bar shifts the opening range; a hole fakes gap fills)."""
    if day_df.empty or day_df.index[0].hour != 9 or day_df.index[0].minute != 30:
        return False
    return not (day_df.index.to_series().diff().dropna() > pd.Timedelta(minutes=15)).any()
