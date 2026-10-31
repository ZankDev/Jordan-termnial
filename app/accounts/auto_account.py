"""The AUTO account: a dedicated paper account that always trades the current champion.

Why: the champion pipeline (app/optimizer/promotion.py) keeps "the best strategy" fresh, but
nothing ever put it in front of the market. This supervisor runs one system account
(is_system=1, a 50k Apex evaluation) and swaps its strategy/params when a better champion
appears - so the best strategy is always the one actually being forward-tested, and every
evaluation it runs (pass / fail) becomes real forward evidence for that strategy.

It only ever touches its own account. The user's other live accounts are never modified:
every write here is keyed by the AUTO account's id.

Rules, chosen to avoid churn and to keep the forward record clean:
  * target: the best CONFIRMED slot if one exists, else a SEASONED leader (AUTO_ALLOW_LEADER).
    A leader is seasoned once it has held the slot for a while and keeps passing re-scoring -
    a fresh leader can be a lucky draw, so it isn't traded immediately.
  * swap only while FLAT (never yank a strategy out from under an open position), and not
    more often than MIN_HOLD_HOURS.
  * never a strategy whose stress battery says "failed" (app/optimizer/stress_battery.py) or that
    loses over the verified multi-month history (the battery's "history" check).
  * the account's risk per trade follows the Apex gate's recommended risk for that strategy.
  * when its evaluation concludes (passed / failed) the account is archived (trades kept)
    and a fresh generation is created - never reset, which would delete the record.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app import db
from app.accounts import apex_rules, ledger
from app.notify import telegram_bot

logger = logging.getLogger(__name__)

AUTO_NAME_PREFIX = "NOSTRO-AUTO"
AUTO_TIER = 50_000
AUTO_ALLOW_LEADER = True  # confirmed needs multi-year data that is still being repaired; set False for strictly confirmed
MIN_HOLD_HOURS = 12
SEASONED_LEADER_AGE_HOURS = 6
SEASONED_MIN_RESCORES = 6
SEASONED_OK_RATIO = 0.8
SWAP_MARGIN = 0.15  # a new target must beat the held strategy's current score by this fraction
DEFAULT_RISK = 250.0


def _resolved_hash(strategy: str | None, params: dict[str, Any] | None) -> str | None:
    """Hash of params with the strategy defaults filled in - ledger.assign_strategy stores the
    resolved set, so a slot and the account it feeds only compare equal on that basis."""
    from app.optimizer.search import params_hash
    from app.strategies import registry

    if not strategy:
        return None
    try:
        return params_hash({**registry.get(strategy).default_params(), **(params or {})})
    except KeyError:
        return None


def _hours_since(sql_ts: str | None) -> float:
    if not sql_ts:
        return float("inf")
    try:
        t = dt.datetime.strptime(sql_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return float("inf")
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600


def _seasoned(slot: dict[str, Any]) -> bool:
    history = slot.get("rescore_history") or []
    if len(history) < SEASONED_MIN_RESCORES or slot.get("consecutive_fails"):
        return False
    ok_ratio = sum(1 for h in history if h.get("ok")) / len(history)
    return _hours_since(slot.get("promoted_at")) >= SEASONED_LEADER_AGE_HOURS and ok_ratio >= SEASONED_OK_RATIO


def _target_from(slot: dict[str, Any], tier: str) -> dict[str, Any]:
    apex = slot.get("apex") or {}
    risk = apex.get("recommended_risk_dollars") or DEFAULT_RISK
    return {
        "symbol": slot["symbol"], "tier": tier, "strategy": slot["strategy"], "params": slot["params"],
        "params_hash": _resolved_hash(slot["strategy"], slot["params"]), "version": slot.get("version"),
        "score": slot.get("deep_score") if tier == "confirmed" and slot.get("deep_score") is not None else slot.get("score"),
        "risk_dollars": float(risk),
        "cert": (slot.get("certification") or {}).get("status", "watch"),  # unjudged counts as watch
        # a strategy that loses over the verified multi-month history is never traded
        "history_ok": (slot.get("certification") or {}).get("history_ok", True),
    }


def pick_target() -> dict[str, Any] | None:
    confirmed, seasoned = [], []
    for symbol in ("NQ", "ES"):
        c = db.get_slot(symbol, "confirmed")
        if c:
            confirmed.append(_target_from(c, "confirmed"))
        lead = db.get_slot(symbol, "leader")
        if AUTO_ALLOW_LEADER and lead and _seasoned(lead):
            seasoned.append(_target_from(lead, "leader"))
    pool = confirmed or seasoned
    # never trade a strategy the stress battery rejected; among the rest, certified beats watch
    pool = [t for t in pool if t["cert"] != "failed" and t["history_ok"]]
    return max(pool, key=lambda t: (t["cert"] == "certified", t["score"] or 0)) if pool else None


def _account_hash(acct: dict[str, Any]) -> str | None:
    import json

    raw = acct.get("strategy_params_json")
    return _resolved_hash(acct.get("strategy"), json.loads(raw) if raw else None) if acct.get("strategy") else None


def _held_score(acct: dict[str, Any]) -> float | None:
    """The held strategy's current same-scale score if it is still a champion slot, else None."""
    h = _account_hash(acct)
    for tier in ("confirmed", "leader"):
        slot = db.get_slot(acct["symbol"], tier) if acct.get("symbol") else None
        if slot and slot["strategy"] == acct.get("strategy") and _resolved_hash(slot["strategy"], slot["params"]) == h:
            return slot.get("deep_score") if tier == "confirmed" and slot.get("deep_score") is not None else slot.get("score")
    return None


def _conclude_if_done(acct: dict[str, Any]) -> bool:
    status = acct.get("eval_status")
    if status not in ("passed", "failed"):
        return False
    db.archive_account(acct["id"], f"eval_{status}")
    db.log_auto_event("eval_concluded", acct["id"], acct.get("symbol"), acct.get("strategy"), None,
                      reason=f"evaluation {status}: {acct.get('eval_fail_reason') or 'target reached'}")
    telegram_bot.send_text(
        f"NOSTRO-AUTO evaluation {status.upper()} on {acct.get('strategy')} ({acct.get('symbol')}) - "
        f"balance ${acct['balance']:,.0f}. A new evaluation will start automatically."
    )
    return True


def tick() -> None:
    """One supervision pass. Safe to call every live-loop iteration; does nothing most times."""
    if db.meta_get("auto_account_disabled") == "1":  # the owner chose to run only the accounts they add by hand
        return
    acct = db.get_active_system_account()
    if acct and _conclude_if_done(acct):
        acct = None  # next block starts a fresh generation

    target = pick_target()
    if not target:
        return

    if acct is None:
        tier = apex_rules.tier_for_size(AUTO_TIER)
        name = f"{AUTO_NAME_PREFIX} {dt.datetime.now():%Y-%m-%d %H:%M}"
        acct = db.create_account(
            name, starting_balance=float(AUTO_TIER), account_type="apex_eval", profit_target=tier["profit_target"],
            max_drawdown=tier["max_drawdown"], max_contracts_minis=tier["max_contracts_minis"],
            sizing_mode="risk_micro", risk_per_trade=target["risk_dollars"], is_system=1,
        )
        db.log_auto_event("created", acct["id"], reason="auto account created")
        logger.info("AUTO account created: id %s", acct["id"])

    current_hash = _account_hash(acct)
    same = acct.get("strategy") == target["strategy"] and acct.get("symbol") == target["symbol"] and current_hash == target["params_hash"]

    if not same:
        flat = acct.get("open_position") is None
        held_ok = (not acct.get("strategy")) or _hours_since(db.last_auto_swap_ts(acct["id"])) >= MIN_HOLD_HOURS
        held_score = _held_score(acct) if acct.get("strategy") else None
        clearly_better = held_score is None or (target["score"] or 0) >= held_score * (1 + SWAP_MARGIN)
        if not (flat and held_ok and clearly_better):
            return
        old = acct.get("strategy")
        ledger.assign_strategy(acct["id"], target["strategy"], target["symbol"], target["params"])
        db.set_risk_per_trade(acct["id"], target["risk_dollars"])
        db.log_auto_event(
            "swap", acct["id"], target["symbol"], old, target["strategy"], target["tier"],
            f"following {target['tier']} {target['strategy']} on {target['symbol']} (score {target['score']:.2f}, "
            f"risk ${target['risk_dollars']:.0f}/trade)", target.get("version"),
        )
        telegram_bot.send_text(
            f"NOSTRO-AUTO now follows {target['tier'].upper()} {target['strategy']} on {target['symbol']} "
            f"(score {target['score']:.2f}, risk ${target['risk_dollars']:.0f}/trade)"
            + (f" - replaced {old}" if old else "")
        )
        acct = db.get_account(acct["id"])

    if acct.get("status") != "live" and acct.get("strategy") and acct.get("symbol"):
        try:
            ledger.go_live(acct["id"])
        except (ValueError, RuntimeError, KeyError) as e:
            logger.warning("AUTO account could not go live: %s", e)


def status() -> dict[str, Any]:
    acct = db.get_active_system_account()
    return {"account": acct, "target": pick_target(), "events": db.list_auto_events(15)}
