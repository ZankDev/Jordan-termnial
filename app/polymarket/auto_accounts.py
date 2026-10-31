"""Auto paper accounts — the forward test that makes training real.

A backtest can only say what WOULD have worked; these accounts trade the live 5-minute
sessions with real fills at the live ask/bid, so they say what does. Kept running at
all times, no manual setup:

  AUTO · JS DEFAULT     the source bot's original tuned parameters — the fixed baseline
                        every learned parameter set has to beat.
  AUTO · CHAMPION vN    the current champion. When the champion changes, this account is
                        archived (its trades stay, with its own P&L) and a fresh one starts,
                        so each champion keeps an honest, separate forward record.
  AUTO · CANDIDATE 1-3  the best not-yet-promoted candidates from the latest search, each
                        given room to prove itself; rotated once mature (trades or age)
                        and only when flat.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app import db
from app.optimizer.search import params_hash
from app.polymarket import store
from app.polymarket.registry import get as get_strategy

logger = logging.getLogger(__name__)

N_CANDIDATE_SLOTS = 3
ROTATE_MIN_TRADES = 25
ROTATE_MIN_AGE_HOURS = 8
STARTING_BALANCE = 1_000.0
STAKE = 5.0


def _active(role: str) -> list[dict[str, Any]]:
    return [a for a in store.list_pm_accounts() if a.get("role") == role and a.get("is_auto")]


def _age_hours(a: dict[str, Any]) -> float:
    try:
        created = dt.datetime.strptime(a["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - created).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return 1e9


def _new(name: str, role: str, params: dict[str, Any]) -> dict[str, Any]:
    strat = get_strategy("momo")
    resolved = {**strat["default_params"](), **params}
    acct = store.create_pm_account(name, STARTING_BALANCE, role=role, is_auto=True, stake_usd=STAKE)
    store.assign_pm_strategy(acct["id"], "momo", resolved, params_hash(resolved))
    store.set_pm_status(acct["id"], "live")
    logger.info("auto account created: %s", name)
    return store.get_pm_account(acct["id"])


def ensure_baseline() -> None:
    """Called at startup and after every cycle: the baseline exists and is live, and any
    auto account left idle (e.g. by a manual stop) is left alone — the user's call."""
    if not _active("default"):
        _new("AUTO · JS DEFAULT", "default", get_strategy("momo")["default_params"]())


def sync(champion: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Point the auto accounts at the latest champion / candidates. Returns a summary."""
    ensure_baseline()
    summary: dict[str, Any] = {"created": [], "rotated": []}
    default_hash = params_hash({**get_strategy("momo")["default_params"]()})

    # ---- champion
    champ_hash = champion["params_hash"] if champion else None
    if champion:
        current = _active("champion")
        if not any(a["params_hash"] == champ_hash for a in current):
            for old in current:
                if old["open_positions"]:
                    summary["deferred"] = "champion swap waits for open positions to close"
                    return summary
            for old in current:
                store.archive_pm_account(old["id"], "rotated")
            acct = _new(f"AUTO · CHAMPION v{champion.get('version')}", "champion", champion["params"])
            summary["created"].append(acct["name"])

    # ---- candidates: best distinct eligible sets that are neither champion nor baseline
    taken = {a["params_hash"] for a in store.list_pm_accounts() if a.get("is_auto") and a.get("role") in ("champion", "default")}
    pool = []
    for c in candidates:
        h = params_hash(c["params"])
        if h in taken or h in (champ_hash, default_hash) or any(h == params_hash(p["params"]) for p in pool):
            continue
        pool.append(c)

    slots = _active("candidate")
    for slot in slots:
        mature = (len(store.get_pm_trades(slot["id"])) >= ROTATE_MIN_TRADES) or _age_hours(slot) >= ROTATE_MIN_AGE_HOURS
        still_good = slot["params_hash"] in {params_hash(c["params"]) for c in candidates[:8]}
        if mature and not still_good and not slot["open_positions"] and pool:
            store.archive_pm_account(slot["id"], "rotated")
            new = pool.pop(0)
            acct = _new(slot["name"], "candidate", new["params"])
            summary["rotated"].append(acct["name"])
    active_hashes = {a["params_hash"] for a in _active("candidate")}
    pool = [c for c in pool if params_hash(c["params"]) not in active_hashes]
    while len(_active("candidate")) < N_CANDIDATE_SLOTS and pool:
        n = len(_active("candidate")) + 1
        used = {a["name"] for a in _active("candidate")}
        name = next((f"AUTO · CANDIDATE {k}" for k in range(1, N_CANDIDATE_SLOTS + 1) if f"AUTO · CANDIDATE {k}" not in used),
                    f"AUTO · CANDIDATE {n}")
        acct = _new(name, "candidate", pool.pop(0)["params"])
        summary["created"].append(acct["name"])
    return summary
