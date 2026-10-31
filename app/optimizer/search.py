"""Random-search parameter optimizer: the "training" step.

Not a black-box model — it just tries many parameter sets for a rule-based strategy,
backtests each on a training window, re-validates the survivors on a held-out test
window (walk-forward, no shuffling), and ranks by out-of-sample performance. This
keeps every result traceable back to an explicit, human-readable rule set.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from typing import Any

import numpy as np
import pandas as pd

from app import db
from app.backtest.engine import run_backtest
from app.backtest.sizing import default_config
from app.optimizer import regime
from app.strategies.base import ParamSpec, Strategy

# Minimum trades needed in EACH regime bucket before the consistency check applies —
# below this a regime split is too small a sample to judge, so it's skipped rather
# than penalizing a candidate for a data-scarcity artifact.
MIN_TRADES_PER_REGIME = 6
# How lopsided the two regimes' profit factors have to be to flag inconsistency —
# comfortably apart, not a razor's-edge 1.05-vs-0.95 split.
REGIME_PROFITABLE_PF = 1.3
REGIME_LOSING_PF = 0.7


def _regime_inconsistent(trades: list[dict[str, Any]], day_labels: dict[dt.date, str]) -> bool:
    """True if the strategy is solidly profitable in one regime (trending/choppy)
    and solidly losing in the other, with enough trades in both to judge — reuses
    the exact trades a fold's test backtest already produced, no extra backtest
    runs needed."""
    buckets: dict[str, list[float]] = {"trending": [], "choppy": []}
    for t in trades:
        day = dt.date.fromisoformat(t["entry_time"][:10])
        label = day_labels.get(day)
        if label:
            buckets[label].append(t["pnl"])

    if len(buckets["trending"]) < MIN_TRADES_PER_REGIME or len(buckets["choppy"]) < MIN_TRADES_PER_REGIME:
        return False

    def profit_factor(pnls: list[float]) -> float:
        wins = sum(p for p in pnls if p > 0)
        losses = abs(sum(p for p in pnls if p <= 0))
        return (wins / losses) if losses > 0 else (float("inf") if wins > 0 else 0.0)

    pf_trend = profit_factor(buckets["trending"])
    pf_chop = profit_factor(buckets["choppy"])
    return (pf_trend > REGIME_PROFITABLE_PF and pf_chop < REGIME_LOSING_PF) or (
        pf_chop > REGIME_PROFITABLE_PF and pf_trend < REGIME_LOSING_PF
    )


# Day-clustered significance check. edge_t_stat (app/backtest/engine.py) computes a one-sample
# t-stat treating every TRADE as an independent draw, but trades on the same day share the same
# regime/volatility/news, so they are not independent - a strategy that fires several times on
# the same handful of good days can post a trade-level t-stat that looks strong purely because
# n is inflated, not because the edge is real. Re-computing the same t-stat on DAY-SUMMED pnl
# (same day-bucketing already used by _regime_inconsistent and stress_battery.monte_carlo) gives
# the honest sample size. A trade-level score that collapses once clustering is accounted for is
# flagged exactly like "diverged" or "too_smooth" - it never changes the score itself, so every
# margin/threshold calibrated against edge_t_stat's scale elsewhere stays valid.
CLUSTER_MIN_DAYS = 6  # fewer distinct days than this and a day-level t-stat is too noisy to judge
CLUSTER_TRADE_T = 2.0  # trade-level t-stat has to look "significant" for this check to even apply
CLUSTER_DAY_T = 1.0  # ...and collapse below this on a day-summed basis to count as inflated


def _day_edge_t_stat(trades: list[dict[str, Any]]) -> tuple[float, int]:
    """(t-stat, n_days) of pnl summed per calendar day - the clustering-robust counterpart of
    edge_t_stat, reusing the exact trades a fold's test backtest already produced."""
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[str(t["entry_time"])[:10]] = by_day.get(str(t["entry_time"])[:10], 0.0) + t["pnl"]
    day_pnls = list(by_day.values())
    n = len(day_pnls)
    if n < 2:
        return 0.0, n
    mean = sum(day_pnls) / n
    variance = sum((p - mean) ** 2 for p in day_pnls) / (n - 1)
    std = variance ** 0.5
    return ((mean / std * (n ** 0.5)) if std > 0 else 0.0), n


def _sample_param(spec: ParamSpec, rng: random.Random) -> float:
    steps = max(1, round((spec.max - spec.min) / spec.step))
    idx = rng.randint(0, steps)
    value = spec.min + idx * spec.step
    return spec.cast(value)


# Explore/exploit search for _search_given_folds. Two designs were tried empirically
# (A/B'd against plain uniform-random search across 8 strategies x 3 seeds each,
# same iteration budget) before picking these numbers:
#   - A hard "explore first 40%, then only ever exploit" split measurably beat plain
#     random search on most strategies, but sometimes regressed it (e.g. ema_crossover
#     dropped from a positive edge to a negative one on one seed) — these gated,
#     rule-based strategies have a discontinuous score landscape (a param nudge can
#     flip a candidate straight to None via evaluate_candidate's trade-count/diverged/
#     too_smooth gates), so committing hard to local exploitation too early is risky.
#   - A longer warmup (70% pure random) plus, for the remaining iterations, only a
#     30% per-iteration chance of exploiting (else another random draw) was
#     non-regressive on 20/21 informative strategy x seed pairs, with several large
#     wins, and the one strategy that still occasionally regressed slightly
#     (donchian_breakout) can only ever cost a cycle where nothing new gets promoted —
#     trainer_service only replaces the persisted champion when a candidate's score
#     strictly beats it, so a single below-par cycle never overwrites a better
#     existing champion, and the same strategy gets re-rolled with a fresh seed again
#     next cycle. That's the version below.
WARMUP_FRACTION = 0.7
EXPLOIT_PROB = 0.3
ELITE_POOL_SIZE = 10
MUTATION_MAX_STEPS = 3
REROLL_PROB = 0.15  # per-param chance a "mutation" fully re-randomizes instead of nudges, to avoid collapsing onto one basin


def _mutate_params(parent: dict[str, Any], specs: list[ParamSpec], rng: random.Random) -> dict[str, Any]:
    """Perturbs a known-good param set by a few grid steps per param (occasionally
    fully re-randomizing one via _sample_param) instead of drawing an entirely fresh
    random point — the exploitation half of the search's explore/exploit split."""
    mutated: dict[str, Any] = {}
    for spec in specs:
        if rng.random() < REROLL_PROB:
            mutated[spec.name] = _sample_param(spec, rng)
            continue
        steps = max(1, round((spec.max - spec.min) / spec.step))
        current_idx = round((parent[spec.name] - spec.min) / spec.step)
        jump = rng.randint(1, MUTATION_MAX_STEPS) * rng.choice([-1, 1])
        new_idx = min(max(current_idx + jump, 0), steps)
        value = spec.min + new_idx * spec.step
        mutated[spec.name] = spec.cast(value)
    return mutated


def _elite_parent(pool: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    """Rank-weighted pick from the elite pool for the exploit half of the search (was a uniform
    rng.choice): the best-ranked candidate is favoured over the weakest without ever excluding it
    outright. Rank, not raw score, does the weighting - a t-stat-style score can be negative or
    sit on a different scale strategy to strategy, but "1st beats 2nd beats 3rd" is always
    meaningful. Linear weights (best gets weight n, worst gets weight 1) were picked over
    anything sharper deliberately: this same search's own explore/exploit split was tuned
    empirically because these gated strategies have a discontinuous score landscape where
    committing too hard, too early collapses onto one basin (see WARMUP_FRACTION/EXPLOIT_PROB
    comment above) - a mild bias keeps that same caution while still using the pool's ordering
    instead of throwing it away."""
    n = len(pool)
    return rng.choices(pool, weights=[n - i for i in range(n)], k=1)[0]


def deterministic_neighbors(params: dict[str, Any], specs: list[ParamSpec]) -> list[dict[str, Any]]:
    """One-grid-step-in-each-direction neighbors of `params`, one param perturbed at a
    time (not a full combinatorial grid, and not random like _mutate_params above) —
    used to check whether a winning parameter set is a genuine local peak or a knife's-
    edge lucky spike before it's trusted as a confirmed champion (see
    trainer_service.py's _run_neighborhood_check_sync). Skips a direction that's
    already at its param's min/max (nothing to perturb into) and de-duplicates any
    neighbor identical to one already produced."""
    neighbors: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for spec in specs:
        steps = max(1, round((spec.max - spec.min) / spec.step))
        current_idx = round((params[spec.name] - spec.min) / spec.step)
        for direction in (-1, 1):
            new_idx = current_idx + direction
            if new_idx < 0 or new_idx > steps:
                continue
            value = spec.cast(spec.min + new_idx * spec.step)
            if value == params[spec.name]:
                continue
            neighbor = dict(params)
            neighbor[spec.name] = value
            key = tuple(sorted(neighbor.items()))
            if key in seen:
                continue
            seen.add(key)
            neighbors.append(neighbor)
    return neighbors


def is_eligible(r: dict[str, Any]) -> bool:
    """A candidate that clears every quality gate - the single definition of 'survivor'
    (leader eligibility, Field-page survivors). Deep validation / Apex / robustness are
    further stages applied only to the champion candidates, not to every result."""
    return not (
        r.get("diverged") or r.get("too_smooth") or r.get("exceeds_burn_limit") or r.get("regime_inconsistent")
        or r.get("clustering_inflated")
    )


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.blake2s(repr(sorted(params.items())).encode(), digest_size=4).hexdigest()


def build_folds(df: pd.DataFrame, k_folds: int = 3, train_frac: float = 0.7) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """The exact fold set random_search uses for a given df, so the trainer can re-score an
    incumbent champion on the SAME folds its challengers were just scored on."""
    folds = _split_k_fold_walk_forward(df, k=k_folds, test_frac=0.15)
    return folds or [_split_train_test(df, train_frac)]


def _split_train_test(df: pd.DataFrame, train_frac: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(set(df.index.date))
    split = max(1, int(len(dates) * train_frac))
    train_dates = set(dates[:split])
    test_dates = set(dates[split:]) or set(dates[-1:])
    train_mask = np.isin(df.index.date, list(train_dates))
    test_mask = np.isin(df.index.date, list(test_dates))
    return df[train_mask], df[test_mask]


def _split_k_fold_walk_forward(df: pd.DataFrame, k: int = 3, test_frac: float = 0.15) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """k expanding-window folds instead of one train/test split: fold i's test
    window is a block of the most recent-but-one days, its train window is
    EVERY day strictly before that block (not just the immediately preceding
    days) — each fold sees more history than the last, and a candidate has to
    hold up across several separate, temporally distinct out-of-sample windows
    instead of getting one shot at a lucky split. Folds are returned oldest
    test-window first. Degrades to fewer folds (never zero, unless there's too
    little data for even one) when the window is short."""
    dates = sorted(set(df.index.date))
    n = len(dates)
    block = max(1, int(n * test_frac))
    folds: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    for i in range(k):
        test_end_idx = n - i * block
        test_start_idx = test_end_idx - block
        if test_start_idx <= 0:
            break
        train_dates = set(dates[:test_start_idx])
        test_dates = set(dates[test_start_idx:test_end_idx])
        train_mask = np.isin(df.index.date, list(train_dates))
        test_mask = np.isin(df.index.date, list(test_dates))
        folds.append((df[train_mask], df[test_mask]))
    folds.reverse()
    return folds


def _split_walk_forward_year_folds(df: pd.DataFrame, years: int) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """(years - 1) rolling annual folds: train on year 1 alone, test on year 2;
    train on years 1-2, test on year 3; and so on — 'train 1 year ago, validate
    1 year from the second year', generalized across however much history is
    available instead of one fixed pair. years=2 reproduces the original single
    old/new split exactly."""
    max_ts = df.index.max()
    n_folds = max(1, years - 1)
    folds: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    for i in range(n_folds, 0, -1):
        test_end = max_ts - pd.DateOffset(years=i - 1)
        test_start = max_ts - pd.DateOffset(years=i)
        train_df = df[df.index < test_start]
        test_df = df[(df.index >= test_start) & (df.index < test_end)]
        if not train_df.empty and not test_df.empty:
            folds.append((train_df, test_df))
    return folds


def split_walk_forward_span_folds(df: pd.DataFrame, segments: int = 4) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Expanding walk-forward folds for a history too short for year folds (12-23 months):
    cut it into `segments` equal time spans; fold i trains on spans[:i] and tests on span i.
    Every test span is out-of-sample for the parameters' training data and they never overlap."""
    if df.empty or segments < 2:
        return []
    edges = pd.date_range(df.index.min(), df.index.max(), periods=segments + 1)
    folds: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    for i in range(1, segments):
        train_df = df[df.index < edges[i]]
        test_df = df[(df.index >= edges[i]) & (df.index < edges[i + 1] if i < segments - 1 else df.index <= edges[i + 1])]
        if not train_df.empty and not test_df.empty:
            folds.append((train_df, test_df))
    return folds


def evaluate_candidate(
    params: dict[str, Any],
    folds: list[tuple[pd.DataFrame, pd.DataFrame]],
    strategy: Strategy,
    symbol: str,
    starting_balance: float,
    min_test_trades: int,
    burn_gate: bool = True,
    collect_trades: bool = False,
    reject_info: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Score one fixed param set across every fold. Shared by the random-search loop
    (many candidate param sets) and the deep-validation step (one specific candidate,
    re-scored against a different, longer-horizon set of folds) — same rules either way,
    so a candidate can't pass one stage under looser logic than the other. Returns None
    if the candidate doesn't clear the trade-count bar on every fold.

    burn_gate=False skips the static per-fold max-drawdown flag (deep validation applies the
    Apex pass-rate test instead - a per-YEAR-fold $2,500 cap is unreachable and rejected
    every candidate). collect_trades=True adds "test_trades": [(exit_time, pnl, mfe, mae)]
    across all test folds for that Apex replay; it is never persisted to optimizer_runs."""
    # Scored on risk-sized (micro-contract) P&L off a fixed non-compounding balance, so every
    # candidate risks the same dollars per trade and fold scores stay comparable (epoch 3).
    sizing = default_config(starting_balance)
    test_trades: list[tuple[str, float, float, float]] = []
    fold_scores: list[float] = []
    fold_metrics: list[dict[str, Any]] = []
    diverged = False
    too_smooth = False
    exceeds_burn_limit = False
    regime_inconsistent = False
    clustering_inflated = False

    for train_df, test_df in folds:
        train_res = run_backtest(train_df, strategy, params, symbol, starting_balance, sizing=sizing)
        if train_res.metrics["total_trades"] == 0:
            if reject_info is not None:
                reject_info["reason"] = "no_train_trades"
            return None

        test_res = run_backtest(test_df, strategy, params, symbol, starting_balance, sizing=sizing)
        if test_res.metrics["total_trades"] < min_test_trades:
            if reject_info is not None:
                # keep the metrics of the fold that failed the trade-count gate: a rejected
                # candidate is still a point in The Field (a thin one, but a real one)
                reject_info["reason"] = "test_trades"
                reject_info["test_metrics"] = test_res.metrics
            return None

        train_pf = train_res.metrics["profit_factor"]
        test_pf = test_res.metrics["profit_factor"]
        # Signature of a noise-fit, not a real edge: losing in-sample but huge/infinite
        # out-of-sample. A genuine edge should look roughly consistent, not spike on a
        # window it was never fit to. Any single fold showing this disqualifies the
        # candidate — one clean fold doesn't excuse another that looks fabricated.
        if (train_pf is not None and train_pf < 1.0) and (test_pf is None or test_pf > 3.0):
            diverged = True

        # Overfitting-"too smooth" tell: a real edge, traded enough times, should show
        # *some* drawdown proportional to how much it made — real markets don't hand
        # you dozens of trades in a row with no pullback. An equity curve with barely
        # any drawdown relative to its total return, over a large-enough sample, is the
        # "curve looks great because it was fit to this exact path" red flag rather than
        # something to trust.
        ret = test_res.metrics["return_pct"]
        dd = test_res.metrics["max_drawdown_pct"]
        if test_res.metrics["total_trades"] >= 30 and ret > 0 and dd < 0.15 * ret:
            too_smooth = True

        # Hard account-survival gate: on a real $50k account, losing more than
        # BURN_LOSS_FRACTION (5% / $2,500) is a burned account (see db.py's
        # check_and_archive_if_burned) — a strategy whose own backtest drawdown would
        # have burned the very account it's meant to trade isn't a candidate, no matter
        # how good its other numbers look.
        if burn_gate and test_res.metrics["max_drawdown"] > db.BURN_LOSS_FRACTION * starting_balance:
            exceeds_burn_limit = True

        if collect_trades:
            test_trades.extend(
                (t["exit_time"], t["pnl"], t.get("mfe_pnl", 0.0), t.get("mae_pnl", 0.0)) for t in test_res.trades
            )

        # Regime robustness: a strategy solidly profitable in trending days and
        # solidly losing in choppy ones (or vice versa) isn't a robust edge, even
        # if its blended test-fold numbers look fine — see regime.py.
        if _regime_inconsistent(test_res.trades, regime.classify_days(test_df)):
            regime_inconsistent = True

        # A trade-level t-stat that looks significant only because trades cluster on a handful of
        # days is not a real edge - see CLUSTER_* comment above.
        day_t, n_days = _day_edge_t_stat(test_res.trades)
        if n_days >= CLUSTER_MIN_DAYS and test_res.metrics["edge_t_stat"] >= CLUSTER_TRADE_T and day_t < CLUSTER_DAY_T:
            clustering_inflated = True

        fold_scores.append(test_res.metrics["edge_t_stat"])
        fold_metrics.append({"train_metrics": train_res.metrics, "test_metrics": test_res.metrics,
                             "day_edge_t_stat": round(day_t, 3), "n_test_days": n_days})

    if not fold_scores:
        return None

    mean_score = sum(fold_scores) / len(fold_scores)
    if len(fold_scores) > 1:
        variance = sum((s - mean_score) ** 2 for s in fold_scores) / (len(fold_scores) - 1)
        std_score = variance ** 0.5
    else:
        std_score = 0.0
    # Reward consistency across folds, not just average performance: a strategy that
    # scores similarly well on every out-of-sample window is worth more than one that
    # spikes on one window and is mediocre on another, even at the same average.
    combined_score = mean_score - std_score

    last_fold = fold_metrics[-1]
    out = {
        "params": params,
        "train_metrics": last_fold["train_metrics"],
        "test_metrics": last_fold["test_metrics"],
        "fold_metrics": fold_metrics,
        "fold_scores": fold_scores,
        "score": combined_score,
        "diverged": diverged,
        "too_smooth": too_smooth,
        "exceeds_burn_limit": exceeds_burn_limit,
        "regime_inconsistent": regime_inconsistent,
        "clustering_inflated": clustering_inflated,
    }
    if collect_trades:
        out["test_trades"] = test_trades
    return out


def _search_given_folds(
    folds: list[tuple[pd.DataFrame, pd.DataFrame]],
    strategy: Strategy,
    symbol: str,
    n_iterations: int,
    starting_balance: float,
    min_test_trades: int,
    top_n: int,
    seed: int | None,
    sink=None,
    seed_params: list[dict[str, Any]] | None = None,
    burn_gate: bool = True,
) -> list[dict[str, Any]]:
    """seed_params are evaluated first (on top of n_iterations) - used to point a deep search at
    parameters that already look promising. burn_gate=False is for long-history folds, where
    the static per-fold drawdown cap is meaningless (Apex replay judges survival instead)."""
    rng = random.Random(seed)
    results: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    warmup_iters = max(1, int(n_iterations * WARMUP_FRACTION))
    queue = list(seed_params or [])

    for i in range(n_iterations + len(queue)):
        if queue:
            params = queue.pop(0)
        elif i < warmup_iters or not results or rng.random() > EXPLOIT_PROB:
            params = {p.name: _sample_param(p, rng) for p in strategy.params}
        else:
            parent = _elite_parent(results[:ELITE_POOL_SIZE], rng)["params"]
            params = _mutate_params(parent, strategy.params, rng)

        key = tuple(sorted(params.items()))
        if key in seen:
            continue
        seen.add(key)

        info: dict[str, Any] | None = {} if sink is not None else None
        result = evaluate_candidate(params, folds, strategy, symbol, starting_balance, min_test_trades,
                                    burn_gate=burn_gate, reject_info=info)
        if sink is not None:
            sink(strategy.name, params, result, info)
        if result is not None:
            results.append(result)
            # Eligible-first: the exploit pool and the returned top_n used to be ranked by
            # raw score alone, so on NQ (97.7% of results flagged) flagged candidates filled
            # every slot and the few promotable ones were truncated away.
            results.sort(key=lambda r: (is_eligible(r), r["score"]), reverse=True)

    return results[:top_n]


def random_search(
    df: pd.DataFrame,
    strategy: Strategy,
    symbol: str,
    n_iterations: int = 200,
    train_frac: float = 0.7,
    starting_balance: float = 50_000.0,
    min_test_trades: int = 15,
    top_n: int = 15,
    seed: int | None = None,
    k_folds: int = 3,
    sink=None,
) -> list[dict[str, Any]]:
    folds = build_folds(df, k_folds=k_folds, train_frac=train_frac)
    return _search_given_folds(folds, strategy, symbol, n_iterations, starting_balance, min_test_trades, top_n, seed, sink)


def walk_forward_search(
    df: pd.DataFrame,
    strategy: Strategy,
    symbol: str,
    n_iterations: int = 150,
    starting_balance: float = 50_000.0,
    min_test_trades: int = 50,
    top_n: int = 15,
    seed: int | None = None,
    years: int = 2,
    sink=None,
) -> list[dict[str, Any]]:
    """Same search mechanics as random_search, but split by calendar year
    instead of a fraction of a short window — a much larger, temporally
    separated out-of-sample test than the fast 60-day trainer can offer.
    Produces (years - 1) rolling annual folds when enough history is present."""
    folds = _split_walk_forward_year_folds(df, years)
    if not folds:
        return []
    return _search_given_folds(folds, strategy, symbol, n_iterations, starting_balance, min_test_trades, top_n, seed, sink)


def train_all(
    df: pd.DataFrame,
    symbol: str,
    n_iterations_per_strategy: int = 40,
    train_frac: float = 0.7,
    starting_balance: float = 50_000.0,
    min_test_trades: int = 15,
    top_n_per_strategy: int = 5,
    seed: int | None = None,
    k_folds: int = 3,
    on_progress=None,
    iterations_by_strategy: dict[str, int] | None = None,
    strategy_names: list[str] | None = None,
    sink=None,
) -> list[dict[str, Any]]:
    """Run the optimizer across every registered strategy and merge into one
    cross-strategy leaderboard — the "actual trainer": instead of tuning one
    strategy by hand, it searches every strategy's parameter space and reports
    which strategy+parameter combination actually held up out-of-sample.

    on_progress(index, total, strategy_name), if given, is called right before each
    strategy's search starts — purely for status reporting (e.g. trainer_service's
    "training rsi_reversal (3/10)…"), never affects the search itself.

    iterations_by_strategy, if given, overrides n_iterations_per_strategy on a
    per-strategy basis (trainer_service's adaptive budget allocator — more iterations
    for strategies actively improving, fewer for ones that have stalled). Strategies
    missing from the dict fall back to n_iterations_per_strategy. None preserves the
    original uniform-budget behavior exactly.

    strategy_names, if given, restricts the run to just those strategies instead of
    every registered one (trainer_service routes strategies that never clear this fast
    screen's trade-count gate through a separate long-window search instead — this lets
    it exclude them here rather than wastefully re-running them on data too short for
    them). None runs every registered strategy, as before.
    """
    from app.strategies.registry import list_strategies

    strategies = [s for s in list_strategies() if strategy_names is None or s.name in strategy_names]
    all_results: list[dict[str, Any]] = []
    for i, strat in enumerate(strategies):
        if on_progress:
            on_progress(i, len(strategies), strat.name)
        n_iterations = (iterations_by_strategy or {}).get(strat.name, n_iterations_per_strategy)
        results = random_search(
            df, strat, symbol,
            n_iterations=n_iterations,
            train_frac=train_frac,
            starting_balance=starting_balance,
            min_test_trades=min_test_trades,
            top_n=top_n_per_strategy,
            seed=seed,
            k_folds=k_folds,
            sink=sink,
        )
        for r in results:
            r["strategy"] = strat.name
            r["window"] = "60d"
        all_results.extend(results)

    all_results.sort(key=lambda r: r["score"], reverse=True)
    return all_results


def train_all_long_horizon(
    df: pd.DataFrame,
    symbol: str,
    n_iterations_per_strategy: int = 150,
    starting_balance: float = 50_000.0,
    min_test_trades: int = 50,
    top_n_per_strategy: int = 5,
    seed: int | None = None,
    years: int = 8,
    on_progress=None,
) -> list[dict[str, Any]]:
    """train_all's long-horizon counterpart: walk_forward_search across every
    strategy, merged into one cross-strategy leaderboard."""
    from app.strategies.registry import list_strategies

    strategies = list_strategies()
    all_results: list[dict[str, Any]] = []
    for i, strat in enumerate(strategies):
        if on_progress:
            on_progress(i, len(strategies), strat.name)
        results = walk_forward_search(
            df, strat, symbol,
            n_iterations=n_iterations_per_strategy,
            starting_balance=starting_balance,
            min_test_trades=min_test_trades,
            top_n=top_n_per_strategy,
            seed=seed,
            years=years,
        )
        for r in results:
            r["strategy"] = strat.name
        all_results.extend(results)

    all_results.sort(key=lambda r: r["score"], reverse=True)
    return all_results
