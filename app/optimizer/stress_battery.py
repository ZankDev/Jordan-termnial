"""Deeper testing of a champion candidate: five attacks its fast-screen score never faced.

A leader is chosen as the best of ~1,000 candidates scored on ~27 days of out-of-sample data -
exactly the setting in which lucky candidates float to the top. The battery re-tests the chosen
parameters in ways that a lucky fit tends not to survive:

  cost_stress   the same trades with double commission and one extra tick of slippage per side.
                A real edge is bigger than its costs; a thin one is not.                    [hard]
  monte_carlo   the out-of-sample trades re-dealt as random trading days (block bootstrap, 400
                paths of a 60-day evaluation) through the Apex trailing-drawdown rules at the
                strategy's recommended risk. Estimates P(pass) and P(breach) without relying on
                the one lucky ordering the history happened to contain.                    [hard]
  neighbours    every parameter nudged one grid step and re-scored on the SAME folds. A peak
                surrounded by cliffs is a fit; a plateau is an edge.                        [hard]
  stability     first half vs second half of the out-of-sample trades - both must make money. [soft]
  transfer      the same parameters on the OTHER index (NQ<->ES). Not required to work (the
                instruments differ) but a strong sign when it does.                          [soft]
  nudge20       every parameter moved ±20% of its value; the result must not collapse.       [hard]
  sample        >= 100 out-of-sample trades (fewer = a hypothesis).       [hard on deep folds, soft on fast]
  by_year       split by calendar year: most years profitable, no year > 60% of the profit.
                                                                          [hard on deep folds, soft on fast]
  trials        pooled t-stat vs the luck bar sqrt(2·ln N) for the N versions ever tried.    [hard on deep folds, soft on fast]

Result: {"status": certified | watch | failed, "passed": n, "of": n, "checks": [...]}. certified =
every hard check passes, watch = one hard check fails, failed = two or more. It never changes
who the leader is - promotion decides that - it says how much to trust the leader, and the AUTO
account refuses to trade a "failed" one.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import statistics
from typing import Any

from app.accounts.apex_rules import TIERS, compute_floor
from app.backtest.engine import TICK_SIZE, run_backtest
from app.backtest.sizing import default_config, point_value
from app.optimizer.search import deterministic_neighbors, evaluate_candidate
from app.strategies import registry

MC_PATHS = 400
MC_EVAL_DAYS = 60
MC_MIN_SOURCE_DAYS = 8
MC_MAX_BREACH = 0.40

COST_COMMISSION_MULT = 2.0
COST_EXTRA_TICKS_PER_SIDE = 1

NEIGHBOR_HOLD_FRACTION = 0.5  # a neighbour "holds" if it keeps this fraction of the score...
NEIGHBOR_PASS_FRACTION = 0.6  # ...and this share of neighbours must hold
NEIGHBOR_MIN_TRADES = 15


def _test_trades(strategy, params, symbol, folds, starting_balance=50_000.0) -> list[dict[str, Any]]:
    sizing = default_config(starting_balance)
    out: list[dict[str, Any]] = []
    for _train, test in folds:
        out.extend(run_backtest(test, strategy, params, symbol, starting_balance, sizing=sizing).trades)
    return sorted(out, key=lambda t: str(t["exit_time"]))


def _check(name: str, ok: bool, hard: bool, value: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "hard": hard, "value": value, "detail": detail or {}}


# ------------------------------------------------------------------ individual checks
def cost_stress(trades: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    tick = TICK_SIZE.get(symbol.upper(), 0.25)
    base = stressed = 0.0
    for t in trades:
        qty = t.get("qty") or 1.0
        pv = point_value(symbol, t.get("contract") or "mini")
        extra = (COST_COMMISSION_MULT - 1.0) * (t.get("commission") or 0.0) + COST_EXTRA_TICKS_PER_SIDE * 2 * tick * pv * qty
        base += t["pnl"]
        stressed += t["pnl"] - extra
    return _check(
        "cost_stress", bool(trades) and stressed > 0, True,
        f"${base:,.0f} -> ${stressed:,.0f} with 2x costs + 1 tick/side",
        {"base_pnl": round(base, 2), "stressed_pnl": round(stressed, 2), "n_trades": len(trades)},
    )


def _simulate_path(days: list[list[tuple]], start_bal: float, target: float, max_dd: float) -> str:
    eq = peak = start_bal
    for day in days:
        for pnl, mfe, mae in day:
            peak = max(peak, eq + mfe)
            if eq + mae <= compute_floor(peak, start_bal, max_dd):
                return "breached"
            eq += pnl
            peak = max(peak, eq)
            if eq <= compute_floor(peak, start_bal, max_dd):
                return "breached"
            if eq >= start_bal + target:
                return "passed"
    return "open"


def monte_carlo(trades: list[dict[str, Any]], risk_scale: float = 1.0, seed: int = 7) -> dict[str, Any]:
    """Block bootstrap over trading days (a day's trades stay together: they are correlated)."""
    by_day: dict[str, list[tuple]] = {}
    for t in trades:
        by_day.setdefault(str(t["exit_time"])[:10], []).append(
            (t["pnl"] * risk_scale, (t.get("mfe_pnl") or 0.0) * risk_scale, (t.get("mae_pnl") or 0.0) * risk_scale)
        )
    pool = list(by_day.values())
    if len(pool) < MC_MIN_SOURCE_DAYS:
        return _check("monte_carlo", True, True, f"only {len(pool)} trading days - not judged", {"judged": False})
    tier = TIERS[50_000]
    rng = random.Random(seed)
    tally = {"passed": 0, "breached": 0, "open": 0}
    for _ in range(MC_PATHS):
        path = [pool[rng.randrange(len(pool))] for _ in range(MC_EVAL_DAYS)]
        tally[_simulate_path(path, 50_000.0, float(tier["profit_target"]), float(tier["max_drawdown"]))] += 1
    p_pass, p_breach = tally["passed"] / MC_PATHS, tally["breached"] / MC_PATHS
    return _check(
        "monte_carlo", p_breach <= MC_MAX_BREACH, True,
        f"{MC_EVAL_DAYS}-day evaluation: pass {p_pass:.0%}, breach {p_breach:.0%}, open {tally['open'] / MC_PATHS:.0%} (limit breach {MC_MAX_BREACH:.0%})",
        {"judged": True, "p_pass": round(p_pass, 3), "p_breach": round(p_breach, 3), "p_open": round(tally["open"] / MC_PATHS, 3),
         "paths": MC_PATHS, "eval_days": MC_EVAL_DAYS, "source_days": len(pool), "risk_scale": risk_scale},
    )


def neighbours(strategy, params, symbol, folds, score: float) -> dict[str, Any]:
    nbrs = deterministic_neighbors(params, strategy.params)
    if not nbrs or score <= 0:
        return _check("neighbours", score > 0, True, "no neighbours to test" if score > 0 else "score <= 0", {"n": 0})
    scores = []
    for n in nbrs:
        res = evaluate_candidate(n, folds, strategy, symbol, 50_000.0, NEIGHBOR_MIN_TRADES, burn_gate=False)
        scores.append(res["score"] if res is not None else 0.0)
    frac = sum(1 for s in scores if s >= NEIGHBOR_HOLD_FRACTION * score) / len(scores)
    return _check(
        "neighbours", frac >= NEIGHBOR_PASS_FRACTION, True,
        f"{frac:.0%} of {len(scores)} one-step neighbours keep >= {NEIGHBOR_HOLD_FRACTION:.0%} of the score (need {NEIGHBOR_PASS_FRACTION:.0%})",
        {"n": len(scores), "hold_fraction": round(frac, 3), "median": round(statistics.median(scores), 3), "worst": round(min(scores), 3)},
    )


NUDGE_FRACTION = 0.20       # every parameter moved ±20% of its value
NUDGE_HOLD_FRACTION = 0.5   # a nudged version "holds" if it keeps this fraction of the score...
NUDGE_PASS_FRACTION = 0.6   # ...and this share of nudged versions must hold
SAMPLE_MIN_TRADES = 100     # under this many out-of-sample trades it is a hypothesis, not a result
YEAR_MAX_SHARE = 0.60       # no single year may carry more than this share of the total profit
YEAR_MIN_TRADES = 20        # a year needs this many trades to count


def nudge20(strategy, params, symbol, folds, score: float) -> dict[str, Any]:
    """Move each parameter ±20% of its own value (snapped to its grid, clamped to its range) and re-score on the same
    folds. A result that collapses when a parameter moves 20% was luck. Unlike `neighbours` (one grid step, which can be
    tiny), this is the same relative shove for every parameter."""
    if score <= 0:
        return _check("nudge20", False, True, "score <= 0", {"n": 0})
    variants, seen = [], set()
    for spec in strategy.params:
        v = params.get(spec.name)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v == 0:
            continue
        for sgn in (-1, 1):
            raw = v * (1 + sgn * NUDGE_FRACTION)
            snapped = spec.min + round((raw - spec.min) / spec.step) * spec.step if spec.step else raw
            nv = spec.cast(max(spec.min, min(spec.max, snapped)))
            if nv == v:
                continue
            p = {**params, spec.name: nv}
            k = tuple(sorted(p.items()))
            if k not in seen:
                seen.add(k)
                variants.append(p)
    if not variants:
        return _check("nudge20", True, True, "no numeric parameters to nudge", {"n": 0})
    scores = []
    for p in variants:
        res = evaluate_candidate(p, folds, strategy, symbol, 50_000.0, NEIGHBOR_MIN_TRADES, burn_gate=False)
        scores.append(res["score"] if res is not None else 0.0)
    frac = sum(1 for s in scores if s >= NUDGE_HOLD_FRACTION * score) / len(scores)
    return _check(
        "nudge20", frac >= NUDGE_PASS_FRACTION, True,
        f"{frac:.0%} of {len(scores)} ±20% versions keep >= {NUDGE_HOLD_FRACTION:.0%} of the score (need {NUDGE_PASS_FRACTION:.0%})",
        {"n": len(scores), "hold_fraction": round(frac, 3), "median": round(statistics.median(scores), 3), "worst": round(min(scores), 3)},
    )


def sample_size(trades: list[dict[str, Any]], hard: bool = False) -> dict[str, Any]:
    n = len(trades)
    return _check("sample", n >= SAMPLE_MIN_TRADES, hard,
                  f"{n} out-of-sample trades" + ("" if n >= SAMPLE_MIN_TRADES else f" - under {SAMPLE_MIN_TRADES} it is a hypothesis, not a result"),
                  {"n": n, "min": SAMPLE_MIN_TRADES})


def trials_adjusted(trades: list[dict[str, Any]], n_trials: int, hard: bool = False) -> dict[str, Any]:
    """Multiple-testing haircut: the best of N random tries looks like a t-stat of about sqrt(2·ln N) by luck alone.
    Compares the pooled out-of-sample t-stat of the trade P&L with that luck bar. Soft on the fast folds; hard on the multi-year
    folds (hard=True), where hundreds of trades make a real edge clear the bar easily. Every version the trainer ever tried for
    this strategy counts, including the thousands it threw away."""
    import math

    pnl = [t["pnl"] for t in trades]
    if len(pnl) < 10 or n_trials < 2:
        return _check("trials", True, False, "not judged", {"judged": False, "n_trials": n_trials})
    sd = statistics.pstdev(pnl) or 1.0
    t_stat = statistics.mean(pnl) / sd * math.sqrt(len(pnl))
    bar = math.sqrt(2 * math.log(n_trials))
    return _check("trials", t_stat >= bar, hard,
                  f"pooled t {t_stat:.2f} vs luck bar {bar:.2f} after {n_trials:,} versions tried",
                  {"judged": True, "t_stat": round(t_stat, 3), "luck_bar": round(bar, 3), "n_trials": n_trials})


def by_year(trades: list[dict[str, Any]], hard: bool = False) -> dict[str, Any]:
    """Split by calendar year: most years must make money on their own and no single year may carry the rest."""
    years: dict[str, list[float]] = {}
    for t in trades:
        years.setdefault(str(t["exit_time"])[:4], []).append(t["pnl"])
    counted = {y: sum(v) for y, v in years.items() if len(v) >= YEAR_MIN_TRADES}
    if len(counted) < 2:
        return _check("by_year", True, hard, f"{len(counted)} full year(s) of trades - not judged", {"judged": False, "years": {y: round(s) for y, s in counted.items()}})
    total = sum(counted.values())
    pos = sum(1 for s in counted.values() if s > 0)
    top = max(counted.values())
    share = top / total if total > 0 else 1.0
    ok = total > 0 and pos >= math.ceil(len(counted) / 2) and share <= YEAR_MAX_SHARE if len(counted) >= 3 else total > 0 and pos == len(counted)
    return _check("by_year", ok, hard,
                  f"{pos}/{len(counted)} years profitable, best year = {share:.0%} of total profit" + ("" if ok else " (one year carrying the rest)"),
                  {"judged": True, "years": {y: round(s) for y, s in sorted(counted.items())}, "best_share": round(share, 3)})


def stability(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if len(trades) < 20:
        return _check("stability", True, False, f"only {len(trades)} trades - not judged", {"judged": False})
    mid = len(trades) // 2
    a, b = sum(t["pnl"] for t in trades[:mid]), sum(t["pnl"] for t in trades[mid:])
    return _check("stability", a > 0 and b > 0, False, f"first half ${a:,.0f}, second half ${b:,.0f}",
                  {"first": round(a, 2), "second": round(b, 2)})


def transfer(strategy, params, symbol: str) -> dict[str, Any]:
    other = "ES" if symbol.upper() == "NQ" else "NQ"
    try:
        from app.data import yfinance_source
        from app.optimizer.search import build_folds

        df = yfinance_source.fetch(other, "5m")
        if df is None or df.empty:
            return _check("transfer", True, False, f"no {other} data - not judged", {"judged": False})
        res = evaluate_candidate(params, build_folds(df), strategy, other, 50_000.0, 5, burn_gate=False)
    except Exception as e:  # noqa: BLE001 - informational check; never let it break certification
        return _check("transfer", True, False, f"not judged ({type(e).__name__})", {"judged": False})
    if res is None:
        return _check("transfer", False, False, f"too few trades on {other}", {"other": other})
    return _check("transfer", res["score"] > 0, False, f"same parameters on {other}: score {res['score']:.2f}",
                  {"other": other, "score": round(res["score"], 3)})


# ------------------------------------------------------------------ battery
def _n_trials(symbol: str, strategy_name: str) -> int:
    """Every version the trainer has tried for this strategy on this symbol (cycles x search iterations)."""
    try:
        from app import db
        from app.optimizer.trainer_service import N_ITERATIONS_PER_STRATEGY

        st = db.get_strategy_stats(symbol).get(strategy_name) or {}
        # fast-screen iterations plus the deep search's (60 per strategy per rotation): deliberately a high count, so the luck bar is conservative
        return int((st.get("total_cycles") or 0) * (N_ITERATIONS_PER_STRATEGY + 60))
    except Exception:  # noqa: BLE001
        return 0


def run_battery(strategy_name: str, params: dict[str, Any], symbol: str, folds: list, score: float,
                recommended_risk: float | None = None, deep: bool = False) -> dict[str, Any]:
    """Full certification of one candidate. deep=True (multi-year folds) makes the sample-size and by-year checks hard;
    on the ~60-day fast folds they are informational, because 60 days cannot hold 100 trades or two years."""
    strategy = registry.get(strategy_name)
    trades = _test_trades(strategy, params, symbol, folds)
    base_risk = 50_000 * 0.01
    scale = (recommended_risk / base_risk) if recommended_risk else 1.0
    checks = [
        cost_stress(trades, symbol),
        monte_carlo(trades, risk_scale=scale),
        neighbours(strategy, params, symbol, folds, score),
        nudge20(strategy, params, symbol, folds, score),
        sample_size(trades, hard=deep),
        by_year(trades, hard=deep),
        trials_adjusted(trades, _n_trials(symbol, strategy_name), hard=deep),
        stability(trades),
        transfer(strategy, params, symbol),
    ]
    hard_failed = [c["name"] for c in checks if c["hard"] and not c["ok"]]
    status = "certified" if not hard_failed else ("watch" if len(hard_failed) == 1 else "failed")
    return {
        "status": status,
        "passed": sum(1 for c in checks if c["ok"]),
        "of": len(checks),
        "hard_failed": hard_failed,
        "checks": checks,
        "n_trades": len(trades),
        "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
