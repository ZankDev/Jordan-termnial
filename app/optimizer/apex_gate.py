"""Apex pass-rate gates used by promotion ("does this strategy pass NOSTRO?").

Whether a strategy survives an Apex evaluation depends heavily on how much it risks per
trade, not just on its edge: measured on real NQ data, rsi_reversal breaches the $2,000
trailing drawdown from 73% of start dates at $500 risk per trade, but passes from 76% of
start dates with 0% breaches at $175. So the gate does not just ask "does it pass at the
default size" - it searches a small grid of risk scales and reports the best one, and that
recommended risk is what an account following the strategy should use.

Trade P&L scales ~linearly with position size, so a candidate's out-of-sample trades are
rescaled by k rather than re-backtested (quantisation of whole micro contracts is ignored;
the recommendation is a guide, not a guarantee).

Two strengths, because the two stages have very different amounts of evidence:

  fast proxy  ~27 days of out-of-sample trades from the 60-day screen. Too short to prove a
              pass, but enough to reject a strategy that keeps blowing the trailing drawdown
              even at its best risk level.
  confirmed   the concatenated multi-year out-of-sample trades from deep validation: must
              actually pass often enough, breach rarely, and leave few starts unresolved.
"""
from __future__ import annotations

from typing import Any

from app.accounts.apex_sim import apex_pass_rate
from app.backtest.sizing import RISK_PCT_OF_STARTING

BASE_RISK_DOLLARS = 50_000 * RISK_PCT_OF_STARTING  # the risk the optimizer's P&L was sized at
RISK_SCALES = (0.2, 0.35, 0.5, 0.75, 1.0)

LEADER_MAX_BREACH_RATE = 0.50
LEADER_MIN_STARTS = 8  # fewer start dates than this and the proxy says nothing either way

# Confirmed gate (GATE_VERSION 2). v1 demanded pass_rate >= 60% of ALL start dates and <= 10% unresolved.
# That was a methodology error: a start date is "unresolved" when the history ends (or, for a safe low-risk
# strategy, before the $3,000 target is reached), which says nothing about the strategy - measured on real
# NQ, rsi_reversal at $175 risk passed from 82% of start dates with ZERO breaches and was rejected only because
# 18% of starts were still open. v2 judges what actually matters: of the evaluations that concluded, how many
# passed; how often the account was blown; and whether passing is fast enough to be useful.
GATE_VERSION = 2
CONFIRM_MIN_RESOLVED_PASS = 0.65  # of concluded (passed or breached) start dates
CONFIRM_MAX_BREACH_RATE = 0.30  # of all counted start dates - blowing the account is the failure that matters
CONFIRM_MAX_UNRESOLVED = 0.40  # a mostly-open strategy is just slow/flat; too many means it never gets anywhere
CONFIRM_MIN_RESOLVED = 30  # concluded start dates needed before the numbers mean anything
CONFIRM_MIN_STARTS = 60
CONFIRM_MIN_DAYS_AFTER_START = 20  # starts closer than this to the end of history are right-censored, not counted
CONFIRM_MAX_MEDIAN_DAYS_TO_PASS = 60  # trading days


def _scaled(trades: list, k: float) -> list:
    return [(t[0], t[1] * k, (t[2] or 0.0) * k, (t[3] or 0.0) * k) for t in trades]


def analyse(trades: list, min_days_after_start: int, max_starts: int = 200) -> dict[str, Any]:
    """Apex replay at each risk scale; returns the best one (highest pass rate minus breach
    rate, larger size on ties) with the full grid attached."""
    grid = []
    for k in RISK_SCALES:
        r = apex_pass_rate(_scaled(trades, k), min_days_after_start=min_days_after_start, max_starts=max_starts)
        grid.append((k, r))
    valid = [(k, r) for k, r in grid if r["n_starts"]]
    if not valid:
        _, empty = grid[-1]
        empty["risk_scale"], empty["recommended_risk_dollars"], empty["by_scale"] = 1.0, BASE_RISK_DOLLARS, []
        return empty
    best_k, best = max(valid, key=lambda kr: (kr[1]["pass_rate"] - kr[1]["breach_rate"], kr[0]))
    best = dict(best)
    best["risk_scale"] = best_k
    best["recommended_risk_dollars"] = round(BASE_RISK_DOLLARS * best_k, 0)
    best["by_scale"] = [
        {"risk": round(BASE_RISK_DOLLARS * k), "pass": r["pass_rate"], "breach": r["breach_rate"], "open": r["unresolved_rate"]}
        for k, r in valid
    ]
    return best


def fast_proxy(test_trades: list) -> tuple[bool, dict[str, Any]]:
    """Leader gate. Returns (ok, info); info always carries the pass/breach numbers."""
    info = analyse(test_trades, min_days_after_start=3, max_starts=60)
    if info["n_starts"] < LEADER_MIN_STARTS:
        info["reason"] = f"only {info['n_starts']} usable start dates - proxy not judged"
        info["judged"] = False
        return True, info
    info["judged"] = True
    if info["breach_rate"] > LEADER_MAX_BREACH_RATE:
        info["reason"] = (
            f"breaches the Apex trailing drawdown from {info['breach_rate']:.0%} of start dates even at its "
            f"best risk (${info['recommended_risk_dollars']:.0f}); limit {LEADER_MAX_BREACH_RATE:.0%}"
        )
        return False, info
    info["reason"] = f"within the Apex drawdown proxy at ${info['recommended_risk_dollars']:.0f} risk per trade"
    return True, info


def confirmed_gate(test_trades: list) -> tuple[bool, dict[str, Any]]:
    """Confirmed-tier gate on the multi-month out-of-sample trades (see GATE_VERSION note above)."""
    info = analyse(test_trades, min_days_after_start=CONFIRM_MIN_DAYS_AFTER_START)
    info["gate_version"] = GATE_VERSION
    if info["n_starts"] < CONFIRM_MIN_STARTS:
        info["reason"] = f"only {info['n_starts']} start dates (need {CONFIRM_MIN_STARTS}) - too little history to judge"
        return False, info
    resolved = info["passed"] + info["breached"]
    problems = []
    if resolved < CONFIRM_MIN_RESOLVED:
        problems.append(f"only {resolved} evaluations concluded (need {CONFIRM_MIN_RESOLVED})")
    elif info["resolved_pass_rate"] < CONFIRM_MIN_RESOLVED_PASS:
        problems.append(f"only {info['resolved_pass_rate']:.0%} of concluded evaluations passed (need {CONFIRM_MIN_RESOLVED_PASS:.0%})")
    if info["breach_rate"] > CONFIRM_MAX_BREACH_RATE:
        problems.append(f"breaches from {info['breach_rate']:.0%} of start dates (limit {CONFIRM_MAX_BREACH_RATE:.0%})")
    if info["unresolved_rate"] > CONFIRM_MAX_UNRESOLVED:
        problems.append(f"{info['unresolved_rate']:.0%} of starts never conclude (limit {CONFIRM_MAX_UNRESOLVED:.0%})")
    med = info.get("median_days_to_pass")
    if med is not None and med > CONFIRM_MAX_MEDIAN_DAYS_TO_PASS:
        problems.append(f"median {med:.0f} trading days to pass (limit {CONFIRM_MAX_MEDIAN_DAYS_TO_PASS})")
    if problems:
        info["reason"] = "Apex replay (best risk ${:.0f}): ".format(info["recommended_risk_dollars"]) + "; ".join(problems)
        return False, info
    info["reason"] = (
        f"{info['resolved_pass_rate']:.0%} of concluded Apex evaluations pass ({info['passed']} of {resolved}), "
        f"{info['breach_rate']:.0%} breach, median {med if med is not None else '?'} days, at ${info['recommended_risk_dollars']:.0f} risk per trade"
    )
    return True, info
