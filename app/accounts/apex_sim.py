"""Would this strategy pass an Apex evaluation? Replays a closed-trade sequence through
NOSTRO's Apex rules (app/accounts/apex_rules.py) from many different start dates and
reports how often it hits the profit target before breaching the trailing drawdown.

Pure functions, no I/O. This is the "passes NOSTRO" measure: one starting date is an
anecdote, the pass rate across every plausible start is evidence.

Rules replayed (from apex_rules): profit target, and a trailing drawdown whose floor
follows peak equity down by max_drawdown but locks at the starting balance once profit
reaches that level (compute_floor). Breach is checked before target within a trade.

Known limits, stated plainly:
  * Closed-trade granularity. Real Apex trails the drawdown on intraday equity. Where a trade
    carries its best/worst intra-trade excursion (mfe/mae, available for the bar-by-bar
    strategies) the replay is pessimistic about it: the favourable excursion raises the peak
    first, then the adverse excursion is tested against the raised floor. The three
    session strategies have no excursion data and are judged on closed trades only.
  * No consistency rule, no time limit, no contract-count or news restrictions beyond what
    position sizing already applies.
The result is flagged conservative accordingly.
"""
from __future__ import annotations

import statistics
from typing import Any

from app.accounts.apex_rules import TIERS, compute_floor

# (exit_time ISO string, net pnl, mfe pnl >= 0 or None, mae pnl <= 0 or None)
TradeTuple = tuple


def _day(t: TradeTuple) -> str:
    return str(t[0])[:10]


def apex_pass_rate(
    trades: list[TradeTuple],
    tier_size: int = 50_000,
    max_starts: int = 200,
    min_days_after_start: int = 3,
) -> dict[str, Any]:
    tier = TIERS[tier_size]
    target, max_dd = float(tier["profit_target"]), float(tier["max_drawdown"])
    start_bal = float(tier_size)

    ordered = sorted(trades, key=lambda t: str(t[0]))
    result: dict[str, Any] = {
        "tier": tier_size, "n_trades": len(ordered), "n_starts": 0, "passed": 0, "breached": 0, "unresolved": 0,
        "pass_rate": None, "breach_rate": None, "unresolved_rate": None, "resolved_pass_rate": None,
        "median_trades_to_pass": None, "median_days_to_pass": None,
        "conservative": True,
        "pessimistic_excursions": any(len(t) > 3 and (t[2] or t[3]) for t in ordered),
    }
    if not ordered:
        return result

    # candidate start points: the first trade of each trading day
    day_first: list[int] = []
    last_day = None
    for i, t in enumerate(ordered):
        d = _day(t)
        if d != last_day:
            day_first.append(i)
            last_day = d
    all_days = [_day(ordered[i]) for i in day_first]
    last_trade_day = _day(ordered[-1])

    # right-censoring: a start with fewer than min_days_after_start days of data left proves nothing
    eligible = []
    for i in day_first:
        remaining = len([d for d in all_days if d >= _day(ordered[i])])
        if remaining >= min_days_after_start:
            eligible.append(i)
    if not eligible:
        return result
    if len(eligible) > max_starts:  # evenly spaced sample
        step = len(eligible) / max_starts
        eligible = [eligible[int(k * step)] for k in range(max_starts)]

    passed = breached = unresolved = 0
    trades_to_pass: list[int] = []
    days_to_pass: list[int] = []
    for i in eligible:
        eq, peak = start_bal, start_bal
        outcome = "unresolved"
        for k in range(i, len(ordered)):
            t = ordered[k]
            pnl = float(t[1])
            mfe = float(t[2]) if len(t) > 2 and t[2] else 0.0
            mae = float(t[3]) if len(t) > 3 and t[3] else 0.0
            # pessimistic intra-trade path: best excursion first, then worst, against the raised floor
            peak = max(peak, eq + mfe)
            if eq + mae <= compute_floor(peak, start_bal, max_dd):
                outcome = "breached"
                break
            eq += pnl
            peak = max(peak, eq)
            if eq <= compute_floor(peak, start_bal, max_dd):
                outcome = "breached"
                break
            if eq >= start_bal + target:
                outcome = "passed"
                trades_to_pass.append(k - i + 1)
                days_to_pass.append(len({_day(x) for x in ordered[i:k + 1]}))
                break
        if outcome == "passed":
            passed += 1
        elif outcome == "breached":
            breached += 1
        else:
            unresolved += 1

    n = len(eligible)
    resolved = passed + breached
    result.update(
        n_starts=n, passed=passed, breached=breached, unresolved=unresolved,
        pass_rate=round(passed / n, 4), breach_rate=round(breached / n, 4), unresolved_rate=round(unresolved / n, 4),
        resolved_pass_rate=round(passed / resolved, 4) if resolved else None,
        median_trades_to_pass=statistics.median(trades_to_pass) if trades_to_pass else None,
        median_days_to_pass=statistics.median(days_to_pass) if days_to_pass else None,
        last_trade_day=last_trade_day,
    )
    return result
