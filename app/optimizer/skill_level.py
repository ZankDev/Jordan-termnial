"""Turns a strategy's persisted training history (app/db.py's strategy_stats table)
into a legible 0-100 "learning percent" and a Noob -> Master skill level.

Pure functions, no I/O — kept explainable on purpose (see the project's own stated
design ethos: "Strategies are plain Python, not black-box models"). Every point on the
0-100 scale traces back to a specific, human-readable reason, not a fitted/opaque model.
"""
from __future__ import annotations

from typing import Any

# Weighted components summing to 100 — see each constant's own comment for why that
# particular ceiling/denominator was chosen.
MATURITY_WEIGHT = 25.0
MATURITY_CYCLES_FOR_FULL_CREDIT = 30  # ~30 combined (NQ+ES) cycles ~= "fully seasoned"

SAMPLE_SIZE_WEIGHT = 20.0
SAMPLE_SIZE_TRADES_FOR_FULL_CREDIT = 300  # cumulative out-of-sample trades for full credit

PEAK_EDGE_WEIGHT = 30.0
# best_score is an edge_t_stat-style mean-minus-std t-statistic (see
# app/backtest/engine.py); a t-stat of ~2.0 is the conventional "statistically
# significant" threshold, so it's used as the ceiling rather than an arbitrary number.
PEAK_EDGE_SCORE_CEILING = 2.0
NEVER_VALID_SENTINEL = -999.0  # matches db.update_strategy_stats' "no candidate" sentinel

FRESHNESS_WEIGHT = 10.0
# Fully decays by this many stale cycles, mirroring trainer_service.STALLED_PATIENCE —
# the same "has this strategy stopped improving" threshold the trainer itself uses to
# deprioritize a strategy's search budget.
FRESHNESS_STALL_CYCLES = 8

CONFIRMED_BONUS = 15.0

LEVELS: list[tuple[float, str]] = [
    (0.0, "Noob"),
    (20.0, "Apprentice"),
    (40.0, "Journeyman"),
    (60.0, "Expert"),
    (80.0, "Master"),
]

LEVEL_DESCRIPTIONS: dict[str, str] = {
    "Noob": "Brand new to the rotation, with little or no training history yet.",
    "Apprentice": "Has some cycles logged and a modest score, but not yet a proven edge.",
    "Journeyman": "A consistent, multi-cycle track record with a statistically visible edge.",
    "Expert": "A strong, still-improving edge backed by a healthy trade sample.",
    "Master": "Everything Expert has, plus it has actually passed deep validation and "
    "been confirmed as a live-eligible champion at least once.",
}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def aggregate_stats_across_symbols(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Combines a strategy's per-symbol strategy_stats rows (NQ, ES, ...) into one
    summary, since the Strategies tab isn't symbol-scoped. Missing/empty input yields
    an all-zero summary (a brand-new strategy with no cycles yet), which is exactly
    what a fresh strategy should show: 0% learned, Noob."""
    rows = [r for r in rows if r]
    if not rows:
        return {
            "total_cycles": 0,
            "total_trades_evaluated": 0,
            "best_score": NEVER_VALID_SENTINEL,
            "cycles_since_improvement": 0,
            "ever_confirmed_champion": False,
            "apex_best_pass_rate": None,
            "apex_last_pass_rate": None,
        }
    return {
        "total_cycles": sum(r.get("total_cycles", 0) for r in rows),
        "total_trades_evaluated": sum(r.get("total_trades_evaluated", 0) for r in rows),
        # a scoring-epoch bump resets best_score; the all-epochs peak keeps skill levels stable
        "best_score": max(
            max(r.get("best_score", NEVER_VALID_SENTINEL), r.get("peak_score_all_epochs") or NEVER_VALID_SENTINEL)
            for r in rows
        ),
        "cycles_since_improvement": min(r.get("cycles_since_improvement", 0) for r in rows),
        "ever_confirmed_champion": any(bool(r.get("ever_confirmed_champion")) for r in rows),
        "apex_best_pass_rate": max((r.get("apex_best_pass_rate") for r in rows if r.get("apex_best_pass_rate") is not None), default=None),
        "apex_last_pass_rate": max((r.get("apex_last_pass_rate") for r in rows if r.get("apex_last_pass_rate") is not None), default=None),
    }


def compute_learning_percent(
    total_cycles: int,
    total_trades_evaluated: int,
    best_score: float,
    cycles_since_improvement: int,
    ever_confirmed_champion: bool,
) -> float:
    maturity_pts = MATURITY_WEIGHT * min(1.0, total_cycles / MATURITY_CYCLES_FOR_FULL_CREDIT)
    sample_pts = SAMPLE_SIZE_WEIGHT * min(1.0, total_trades_evaluated / SAMPLE_SIZE_TRADES_FOR_FULL_CREDIT)
    peak_edge_pts = PEAK_EDGE_WEIGHT * _clamp(best_score / PEAK_EDGE_SCORE_CEILING) if best_score > 0 else 0.0
    freshness_pts = FRESHNESS_WEIGHT * max(0.0, 1.0 - cycles_since_improvement / FRESHNESS_STALL_CYCLES)
    confirmed_pts = CONFIRMED_BONUS if ever_confirmed_champion else 0.0
    total = maturity_pts + sample_pts + peak_edge_pts + freshness_pts + confirmed_pts
    return round(_clamp(total, 0.0, 100.0), 1)


def compute_skill_level(learning_percent: float, ever_confirmed_champion: bool) -> str:
    """Noob -> Master, in order. Capped at "Expert" if the raw score alone would reach
    Master (80+) but the strategy has never actually passed deep validation — the top
    tier stays tied to a real event in the codebase (a confirmed champion), not a
    fabricated proxy from backtest numbers alone."""
    level = LEVELS[0][1]
    for threshold, name in LEVELS:
        if learning_percent >= threshold:
            level = name
    if level == "Master" and not ever_confirmed_champion:
        level = "Expert"
    return level


def strategy_skill_summary(rows_by_symbol: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """rows_by_symbol: {"NQ": strategy_stats_row_or_None, "ES": ...}. Returns the full
    per-strategy skill payload the /api/strategies route attaches under "skill"."""
    agg = aggregate_stats_across_symbols([r for r in rows_by_symbol.values() if r])
    learning_percent = compute_learning_percent(
        agg["total_cycles"],
        agg["total_trades_evaluated"],
        agg["best_score"],
        agg["cycles_since_improvement"],
        agg["ever_confirmed_champion"],
    )
    level = compute_skill_level(learning_percent, agg["ever_confirmed_champion"])
    return {
        "learning_percent": learning_percent,
        "level": level,
        "level_description": LEVEL_DESCRIPTIONS[level],
        "total_cycles": agg["total_cycles"],
        "total_trades_evaluated": agg["total_trades_evaluated"],
        "best_score": None if agg["best_score"] <= NEVER_VALID_SENTINEL else round(agg["best_score"], 3),
        "cycles_since_improvement": agg["cycles_since_improvement"],
        "ever_confirmed_champion": agg["ever_confirmed_champion"],
        # Apex evaluation pass rate ("passes NOSTRO"): share of replayed start dates that hit the
        # profit target before breaching the trailing drawdown - see app/accounts/apex_sim.py
        "apex": {
            "best_pass_rate": agg.get("apex_best_pass_rate"),
            "last_pass_rate": agg.get("apex_last_pass_rate"),
        },
    }
