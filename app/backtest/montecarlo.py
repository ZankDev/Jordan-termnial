"""Monte Carlo trade-order resampling: the practical, deployable version of "how bad
could this realistically get" — the risk-analysis goal behind the Quantum Amplitude
Estimation ask, without requiring actual quantum hardware (see conversation: QAE's
quadratic speedup only exists on real quantum circuits; simulating one classically costs
more than just doing the classical computation directly, and at this problem's scale —
resampling a few dozen to a few hundred historical trades, not continuous price paths —
classical Monte Carlo already runs in well under a second).

Standard technique: a strategy's realized sequence of trade P&Ls is only ONE of the many
orders those same trades could plausibly have happened in. Bootstrap-resampling (with
replacement) rebuilds thousands of alternate equity-curve paths from the same trade
outcomes, and the spread across those paths — not the single historical path — is what
answers "how much could this really lose" (VaR/CVaR) and "how likely is a burned
account" in a statistically honest way.
"""
from __future__ import annotations

import random
from typing import Any

from app.db import BURN_LOSS_FRACTION


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[idx]


def run_monte_carlo(
    trade_pnls: list[float],
    starting_balance: float = 50_000.0,
    n_simulations: int = 1000,
    seed: int | None = None,
) -> dict[str, Any]:
    """Bootstrap-resample `trade_pnls` (with replacement) `n_simulations` times, each
    resampling producing one alternate ordering of the same trade outcomes rebuilt into
    an equity curve. Returns the distribution's key percentiles — this is VaR/CVaR-style
    tail-risk analysis: the 5th-percentile max drawdown is the "bad, but real" scenario,
    not just the one path the backtest happened to trace."""
    if not trade_pnls:
        return {
            "n_simulations": 0,
            "n_trades": 0,
            "max_drawdown_pct": {"p5": 0.0, "p50": 0.0, "p95": 0.0},
            "ending_equity": {"p5": 0.0, "p50": 0.0, "p95": 0.0},
            "return_pct": {"p5": 0.0, "p50": 0.0, "p95": 0.0},
            "prob_burned": 0.0,
            "value_at_risk_95_pct": 0.0,
            "cvar_95_pct": 0.0,
        }

    rng = random.Random(seed)
    n_trades = len(trade_pnls)
    max_drawdowns_pct: list[float] = []
    ending_equities: list[float] = []
    burned_count = 0

    for _ in range(n_simulations):
        equity = starting_balance
        peak = starting_balance
        max_dd = 0.0
        burned_this_run = False
        for _ in range(n_trades):
            pnl = trade_pnls[rng.randrange(n_trades)]
            equity += pnl
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
            if not burned_this_run and (starting_balance - equity) >= BURN_LOSS_FRACTION * starting_balance:
                burned_this_run = True
        if burned_this_run:
            burned_count += 1
        max_drawdowns_pct.append(max_dd / starting_balance * 100 if starting_balance > 0 else 0.0)
        ending_equities.append(equity)

    max_drawdowns_pct.sort()
    ending_equities.sort()
    returns_pct = sorted((e - starting_balance) / starting_balance * 100 for e in ending_equities)

    # VaR 95%: the loss level such that 95% of simulated outcomes are no worse than this —
    # i.e. the 5th percentile of ending equity, expressed as a dollar loss from starting balance.
    var_95_equity = _percentile(ending_equities, 5)
    value_at_risk_95 = max(0.0, starting_balance - var_95_equity)
    # CVaR 95% (expected shortfall): the average loss across just the worst 5% of runs —
    # a harsher, more honest tail measure than VaR alone (VaR ignores how bad the tail gets).
    tail_cutoff_idx = max(1, round(0.05 * len(ending_equities)))
    worst_tail = ending_equities[:tail_cutoff_idx]
    cvar_95 = max(0.0, starting_balance - (sum(worst_tail) / len(worst_tail)))

    return {
        "n_simulations": n_simulations,
        "n_trades": n_trades,
        "max_drawdown_pct": {
            "p5": round(_percentile(max_drawdowns_pct, 5), 2),
            "p50": round(_percentile(max_drawdowns_pct, 50), 2),
            "p95": round(_percentile(max_drawdowns_pct, 95), 2),
        },
        "ending_equity": {
            "p5": round(_percentile(ending_equities, 5), 2),
            "p50": round(_percentile(ending_equities, 50), 2),
            "p95": round(_percentile(ending_equities, 95), 2),
        },
        "return_pct": {
            "p5": round(_percentile(returns_pct, 5), 2),
            "p50": round(_percentile(returns_pct, 50), 2),
            "p95": round(_percentile(returns_pct, 95), 2),
        },
        "prob_burned": round(burned_count / n_simulations * 100, 2),
        "value_at_risk_95_pct": round(value_at_risk_95, 2),
        "cvar_95_pct": round(cvar_95, 2),
    }
