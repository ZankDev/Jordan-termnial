"""Queued strategy switches: move an account onto a new strategy the moment it is flat (never mid-trade).

queue(account_id, strategy, symbol, params, reason) stores the switch in meta 'pending_reassign'. apply_pending() is called by
the live engine every tick: for each queued account with no open position it resets the account to a clean evaluation
(balance back to the start, trades cleared), assigns the new strategy, renames it, puts it back live and tells Telegram.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app import db

logger = logging.getLogger(__name__)
META_KEY = "pending_reassign"


def _load() -> dict[str, Any]:
    try:
        return json.loads(db.meta_get(META_KEY) or "{}")
    except ValueError:
        return {}


def queue(account_id: int, strategy: str, symbol: str, params: dict[str, Any], reason: str) -> None:
    data = _load()
    data[str(account_id)] = {"strategy": strategy, "symbol": symbol, "params": params, "reason": reason}
    db.meta_set(META_KEY, json.dumps(data))


def pending() -> dict[str, Any]:
    return _load()


def apply_pending() -> list[dict[str, Any]]:
    data = _load()
    if not data:
        return []
    from app.accounts import ledger

    done = []
    for sid, job in list(data.items()):
        a = db.get_account(int(sid))
        if not a:
            data.pop(sid, None)
            continue
        if a.get("open_position"):
            continue
        old = a.get("strategy")
        db.reset_account(a["id"])
        db.assign_strategy(a["id"], job["strategy"], job["symbol"], job["params"])
        with db.get_conn() as conn:
            conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (f"{job['strategy']}-{job['symbol']}-#{a['id']} (was {old})", a["id"]))
        try:
            ledger.go_live(a["id"])
        except Exception:  # noqa: BLE001 - no fresh data right now; it stays idle and can be put live later
            logger.exception("reassign: go_live failed for %s", a["id"])
        data.pop(sid, None)
        done.append({"id": a["id"], "from": old, "to": job["strategy"], "reason": job.get("reason")})
    db.meta_set(META_KEY, json.dumps(data))
    if done:
        try:
            from app.notify import telegram_bot

            telegram_bot.send_text("🔁 Strategy switch (long-history check)\n" + "\n".join(
                f"#{d['id']}: {d['from']} → {d['to']}" for d in done))
        except Exception:  # noqa: BLE001
            pass
    return done
