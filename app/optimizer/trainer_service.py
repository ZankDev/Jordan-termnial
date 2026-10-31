"""The always-on trainer: runs the cross-strategy search continuously in the
background (alternating NQ/ES), keeps a persistent "champion" (best result
ever seen per symbol, out-of-sample), and exposes status/history for the UI.

This is what makes the Trainer tab feel alive without the user doing anything:
training happens server-side on a timer regardless of whether anyone has the
page open, so opening the tab just shows whatever the system has already
found instead of waiting on a fresh search every time.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
import os
import json
from typing import Any
from zoneinfo import ZoneInfo

import datetime as dt

from app import db
from app.data import dukascopy_source, yfinance_source
from app.optimizer import deep_search, live_feedback
from app.optimizer.field_metrics import CandidateCollector
from app.optimizer.promotion import run_promotion
from app.optimizer.search import train_all, walk_forward_search
from app.strategies import registry

TRAIN_INTERVAL_SECONDS = 0  # how long to wait AFTER a cycle finishes before starting the next
# Back-to-back, no cooldown: the moment one cycle finishes, the next one starts (still
# serialized by _lock in run_now()/scheduler_loop(), so cycles never overlap). Explicitly
# requested despite the known tradeoff: a 2-min interval (not even zero) previously made
# this machine's low-memory watchdog kill the server almost immediately after every
# restart, vs. ~30 min of healthy uptime at 5 min — more time spent actively training
# means more time spent doing memory-heavy backtest work instead of idling, so expect
# more frequent restarts at 0 than at either of those.
SYMBOL_CYCLE = ["NQ", "ES"]
ALL_STRATEGIES_TAG = "ALL_STRATEGIES"
NY_TZ = ZoneInfo("America/New_York")  # matches ledger.dashboard_summary()'s "today" convention
# Bumped from 30 -> 80: a wider parameter search per strategy per cycle (~800
# backtests total across all 10 strategies, still comfortably under the
# 10-minute cycle interval) so the trainer has a much larger sample of each
# strategy's parameter space to find genuinely profitable, not just lucky,
# combinations before crowning a champion.
N_ITERATIONS_PER_STRATEGY = 80

# Adaptive per-strategy budget: every strategy used to get the exact same fixed 80
# iterations every cycle forever, regardless of whether it's ever shown promise or has
# been failing for 50 cycles straight. This reallocates the SAME total budget
# (base_iterations x n_strategies, so cycle time doesn't change) toward strategies that
# are actively improving and away from ones that have stalled — never to zero, so a
# stalled strategy still gets rediscovered if market conditions later make it viable.
STRATEGY_WARMUP_CYCLES = 3  # need this many cycles of real history before adapting at all
PROMISING_SCORE = 0.5  # best_score at/above this + still improving -> boosted budget
STALLED_PATIENCE = 8  # cycles with zero improvement before a weak strategy is deprioritized
STALLED_SCORE = 0.3  # best_score below this + stalled -> reduced budget
BOOST_WEIGHT = 1.6
BASELINE_WEIGHT = 1.0
REDUCE_WEIGHT = 0.5
MIN_ITERATIONS = 20  # floor so a deprioritized strategy is never fully starved

# Which strategies the trainer searches, per symbol (a symbol not listed trains everything). Chosen 2026-09-26 from the
# forward (live-account) results plus backtest peaks: dropped = forward profit factor < 0.6 over >= 8 trades, or a weak
# backtest peak with no forward evidence. Existing live accounts and champions are untouched; restore a strategy by
# adding its name back here.
TRAIN_ROSTER: dict[str, set[str]] = {
    # 2026-09-28 (evening): the 60-day screen now only searches the families that make money over YEARS
    # (portfolio.analyse_long over 3-6 years: orb_retest NQ +$51k 6/6 years, ny_open_breakout NQ +$44k 6/6, orb_retest ES
    # 4/4; vwap/rsi/macd/ema/fvg/donchian/inside_bar/prev_day lose 0/6 years). The long-history deep search still explores
    # ALL strategies every cycle, so a family that finds a working setting over years can come back on its own.
    "NQ": {"orb_retest", "ny_open_breakout", "orb_fade"},
    "ES": {"orb_retest", "ny_open_breakout", "orb_fade"},
}


def _allocate_iterations(symbol: str, strategy_names: list[str], base_iterations: int) -> dict[str, dict[str, Any]]:
    """Returns {strategy: {"iterations": int, "label": "cold_start"|"promising"|"stalled"|"baseline"}}."""
    stats = db.get_strategy_stats(symbol)
    total_budget = base_iterations * len(strategy_names)

    weights: dict[str, float] = {}
    labels: dict[str, str] = {}
    for name in strategy_names:
        s = stats.get(name)
        if not s or s["total_cycles"] < STRATEGY_WARMUP_CYCLES:
            weights[name], labels[name] = BASELINE_WEIGHT, "cold_start"
        elif s["best_score"] >= PROMISING_SCORE and s["cycles_since_improvement"] <= 3:
            weights[name], labels[name] = BOOST_WEIGHT, "promising"
        elif s["cycles_since_improvement"] >= STALLED_PATIENCE and s["best_score"] < STALLED_SCORE:
            weights[name], labels[name] = REDUCE_WEIGHT, "stalled"
        else:
            weights[name], labels[name] = BASELINE_WEIGHT, "baseline"

    total_weight = sum(weights.values())
    return {
        name: {
            "iterations": max(MIN_ITERATIONS, round(total_budget * weights[name] / total_weight)),
            "label": labels[name],
        }
        for name in strategy_names
    }


_lock = asyncio.Lock()
_state: dict[str, Any] = {
    "cycle_index": 0,
    "next_run_at": time.time() + 2,  # first run fires ~2s after startup
    "last_error": None,
    "last_run_symbol": None,
    "stage": "idle",  # idle | fetching_data | training | deep_validating | saving
    "stage_symbol": None,
    "stage_detail": None,  # {"strategy": str, "index": int, "total": int} while stage == "training"
}


def _set_stage(stage: str, symbol: str | None = None, detail: dict[str, Any] | None = None) -> None:
    _state["stage"] = stage
    _state["stage_symbol"] = symbol
    _state["stage_detail"] = detail


DEEP_VALIDATION_YEARS = 8  # matches the long-horizon trainer's own "as much data as possible" default
# Must match the fast trainer's own interval ("5m", see _run_training_sync below) — a
# candidate's params were tuned against 5m bar signal frequency, so validating at a
# coarser interval (e.g. 15m) changes how often the strategy even trades and can fail
# the trade-count gate for reasons that have nothing to do with the strategy's real
# robustness. Slower to fetch/backtest across 8 years than 15m would be, but this only
# runs when a candidate has already beaten the current champion, not every cycle.
DEEP_VALIDATION_INTERVAL = "5m"

# Strategies that consistently produce zero valid candidates on the fast 60-day screen
# (session-based, low-frequency ones especially — see _needs_long_window) get routed
# through a 2-year Dukascopy walk-forward search instead of being auto-rejected for lack
# of data every cycle forever. min_test_trades is lower here than walk_forward_search's
# own default (50, tuned for the 8-year/multi-fold deep-validation stage) — 25 trades
# across a full year of test data is still a meaningful bar for a genuinely selective,
# once-a-day-style strategy, not a lowered quality bar: every other evaluate_candidate
# gate (diverged/too_smooth/exceeds_burn_limit/regime_inconsistent) still applies in full.
LONG_WINDOW_YEARS = 2
LONG_WINDOW_INTERVAL = "5m"
LONG_WINDOW_MIN_TEST_TRADES = 25
LOW_FREQUENCY = {"orb_retest", "ny_open_breakout", "orb_fade"}  # about one trade per session: cannot be judged on 60 days
NEVER_VALID_SENTINEL_CUTOFF = -900  # matches db.update_strategy_stats' -999 "no candidate" sentinel


def _needs_long_window(stats_row: dict[str, Any] | None, name: str | None = None) -> bool:
    """True if this strategy has had a fair number of fast-screen attempts and has
    NEVER ONCE produced a single valid candidate — a sign the 60-day window is too short
    to judge it, not that it's actually bad. The once-a-day opening-range families are always routed here: since the
    2026-09-28 roster change they were the ONLY strategies on the 60-day screen, and at ~1 trade a day they made 4-7 trades
    per test fold against a bar of 15, so every candidate was rejected (0 survivors for 4 days) even though a stats row with
    an old positive best_score kept them off this path."""
    if name in LOW_FREQUENCY:
        return True
    return (
        stats_row is not None
        and stats_row["total_cycles"] >= STRATEGY_WARMUP_CYCLES
        and stats_row["best_score"] <= NEVER_VALID_SENTINEL_CUTOFF
    )


def _run_long_window_strategies(
    symbol: str, names: list[str], allocation: dict[str, dict[str, Any]], total: int, start_index: int, sink=None
) -> list[dict[str, Any]]:
    """Runs strategies that have never once cleared the fast 60-day screen's trade-count
    gate against LONG_WINDOW_YEARS of Dukascopy data instead — see _needs_long_window."""
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * LONG_WINDOW_YEARS + 5)
    df = dukascopy_source.fetch_bars(symbol, start, end, LONG_WINDOW_INTERVAL, max_repair_months=0)
    if df.empty:
        return []

    results: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        a = allocation.get(name, {})
        _set_stage("training", symbol, {
            "strategy": name, "index": start_index + i + 1, "total": total,
            "iterations": a.get("iterations"), "label": a.get("label", "baseline"), "window": "2yr",
        })
        strat = registry.get(name)
        strat_results = walk_forward_search(
            df, strat, symbol,
            n_iterations=a.get("iterations", N_ITERATIONS_PER_STRATEGY),
            min_test_trades=LONG_WINDOW_MIN_TEST_TRADES,
            years=LONG_WINDOW_YEARS,
            sink=sink,
        )
        for r in strat_results:
            r["strategy"] = name
            r["window"] = "2yr"
        results.extend(strat_results)
    return results


def _maintenance() -> None:
    """Every 10th cycle: age-based slimming of optimizer_runs (the ~37 MB/day growth) and a
    cap on the promotion log. Cheap, and never allowed to fail a cycle."""
    if _state["cycle_index"] % 10 != 0:
        return
    try:
        db.slim_optimizer_runs()
        db.trim_attempts()
        db.prune_field()
    except Exception:  # noqa: BLE001
        pass


def _run_training_sync(symbol: str, n_iterations_per_strategy: int) -> dict[str, Any]:
    _set_stage("fetching_data", symbol)
    df = yfinance_source.fetch(symbol, "5m")

    all_names = [s.name for s in registry.list_strategies()]
    roster = TRAIN_ROSTER.get(symbol)
    strategy_names = [n for n in all_names if roster is None or n in roster]
    # The fast 60-day screen keeps its normal per-strategy budget: the CPU a smaller roster frees goes to the long-history
    # deep search (deep_search.py), which is where parameters are now chosen.
    stats = db.get_strategy_stats(symbol)
    long_window_names = [n for n in strategy_names if _needs_long_window(stats.get(n), n)]
    fast_names = [n for n in strategy_names if n not in long_window_names]

    allocation = _allocate_iterations(symbol, strategy_names, n_iterations_per_strategy)
    iterations_by_strategy = {name: a["iterations"] for name, a in allocation.items()}

    def _on_progress(index: int, total: int, strategy_name: str) -> None:
        a = allocation.get(strategy_name, {})
        _set_stage("training", symbol, {
            "strategy": strategy_name, "index": index + 1, "total": len(strategy_names),
            "iterations": a.get("iterations", n_iterations_per_strategy), "label": a.get("label", "baseline"),
        })

    collector = CandidateCollector(symbol)  # sees EVERY evaluated candidate, for The Field page
    results = train_all(
        df, symbol, n_iterations_per_strategy=n_iterations_per_strategy,
        on_progress=_on_progress, iterations_by_strategy=iterations_by_strategy,
        strategy_names=fast_names, sink=collector.add,
    )
    if long_window_names:
        results += _run_long_window_strategies(
            symbol, long_window_names, allocation, len(strategy_names), len(fast_names), sink=collector.add
        )
        # train_all's own internal sort no longer covers the whole list once the
        # long-window results are appended after it — re-sort so results[0]/the
        # champion-eligibility scan below still see the actual best-to-worst order.
        results.sort(key=lambda r: r["score"], reverse=True)
    winner = results[0] if results else None

    _set_stage("saving", symbol)
    run_id = db.save_optimizer_run(
        symbol,
        ALL_STRATEGIES_TAG,
        "5m",
        sum(iterations_by_strategy.values()),
        results,
        top_strategy=winner["strategy"] if winner else None,
        top_score=winner["score"] if winner else None,
    )

    # Feed each strategy's best result this cycle (None if it produced no valid
    # candidate at all) back into strategy_stats, so next cycle's allocation reflects
    # what actually just happened.
    # best_trades_by_strategy tracks the WINNING candidate's own out-of-sample trade
    # count (summed across its folds) alongside its score — see
    # app/optimizer/skill_level.py for why this is the winner's trades only, not every
    # candidate the search tried.
    best_by_strategy: dict[str, float] = {}
    best_trades_by_strategy: dict[str, int] = {}
    for r in results:
        s = r["strategy"]
        if s not in best_by_strategy or r["score"] > best_by_strategy[s]:
            best_by_strategy[s] = r["score"]
            best_trades_by_strategy[s] = sum(
                fm["test_metrics"]["total_trades"] for fm in r.get("fold_metrics", [])
            )
    for name in strategy_names:
        db.update_strategy_stats(symbol, name, best_by_strategy.get(name), best_trades_by_strategy.get(name, 0))

    # Promotion: leader/confirmed tiers, same-scale re-scoring of the incumbent, top-K
    # candidates, and a logged reason for every decision (see app/optimizer/promotion.py).
    try:
        collector.flush(run_id)
    except Exception:  # noqa: BLE001 - the Field log is telemetry, never worth failing a cycle
        pass
    extra: list[dict[str, Any]] = []
    try:
        _set_stage("deep_search", symbol)
        extra = deep_search.run(symbol, run_id, results, df=df)
    except Exception:  # noqa: BLE001 - deep search is an addition; the normal cycle must still finish
        extra = []
    _set_stage("promoting", symbol)
    promo = run_promotion(symbol, run_id, results, df, extra_confirmed=extra)
    champion_updated = promo["champion_updated"]
    deep_validation = promo["deep_validation"]

    # Live-vs-backtest divergence: cheap (reads already-recorded trades, no backtests), so it
    # runs every cycle rather than being rate-limited like deep validation - see live_feedback.py.
    _set_stage("live_feedback", symbol)
    try:
        live_check = live_feedback.run((symbol,))
        if any(v.get("demoted") for v in live_check.values()):
            champion_updated = True
    except Exception:  # noqa: BLE001 - live feedback must never break a training cycle
        live_check = {}

    _maintenance()

    return {
        "run_id": run_id,
        "symbol": symbol,
        "results": results,
        "champion_updated": champion_updated,
        "deep_validation": deep_validation,
        "live_feedback": live_check,
    }


async def run_now(symbol: str, n_iterations_per_strategy: int = N_ITERATIONS_PER_STRATEGY) -> dict[str, Any]:
    if _lock.locked():
        raise RuntimeError("A training run is already in progress — try again shortly.")
    async with _lock:
        try:
            return await asyncio.to_thread(_run_training_sync, symbol, n_iterations_per_strategy)
        finally:
            _set_stage("idle")


NQ_EXPLORE_EVERY = 4  # while NQ has no strategy that clears the long-history gate, it gets one cycle in this many


def next_symbol(cycle_index: int) -> str:
    """Alternate NQ / ES, except that NQ is only explored (1 cycle in NQ_EXPLORE_EVERY) while none of its strategies has a
    long record that clears the gate: over 96 months it produced nothing, and it was getting half the compute."""
    try:
        from app.optimizer import long_gate

        if long_gate.enabled() and not long_gate.symbol_has_edge("NQ"):
            return "NQ" if cycle_index % NQ_EXPLORE_EVERY == 0 else "ES"
    except Exception:  # noqa: BLE001 - a scheduling aid must never stop the trainer
        pass
    return SYMBOL_CYCLE[cycle_index % len(SYMBOL_CYCLE)]


async def scheduler_loop() -> None:
    while True:
        wait = max(0.0, _state["next_run_at"] - time.time())
        await asyncio.sleep(wait)

        symbol = next_symbol(_state["cycle_index"])
        _state["cycle_index"] += 1
        try:
            await run_now(symbol)
            _state["last_error"] = None
            _state["last_run_symbol"] = symbol
        except Exception as e:  # keep the loop alive across any single bad cycle
            _state["last_error"] = str(e)
        _state["next_run_at"] = time.time() + TRAIN_INTERVAL_SECONDS


def _worker_state() -> dict[str, Any] | None:
    """When training runs in the background worker process (app/worker.py), its live state comes from the file it mirrors."""
    if os.environ.get("NOSTRO_TRAINER_IN_WORKER") != "1":
        return None
    try:
        st = json.loads((Path(__file__).resolve().parents[2] / "data_cache" / "worker_state.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"training_now": False, "stage": "worker starting", "stage_symbol": None, "stage_detail": None,
                "next_run_at": None, "last_error": None, "worker_alive": False}
    st["worker_alive"] = time.time() - float(st.get("heartbeat") or 0) < 15
    if not st["worker_alive"]:
        st.update(training_now=False, stage="worker not responding")
    return st


def get_status() -> dict[str, Any]:
    last_run = db.list_optimizer_runs(limit=1, strategy=ALL_STRATEGIES_TAG)
    st = _worker_state() or {**_state, "training_now": _lock.locked()}
    return {
        "training_now": bool(st.get("training_now")),
        "interval_seconds": TRAIN_INTERVAL_SECONDS,
        "next_run_at": st.get("next_run_at"),
        "total_runs": db.count_optimizer_runs(strategy=ALL_STRATEGIES_TAG),
        "last_run": last_run[0] if last_run else None,
        "last_error": st.get("last_error"),
        "symbol_cycle": SYMBOL_CYCLE,
        "stage": st.get("stage"),
        "stage_symbol": st.get("stage_symbol"),
        "stage_detail": st.get("stage_detail"),
        "in_worker": os.environ.get("NOSTRO_TRAINER_IN_WORKER") == "1",
        "worker_alive": st.get("worker_alive"),
    }


def get_training_today_stats() -> dict[str, Any]:
    """How much training actually happened today (NY calendar day, matching
    ledger.dashboard_summary()'s "today" everywhere else in the UI) — one
    optimizer_runs row per completed cycle, n_iterations backtests each."""
    today = dt.datetime.now(NY_TZ).date()
    runs_today = 0
    backtests_today = 0
    for run in db.list_optimizer_runs(limit=200, strategy=ALL_STRATEGIES_TAG):
        try:
            created = dt.datetime.strptime(run["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if created.astimezone(NY_TZ).date() != today:
            continue
        runs_today += 1
        backtests_today += run["n_iterations"] or 0
    return {"runs_today": runs_today, "backtests_today": backtests_today}
