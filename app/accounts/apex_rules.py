"""Apex Trader Funding-style evaluation rules: intraday trailing drawdown.

Single source of truth for pass/fail logic, shared by the live engine (real,
tick-accurate, checked every poll) and the backtest-evaluation preview
(approximate, checked at each closed trade) so the two can never disagree
about what the rules actually are.

Tier numbers are exactly what the user specified; adjust here if Apex's
published numbers change.
"""
from __future__ import annotations

from typing import Any

TIERS: dict[int, dict[str, Any]] = {
    # daily_loss_limit per Apex's published EOD Evaluation parameters
    # (https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-evaluations/) -
    # a fixed intraday cap, separate from (and tighter than) the EOD drawdown floor below.
    25_000: {"profit_target": 1_500, "max_drawdown": 1_000, "max_contracts_minis": 4, "max_contracts_micros": 40, "daily_loss_limit": 500},
    50_000: {"profit_target": 3_000, "max_drawdown": 2_000, "max_contracts_minis": 6, "max_contracts_micros": 60, "daily_loss_limit": 1_000},
    100_000: {"profit_target": 6_000, "max_drawdown": 3_000, "max_contracts_minis": 8, "max_contracts_micros": 80, "daily_loss_limit": 1_500},
    150_000: {"profit_target": 9_000, "max_drawdown": 4_000, "max_contracts_minis": 12, "max_contracts_micros": 120, "daily_loss_limit": 2_000},
}

FLATTEN_HOUR_ET = 16
FLATTEN_MINUTE_ET = 59  # positions must be flat by 4:59pm ET


def tier_for_size(size: int) -> dict[str, Any]:
    if size not in TIERS:
        raise ValueError(f"Unknown Apex tier size {size}. Valid sizes: {sorted(TIERS)}")
    return TIERS[size]


def compute_floor(peak_equity: float, starting_balance: float, max_drawdown: float) -> float:
    """Trailing drawdown floor: trails the peak downward-shifted by max_drawdown,
    but locks at the starting balance once that would exceed it — Apex's real
    "trailing stops once you're sufficiently profitable" behavior. peak_equity
    only ever increases, so this floor only ever increases too."""
    raw_floor = peak_equity - max_drawdown
    return min(raw_floor, starting_balance)


def evaluate_tick(account: dict[str, Any], current_equity: float, is_eod_close: bool = True) -> dict[str, Any]:
    """Given an account's current mark-to-market equity, compute the updated
    peak/floor and whether this tick breaches or passes the evaluation.

    Apex's real EOD rule: the drawdown threshold is recalculated once per trading day, at
    the 4:59:59pm ET close, from that day's closing balance - then held fixed (never
    re-tightened, never loosened) for the whole next session, though breaches are still
    checked in real time intraday against that fixed level. `is_eod_close` lets the live
    engine pass True only once trading has stopped for the day (reusing its existing
    must_flatten window, where balance is stable since no new entries are taken past it),
    so `peak_equity` only ratchets up at the actual EOD boundary - not on every intraday
    tick, which would trail far tighter than Apex's account actually does. Backtest/preview
    callers that don't track a real EOD boundary default to True (today's prior behavior,
    an intraday-trailing approximation) since they're for strategy ranking, not real money.
    """
    prior_peak = account.get("peak_equity") or account["starting_balance"]
    peak_equity = max(prior_peak, current_equity) if is_eod_close else prior_peak
    floor = compute_floor(peak_equity, account["starting_balance"], account["max_drawdown"])
    return {
        "peak_equity": peak_equity,
        "drawdown_floor": floor,
        "breached": current_equity <= floor,
        "passed": current_equity >= account["starting_balance"] + account["profit_target"],
    }
