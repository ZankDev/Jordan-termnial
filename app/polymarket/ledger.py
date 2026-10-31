"""Polymarket paper-account ledger — the session-based counterpart of
app/accounts/ledger.py (same verbs: create/list_all/get/assign_strategy/go_live/
stop_live/reset/delete/trades/dashboard_summary), backed by polymarket_accounts /
polymarket_trades. Every account here is PAPER ONLY — nothing in this module (or
live_engine.py) ever places a real order.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from app.optimizer.search import params_hash
from app.polymarket import clob, store
from app.polymarket.registry import get as get_strategy

DEFAULT_STARTING_BALANCE = 1_000.0
STAKE_USD = 5.0


def create(name: str, starting_balance: float = DEFAULT_STARTING_BALANCE, stake_usd: float = STAKE_USD) -> dict[str, Any]:
    return store.create_pm_account(name, starting_balance, stake_usd=stake_usd)


def list_all(include_archived: bool = False) -> list[dict[str, Any]]:
    return store.list_pm_accounts(include_archived=include_archived)


def get(account_id: int) -> dict[str, Any] | None:
    return store.get_pm_account(account_id)


def assign_strategy(account_id: int, strategy: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    strat = get_strategy(strategy)  # raises KeyError if unknown
    resolved = {**strat["default_params"](), **(params or {})}
    store.assign_pm_strategy(account_id, strategy, resolved, params_hash(resolved))
    return store.get_pm_account(account_id)


def go_live(account_id: int) -> dict[str, Any]:
    account = store.get_pm_account(account_id)
    if not account:
        raise KeyError(f"No polymarket account with id {account_id}")
    if not account.get("strategy"):
        raise ValueError("Assign a strategy to this account before going live.")
    store.set_pm_status(account_id, "live")
    return store.get_pm_account(account_id)


def stop_live(account_id: int) -> dict[str, Any]:
    account = store.get_pm_account(account_id)
    if not account:
        raise KeyError(f"No polymarket account with id {account_id}")
    balance = account["balance"]
    for pos in account.get("open_positions") or []:
        # Manual stop: mark-to-market at the live bid (0.5 if the market can no longer be read).
        token = pos["up_token_id"] if pos["side"] == "up" else pos["down_token_id"]
        price = clob.current_price(token, side="SELL")
        price = 0.5 if price is None else price
        pnl = (price - pos["entry_price"]) * pos["shares"]
        balance = round(balance + pnl, 4)
        store.record_pm_trade(account_id, {
            "session_slug": pos["session_slug"], "asset": pos["asset"], "side": pos["side"],
            "entry_time": pos["entry_time"], "exit_time": dt.datetime.now(dt.timezone.utc).isoformat(),
            "entry_price": pos["entry_price"], "exit_price": price, "shares": pos["shares"], "pnl": pnl,
            "exit_reason": "manual_stop", "flip_prob": pos.get("flip_prob"), "peak_pct": pos.get("peak_pct"),
        }, balance)
    store.set_pm_open_positions(account_id, [])
    store.set_pm_status(account_id, "idle")
    return store.get_pm_account(account_id)


def reset(account_id: int) -> dict[str, Any]:
    store.reset_pm_account(account_id)
    return store.get_pm_account(account_id)


def delete(account_id: int) -> None:
    store.delete_pm_account(account_id)


def trades(account_id: int) -> list[dict[str, Any]]:
    return store.get_pm_trades(account_id)


def account_stats(a: dict[str, Any], trades_: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [t for t in trades_ if t["pnl"] > 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in trades_ if t["pnl"] <= 0))
    return {
        "n_trades": len(trades_),
        "win_rate": round(len(wins) / len(trades_) * 100, 1) if trades_ else 0.0,
        "pnl": round(a["balance"] - a["starting_balance"], 2),
        "pnl_pct": round((a["balance"] / a["starting_balance"] - 1) * 100, 2) if a["starting_balance"] else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_pnl": round(sum(t["pnl"] for t in trades_) / len(trades_), 3) if trades_ else 0.0,
    }


def accounts_with_stats(include_archived: bool = False) -> list[dict[str, Any]]:
    out = []
    for a in store.list_pm_accounts(include_archived=include_archived):
        tr = store.get_pm_trades(a["id"])
        out.append({**a, **account_stats(a, tr), "curve": [[a["created_at"], a["starting_balance"]]] +
                    [[t["exit_time"], t["equity_after"]] for t in tr[-200:]]})
    return out


def dashboard_summary() -> dict[str, Any]:
    accounts = store.list_pm_accounts()
    total_equity = sum(a["balance"] for a in accounts)
    total_start = sum(a["starting_balance"] for a in accounts)
    open_positions = [
        {"account_id": a["id"], "account_name": a["name"], **p} for a in accounts for p in (a.get("open_positions") or [])
    ]
    recent = store.get_recent_pm_trades(limit=300)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    todays = [t for t in recent if str(t["exit_time"])[:10] == today]
    wins = [t for t in recent if t["pnl"] > 0]
    return {
        "total_equity": round(total_equity, 2),
        "total_pnl": round(total_equity - total_start, 2),
        "today_pnl": round(sum(t["pnl"] for t in todays), 4),
        "open_positions_count": len(open_positions),
        "open_positions": open_positions,
        "live_accounts_count": sum(1 for a in accounts if a["status"] == "live"),
        "accounts_count": len(accounts),
        "today_trades_count": len(todays),
        "recent_trades": recent[:40],
        "win_rate_recent": round(len(wins) / len(recent) * 100, 1) if recent else 0.0,
        "n_recent": len(recent),
    }
