"""Live-vs-backtest divergence: does a champion's ACTUAL live trading agree with what its
backtest, deep validation and stress battery predicted?

Every other check in this pipeline re-scores a strategy against historical bars - the fast
re-score, the confirmed re-score, the stress battery's Monte Carlo, all of it. None of them can
see real fills, real slippage, or a regime shift that only shows up after the strategy actually
started trading. This is the one blind spot a pure backtest-driven pipeline cannot see on its
own, so it closes the loop from the other end: it reads the CLOSED TRADES the live/paper accounts
actually produced while following a slot, and asks whether that record still looks like an edge.

Deliberately conservative: it only ever demotes (moves a slot to champion_history, exactly what
an ordinary failed re-validation already does - see promotion.py's LEADER_FAIL_GRACE /
DEMOTE_AFTER_FAILS), never blocks a promotion and never touches an open position. It uses the
same day-clustered t-stat as search.py's clustering_inflated gate (not a per-trade one - the
whole point is not to fire on ordinary trade-to-trade noise), and requires several checks in a
row to agree before acting, matching the anti-churn philosophy already used for the leader and
confirmed tiers.
"""
from __future__ import annotations

import json
from typing import Any

from app import db
from app.optimizer.search import _day_edge_t_stat

MIN_LIVE_TRADES = 20  # fewer than this and live evidence says nothing yet either way
MIN_LIVE_DAYS = 8  # matches search.py's CLUSTER_MIN_DAYS: need real day-to-day spread, not one hot streak
DIVERGENCE_T_STAT = -1.0  # a live day-clustered t-stat this bad is a real problem, not noise
CONSECUTIVE_CHECKS_TO_DEMOTE = 3  # persistent across several cycles, not one bad stretch


def _resolved_hash(strategy: str, params: dict[str, Any] | None) -> str | None:
    from app.optimizer.search import params_hash
    from app.strategies import registry

    try:
        return params_hash({**registry.get(strategy).default_params(), **(params or {})})
    except KeyError:
        return None


def _live_trades_for(symbol: str, strategy: str, target_hash: str | None) -> list[dict[str, Any]]:
    """Every closed trade from every account (live or archived - a strategy that got demoted
    still counts as evidence) currently, or for the AUTO account most recently, assigned exactly
    this strategy+params on this symbol. The AUTO account reassigns itself in place while flat
    (see auto_account.py), so only trades since its last swap into this exact params set count."""
    out: list[dict[str, Any]] = []
    if not target_hash:
        return out
    for acct in db.list_accounts(include_archived=True):
        if acct.get("symbol") != symbol or acct.get("strategy") != strategy:
            continue
        try:
            # One account with a malformed/legacy strategy_params_json (or any other bad field)
            # must never take the whole check down with it - skip just that account.
            params = json.loads(acct["strategy_params_json"]) if acct.get("strategy_params_json") else None
            if _resolved_hash(strategy, params) != target_hash:
                continue
            since = db.last_auto_swap_ts(acct["id"]) if acct.get("is_system") else None
            for t in db.get_account_trades(acct["id"]):
                if since and t["exit_time"] < since:
                    continue
                out.append(t)
        except Exception:  # noqa: BLE001
            continue
    return out


def check(symbol: str, tier: str) -> dict[str, Any] | None:
    """One check for one slot. Always returns a summary (None only when there is no slot to
    check) and always writes a promotion_attempts row - same "every decision is answerable"
    convention promotion.py itself uses (see its module docstring), so this feature is visible
    and debuggable in the Trainer's decision log from the moment it is deployed, not only once
    it first demotes something."""
    slot = db.get_slot(symbol, tier)
    if not slot:
        return None
    h = _resolved_hash(slot["strategy"], slot["params"])
    trades = _live_trades_for(symbol, slot["strategy"], h)
    streak_key = f"live_diverge_streak_{symbol}_{tier}"

    if len(trades) < MIN_LIVE_TRADES:
        db.meta_set(streak_key, "0")
        result = {"judged": False, "n_trades": len(trades), "reason": f"only {len(trades)} live trades (need {MIN_LIVE_TRADES})"}
        db.log_attempt(symbol, None, tier, "live_feedback", "not_judged", result["reason"],
                       strategy=slot["strategy"], params_hash=h, detail=result)
        return result

    day_t, n_days = _day_edge_t_stat(trades)
    if n_days < MIN_LIVE_DAYS:
        db.meta_set(streak_key, "0")
        result = {"judged": False, "n_trades": len(trades), "n_days": n_days,
                  "reason": f"only {n_days} live trading days (need {MIN_LIVE_DAYS})"}
        db.log_attempt(symbol, None, tier, "live_feedback", "not_judged", result["reason"],
                       strategy=slot["strategy"], params_hash=h, detail=result)
        return result

    diverging = day_t <= DIVERGENCE_T_STAT
    streak = (int(db.meta_get(streak_key, "0") or 0) + 1) if diverging else 0
    db.meta_set(streak_key, str(streak))
    result = {"judged": True, "n_trades": len(trades), "n_days": n_days, "day_t_stat": round(day_t, 3),
              "backtest_score": slot.get("score"), "diverging": diverging, "streak": streak}

    if streak >= CONSECUTIVE_CHECKS_TO_DEMOTE:
        db.retire_slot(symbol, tier, "live_divergence")
        db.bump_champion_version()
        db.log_attempt(
            symbol, None, tier, "live_feedback", "demoted",
            f"live trading disagrees with the backtest: day-clustered t-stat {day_t:.2f} over {n_days} live "
            f"trading days and {len(trades)} trades (backtest score {slot.get('score', 0):.2f}), "
            f"{streak} checks in a row",
            strategy=slot["strategy"], params_hash=h, detail=result,
        )
        db.meta_set(streak_key, "0")
        result["demoted"] = True
    else:
        reason = (f"live trading agrees with the backtest: day-clustered t-stat {day_t:.2f} over {n_days} days"
                 if not diverging else
                 f"live trading looks weak (day-clustered t-stat {day_t:.2f} over {n_days} days), watching "
                 f"({streak}/{CONSECUTIVE_CHECKS_TO_DEMOTE} checks)")
        db.log_attempt(symbol, None, tier, "live_feedback", "kept", reason,
                       strategy=slot["strategy"], params_hash=h, detail=result)
    return result


def run(symbols: tuple[str, ...] = ("NQ", "ES")) -> dict[str, Any]:
    """Called once per training cycle (see trainer_service.py). Cheap - it only reads already-
    recorded trades, no backtests - so it runs every cycle rather than being rate-limited like
    the expensive deep-validation stages."""
    out: dict[str, Any] = {}
    for symbol in symbols:
        for tier in ("leader", "confirmed"):
            try:
                r = check(symbol, tier)
            except Exception as e:  # noqa: BLE001 - live feedback must never break a training cycle
                err = f"{type(e).__name__}: {e}"
                r = {"judged": False, "error": err}
                try:  # best-effort: still leave a trace of the failure in the decision log
                    db.log_attempt(symbol, None, tier, "live_feedback", "error", f"live feedback check crashed: {err}")
                except Exception:  # noqa: BLE001
                    pass
            if r is not None:
                out[f"{symbol}:{tier}"] = r
    return out
