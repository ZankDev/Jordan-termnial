"""Give every account the position size that makes its strategy most likely to PASS the Apex $50k evaluation.

Whether an evaluation passes depends more on risk per trade than on the entry logic (see optimizer/apex_gate.py). The trainer
already replays each candidate through the Apex rules at several risk levels and finds the best one
(`recommended_risk_dollars`), but until now only the AUTO account used it — every other account ran a flat size.

remember(): called by the trainer for the best-scoring candidate of each strategy every cycle; stores
            {symbol: {strategy: {risk, pass, breach, starts, ts}}} in meta 'apex_risk'.
sync():     for every non-AUTO account on risk-sized (micro) sizing, set risk_per_trade to its strategy's recommended risk,
            clamped to [MIN_RISK, MAX_RISK], when a judged, fresh (< MAX_AGE_DAYS) recommendation exists. Returns the changes.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from app import db

logger = logging.getLogger(__name__)
META_KEY = "apex_risk"
MIN_RISK, MAX_RISK = 75.0, 500.0
MAX_AGE_DAYS = 7
MIN_CHANGE = 10.0            # ignore changes smaller than this (noise between cycles)


def _load() -> dict[str, Any]:
    try:
        return json.loads(db.meta_get(META_KEY) or "{}")
    except ValueError:
        return {}


DEEP_PRIORITY_DAYS = 3        # a long-history recommendation is not overwritten by a 60-day one for this long


def remember(symbol: str, strategy: str, info: dict[str, Any], source: str = "fast") -> None:
    """source='deep' = Apex replay over years of out-of-sample trades; 'fast' = the 60-day proxy. Deep wins while fresh."""
    if not info or not info.get("judged") or not info.get("recommended_risk_dollars"):
        return
    data = _load()
    prev = (data.get(symbol) or {}).get(strategy)
    if source == "fast" and prev and prev.get("src") == "deep" and time.time() - prev["ts"] < DEEP_PRIORITY_DAYS * 86400:
        return
    data.setdefault(symbol, {})[strategy] = {
        "risk": float(info["recommended_risk_dollars"]), "pass": info.get("pass_rate"), "breach": info.get("breach_rate"),
        "starts": info.get("n_starts"), "ts": time.time(), "src": source,
    }
    db.meta_set(META_KEY, json.dumps(data))


def recommendations() -> dict[str, Any]:
    return _load()


def sync() -> list[dict[str, Any]]:
    data = _load()
    changes: list[dict[str, Any]] = []
    try:  # combo members keep the size the combo was designed with (the combo test chose it for the shared drawdown)
        from app.accounts import combo_eval

        combo_members = combo_eval.member_ids()
    except Exception:  # noqa: BLE001
        combo_members = set()
    for a in db.list_accounts():
        if a.get("is_system") or not a.get("strategy") or not a.get("symbol") or a.get("sizing_mode") != "risk_micro" or a["id"] in combo_members:
            continue
        rec = (data.get(a["symbol"]) or {}).get(a["strategy"])
        if not rec or time.time() - rec["ts"] > MAX_AGE_DAYS * 86400:
            continue
        risk = max(MIN_RISK, min(MAX_RISK, rec["risk"]))
        if a.get("risk_per_trade") and abs(a["risk_per_trade"] - risk) < MIN_CHANGE:
            continue
        db.set_risk_per_trade(a["id"], risk)
        changes.append({"id": a["id"], "name": a["name"], "strategy": a["strategy"], "from": a.get("risk_per_trade"), "to": risk,
                        "pass": rec.get("pass"), "breach": rec.get("breach")})
    if changes:
        logger.info("risk_sync: %s", changes)
    return changes
