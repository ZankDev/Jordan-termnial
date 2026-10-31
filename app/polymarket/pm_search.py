"""Parameter search for Polymarket strategies — the session-based counterpart of
app/optimizer/search.py: the same explore/exploit mechanics (random warm-up, then a
rank-weighted mutation of an elite pool), the same gate names (diverged, too_smooth,
exceeds_burn_limit), run against walk-forward folds of resolved sessions.

What is different from a per-fold t-stat search, and why:
  * a candidate is scored on its POOLED out-of-sample trades (all test folds
    together), so a strategy that only fires a handful of times per fold is judged
    on all of its trades at once instead of on three tiny noisy samples;
  * a candidate needs a minimum number of pooled OOS trades, and to be profitable
    in most folds — one lucky fold cannot carry it;
  * seeds: elites from earlier cycles are re-scored first each cycle, so training
    accumulates instead of restarting from random every time.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any, Callable

from app.optimizer.search import (
    ELITE_POOL_SIZE, EXPLOIT_PROB, MUTATION_MAX_STEPS, REROLL_PROB, WARMUP_FRACTION,
    _elite_parent, _sample_param, params_hash,
)
from app.polymarket import backtest
from app.polymarket.momo_strategy import repair_params
from app.polymarket.registry import get as get_strategy
from app.strategies.base import ParamSpec

TRAIN_CAP = 2500           # newest training sessions replayed for the in-sample (divergence) check
MIN_POOLED_TRADES = 30      # pooled out-of-sample trades a candidate needs to be scored at all
MIN_PROFITABLE_FOLDS = 2    # ...and it must make money in at least this many folds (of 3)
BURN_FRACTION = 0.10        # pooled OOS max drawdown above this share of the account = flagged
FOLD_PENALTY = 0.5          # score = pooled t-stat - FOLD_PENALTY * std(fold t-stats)


def is_eligible(r: dict[str, Any]) -> bool:
    return not (r.get("diverged") or r.get("too_smooth") or r.get("exceeds_burn_limit"))


def _mutate_params(parent: dict[str, Any], specs: list[ParamSpec], rng: random.Random) -> dict[str, Any]:
    mutated: dict[str, Any] = {}
    for spec in specs:
        if rng.random() < REROLL_PROB:
            mutated[spec.name] = _sample_param(spec, rng)
            continue
        steps = max(1, round((spec.max - spec.min) / spec.step))
        current_idx = round((parent.get(spec.name, spec.default) - spec.min) / spec.step)
        jump = rng.randint(1, MUTATION_MAX_STEPS) * rng.choice([-1, 1])
        new_idx = min(max(current_idx + jump, 0), steps)
        mutated[spec.name] = spec.cast(spec.min + new_idx * spec.step)
    return mutated


def _pool_metrics(pnls: list[float], starting_balance: float) -> dict[str, Any]:
    """Metrics over an ordered list of per-trade P&L (pooled test folds, in time order)."""
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    equity = peak = starting_balance
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    net = sum(pnls)
    t = 0.0
    if n > 1:
        mean = net / n
        std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (n - 1))
        t = mean / std * math.sqrt(n) if std > 0 else 0.0
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "total_trades": n,
        "win_rate_pct": round(len(wins) / n * 100, 2) if n else 0.0,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "net_pnl": round(net, 4),
        "avg_pnl": round(net / n, 4) if n else 0.0,
        "return_pct": round(net / starting_balance * 100, 2) if starting_balance else 0.0,
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd / peak * 100, 2) if peak else 0.0,
        "edge_t_stat": round(t, 3),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "reward_risk_ratio": round(avg_win / abs(avg_loss), 3) if avg_loss else None,
    }


def evaluate_pm_candidate(
    params: dict[str, Any],
    folds: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    strategy_name: str = "momo",
    starting_balance: float = 1_000.0,
    min_pooled_trades: int = MIN_POOLED_TRADES,
    stake_usd: float = backtest.STAKE_USD,
    reject_info: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Score one param set across walk-forward folds. None if it doesn't clear the
    pooled-trade / profitable-folds bars (reject_info then carries what it did do,
    so a rejected candidate is still a point in The Field)."""
    # Test folds first: most random parameter sets never fire enough to matter, and
    # rejecting them on the (small) test blocks avoids the far larger train replay.
    pooled: list[float] = []
    fold_t: list[float] = []
    fold_net: list[float] = []
    fold_n: list[int] = []
    for _train, test in folds:
        res = backtest.run_pm_backtest(strategy_name, params, test, starting_balance, stake_usd)
        pooled.extend(t["pnl"] for t in res.trades)
        fold_t.append(res.metrics["edge_t_stat"])
        fold_net.append(res.metrics["net_pnl"])
        fold_n.append(res.metrics["total_trades"])

    test_metrics = _pool_metrics(pooled, starting_balance)
    if len(pooled) < min_pooled_trades:
        if reject_info is not None:
            reject_info["reason"] = "test_trades"
            reject_info["test_metrics"] = test_metrics
        return None

    # In-sample check for the "diverged" gate (a losing train fit that spikes out of sample is a
    # noise fit): a bounded tail of the training window is enough and keeps this cheap.
    train_res = backtest.run_pm_backtest(strategy_name, params, folds[-1][0][-TRAIN_CAP:], starting_balance, stake_usd)
    if train_res.metrics["total_trades"] == 0:
        if reject_info is not None:
            reject_info["reason"] = "no_train_trades"
            reject_info["test_metrics"] = test_metrics
        return None

    train_pf, test_pf = train_res.metrics["profit_factor"], test_metrics["profit_factor"]
    diverged = bool((train_pf is not None and train_pf < 1.0) and (test_pf is None or test_pf > 3.0))
    too_smooth = bool(test_metrics["total_trades"] >= 30 and test_metrics["return_pct"] > 0
                      and test_metrics["max_drawdown_pct"] < 0.15 * test_metrics["return_pct"])
    exceeds_burn = test_metrics["max_drawdown"] > BURN_FRACTION * starting_balance

    mean_t = sum(fold_t) / len(fold_t)
    std_t = math.sqrt(sum((x - mean_t) ** 2 for x in fold_t) / (len(fold_t) - 1)) if len(fold_t) > 1 else 0.0
    profitable_folds = sum(1 for x in fold_net if x > 0)
    score = test_metrics["edge_t_stat"] - FOLD_PENALTY * std_t
    if profitable_folds < min(MIN_PROFITABLE_FOLDS, len(folds)):
        score = min(score, 0.0) - 0.01

    return {
        "params": params,
        "train_metrics": train_res.metrics,
        "test_metrics": test_metrics,
        "fold_scores": fold_t,
        "fold_net": fold_net,
        "fold_trades": fold_n,
        "profitable_folds": profitable_folds,
        "score": score,
        "diverged": diverged,
        "too_smooth": too_smooth,
        "exceeds_burn_limit": exceeds_burn,
    }


class PmFieldCollector:
    """Sink for pm_random_search — the Field-page data source. Stores every scored
    or rejected candidate (non-survivors sampled 1-in-4 by params hash, survivors
    always) plus exact per-cycle counts, so headline numbers stay honest while the
    stored scatter stays small. exp_net is average $ P&L per trade (each trade risks
    the same flat stake, so it is comparable candidate to candidate); cost_r is 0
    because slippage is folded into the fills themselves."""

    SAMPLE_MODULUS = 4

    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self.counts = {"total": 0, "t0": 0, "t1": 0, "t2": 0}
        self._ts = int(time.time())

    def add(self, strategy: str, params: dict[str, Any], result: dict[str, Any] | None, reject: dict[str, Any] | None) -> None:
        import json

        if result is not None:
            tm = result["test_metrics"]
            survivor = is_eligible(result) and result["score"] > 0
            tier = 2 if survivor else 1
            flags = (1 if result.get("diverged") else 0) | (2 if result.get("too_smooth") else 0) | (4 if result.get("exceeds_burn_limit") else 0)
            score = result["score"]
        else:
            tm = (reject or {}).get("test_metrics")
            tier, flags, score = 0, 0, None
        if not tm or not tm.get("total_trades"):
            return
        n = tm["total_trades"]
        c = self.counts
        c["total"] += 1
        c[f"t{tier}"] += 1
        if tier < 2 and int(params_hash(params), 16) % self.SAMPLE_MODULUS != 0:
            return
        self.rows.append((
            self._ts, None, strategy, tier, flags, n, tm.get("win_rate_pct"), tm.get("reward_risk_ratio"),
            tm["net_pnl"] / n, 0.0, score, 1 if tier == 2 else self.SAMPLE_MODULUS,
            json.dumps(params) if tier == 2 else None,
        ))

    def flush(self, run_id: int | None) -> None:
        if not self.counts["total"]:
            return
        from app import db

        rows = [(r[0], run_id, *r[2:]) for r in self.rows]
        with db.get_conn() as conn:
            if rows:
                conn.executemany(
                    "INSERT INTO pm_candidate_log (ts, run_id, strategy, tier, flags, n, wr_net, rr_net, exp_net, "
                    "cost_r, score, w, params_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows,
                )
            conn.execute(
                "INSERT INTO pm_field_cycle_stats (ts, run_id, n_total, n_tier0, n_tier1, n_survivors) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self._ts, run_id, self.counts["total"], self.counts["t0"], self.counts["t1"], self.counts["t2"]),
            )
            # retention: non-survivors 2 days, survivors + cycle stats 60 days
            conn.execute("DELETE FROM pm_candidate_log WHERE tier < 2 AND ts < strftime('%s','now') - 172800")
            conn.execute("DELETE FROM pm_candidate_log WHERE ts < strftime('%s','now') - 5184000")
            conn.execute("DELETE FROM pm_field_cycle_stats WHERE ts < strftime('%s','now') - 5184000")


def pm_random_search(
    folds: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    strategy_name: str = "momo",
    n_iterations: int = 300,
    starting_balance: float = 1_000.0,
    min_pooled_trades: int = MIN_POOLED_TRADES,
    top_n: int = 25,
    seed: int | None = None,
    sink: Callable | None = None,
    seed_params: list[dict[str, Any]] | None = None,
    max_seconds: float | None = None,
    on_progress: Callable[[int, int, float | None], None] | None = None,
    duty_rest: float = 0.25,
) -> list[dict[str, Any]]:
    """seed_params (elites from earlier cycles + the strategy defaults) are scored
    first, on top of n_iterations. max_seconds bounds a cycle in wall-clock time so
    it stays predictable however large the corpus grows."""
    specs = get_strategy(strategy_name)["params"]
    rng = random.Random(seed)
    results: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    queue = list(seed_params or [])
    warmup_iters = max(1, int(n_iterations * WARMUP_FRACTION))
    started = time.time()
    total = n_iterations + len(queue)
    best: float | None = None

    for i in range(total):
        if max_seconds is not None and time.time() - started > max_seconds:
            break
        from_seed = bool(queue)
        if queue:
            params = queue.pop(0)
        elif i < warmup_iters or not results or rng.random() > EXPLOIT_PROB:
            params = {p.name: _sample_param(p, rng) for p in specs}
        else:
            parent = _elite_parent(results[:ELITE_POOL_SIZE], rng)["params"]
            params = _mutate_params(parent, specs, rng)
        if not from_seed:
            params = repair_params(params)

        key = tuple(sorted(params.items()))
        if key in seen:
            continue
        seen.add(key)

        info: dict[str, Any] | None = {} if sink is not None else None
        t_eval = time.time()
        result = evaluate_pm_candidate(params, folds, strategy_name, starting_balance, min_pooled_trades, reject_info=info)
        eval_secs = time.time() - t_eval
        if sink is not None:
            sink(strategy_name, params, result, info)
        if result is not None:
            results.append(result)
            results.sort(key=lambda r: (is_eligible(r), r["score"]), reverse=True)
            if is_eligible(result) and (best is None or result["score"] > best):
                best = result["score"]
        if on_progress and (i % 5 == 0 or i == total - 1):
            on_progress(i + 1, total, best)
        # Duty-cycle: this pure-Python search runs in a worker thread beside the API and the
        # live engines (one GIL); resting a quarter of each evaluation keeps them responsive.
        time.sleep(min(0.4, eval_secs * duty_rest))

    return results[:top_n]
