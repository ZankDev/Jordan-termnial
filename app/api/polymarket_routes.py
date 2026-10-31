"""Polymarket momo vertical API — accounts CRUD/go-live, dashboard, trainer/champion
status, the momo radar, and session chart data (line-series, for web/pm_chart.js).
Mirrors app/api/live_routes.py's + capital_routes.py's shape where the concepts
overlap, prefixed separately so nothing here can collide with the futures routes.
"""
from __future__ import annotations

from typing import Any

import time

from fastapi import APIRouter, HTTPException

from app.notify import telegram_bot
from pydantic import BaseModel

from app.polymarket import backfill, copytrade, gamma, ledger, live_engine, pm_promotion, radar, real_engine, real_trading, spot, store, trainer_service
from app.polymarket.registry import list_strategies, param_specs

router = APIRouter(prefix="/api/polymarket")

_ttl: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    """Tiny per-process TTL cache: the UI polls several endpoints every few seconds, and
    on a busy server each uncached call queues behind the trainer for the DB lock."""
    now = time.time()
    hit = _ttl.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _ttl[key] = (now, val)
    return val


class CreateAccountIn(BaseModel):
    name: str
    starting_balance: float = 1_000.0
    stake_usd: float = 5.0


class AssignStrategyIn(BaseModel):
    strategy: str
    params: dict[str, Any] | None = None


class CreateCopyAccountIn(BaseModel):
    name: str
    wallet_address: str
    starting_balance: float = 1_000.0
    stake_usd: float = 5.0


@router.get("/strategies")
def api_list_strategies() -> dict[str, Any]:
    return {
        "strategies": [
            {"name": name, "params": [p.__dict__ for p in param_specs(name)]}
            for name in list_strategies()
        ]
    }


@router.get("/accounts")
def api_list_accounts(include_archived: bool = False) -> list[dict[str, Any]]:
    return _cached(f"accounts:{include_archived}", 3.0, lambda: ledger.accounts_with_stats(include_archived=include_archived))


@router.post("/accounts")
def api_create_account(body: CreateAccountIn) -> dict[str, Any]:
    return ledger.create(body.name, body.starting_balance, body.stake_usd)


@router.get("/accounts/{account_id}")
def api_get_account(account_id: int) -> dict[str, Any]:
    account = ledger.get(account_id)
    if not account:
        raise HTTPException(404, "account not found")
    return account


@router.post("/accounts/{account_id}/strategy")
def api_assign_strategy(account_id: int, body: AssignStrategyIn) -> dict[str, Any]:
    try:
        return ledger.assign_strategy(account_id, body.strategy, body.params)
    except KeyError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/accounts/{account_id}/go-live")
def api_go_live(account_id: int) -> dict[str, Any]:
    try:
        return ledger.go_live(account_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@router.post("/accounts/{account_id}/stop-live")
def api_stop_live(account_id: int) -> dict[str, Any]:
    try:
        return ledger.stop_live(account_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/accounts/{account_id}/reset")
def api_reset(account_id: int) -> dict[str, Any]:
    return ledger.reset(account_id)


@router.delete("/accounts/{account_id}")
def api_delete(account_id: int) -> dict[str, str]:
    ledger.delete(account_id)
    return {"status": "deleted"}


@router.get("/accounts/{account_id}/trades")
def api_trades(account_id: int) -> list[dict[str, Any]]:
    return ledger.trades(account_id)


@router.get("/dashboard")
def api_dashboard() -> dict[str, Any]:
    d = ledger.dashboard_summary()
    live = live_engine.LIVE
    d["engine"] = {"last_tick": live["ts"], "tick_ms": live.get("tick_ms"), "recorded": live.get("recorded"),
                   "error": live.get("error"), "running_sessions": len(live["rows"])}
    d["telegram"] = telegram_bot.is_configured()
    return d


@router.get("/champion")
def api_champion() -> dict[str, Any] | None:
    return pm_promotion.get_champion()


@router.get("/trainer/status")
def api_trainer_status() -> dict[str, Any]:
    return _cached("trainer_status", 3.0, _trainer_status)


def _trainer_status() -> dict[str, Any]:
    import json as _json

    from app import db

    latest = None
    with db.get_conn() as conn:
        row = conn.execute("SELECT id, created_at, n_iterations, n_sessions, result_json FROM pm_optimizer_runs ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        latest = {"id": row["id"], "created_at": row["created_at"], "n_sessions": row["n_sessions"],
                  "top": [{"params": r["params"], "score": r["score"], "test_metrics": r["test_metrics"],
                           "fold_scores": r.get("fold_scores"), "profitable_folds": r.get("profitable_folds"),
                           "diverged": r.get("diverged"), "too_smooth": r.get("too_smooth"),
                           "exceeds_burn_limit": r.get("exceeds_burn_limit")}
                          for r in _json.loads(row["result_json"])[:12]]}
    return {
        "state": trainer_service.live_state(),
        "corpus": trainer_service.corpus_stats(),
        "champion": pm_promotion.get_champion(),
        "history": trainer_service.history(200),
        "attempts": pm_promotion.list_attempts(15),
        "champion_history": pm_promotion.list_history(10),
        "latest_run": latest,
        "n_resolved_sessions": store.count_resolved_sessions(),
        "server_time": time.time(),
    }


@router.get("/sessions/live")
def api_sessions_live() -> dict[str, Any]:
    return live_engine.live_view()


@router.get("/sessions/{slug}/chart")
def api_session_chart(slug: str) -> dict[str, Any]:
    sess = store.get_session_by_slug(slug)
    live_ticks = live_engine._ticks.get(slug)
    trades = [t for t in store.get_recent_pm_trades(limit=3000) if t["session_slug"] == slug]
    if sess and (sess.get("resolved_up") is not None or not live_ticks):
        prices = store.get_prices(sess["id"])
        spot_trace = store.get_spot_trace(sess["id"])
        return {"session": sess, "up_price": [{"t": t, "p": p} for t, p in prices],
                "spot": [{"t": t, "p": p} for t, p in spot_trace], "trades": trades}
    row = next((r for r in live_engine.LIVE["rows"] if r["slug"] == slug), None)
    if not row:
        raise HTTPException(404, "session not found")
    trace = [(t, p) for t, p in spot.recent_1s(row["asset"]) if t >= row["start_ts"] - 2]
    return {
        "session": {"slug": slug, "asset": row["asset"], "strike_price": row.get("strike"), "resolved_up": None,
                    "start_ts": row["start_ts"], "end_ts": row["end_ts"]},
        "up_price": [{"t": int(t), "p": up} for t, up, _d in (live_ticks or [])],
        "spot": [{"t": t, "p": p} for t, p in trace], "trades": trades,
    }


@router.get("/trades/recent")
def api_recent_trades(limit: int = 60) -> list[dict[str, Any]]:
    return store.get_recent_pm_trades(limit=max(1, min(limit, 500)))


@router.post("/telegram/digest")
def api_telegram_digest() -> dict[str, Any]:
    if not telegram_bot.is_configured():
        raise HTTPException(400, "Telegram is not configured on the server (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    trainer_service.send_digest(manual=True)
    return {"status": "sent"}


@router.get("/series")
def api_series() -> list[dict[str, Any]]:
    return gamma.discover_5m_series()


@router.post("/backfill")
def api_trigger_backfill() -> dict[str, Any]:
    """Manual/testing trigger — the trainer loop calls backfill.backfill_all() on
    its own cadence; this exists for an on-demand top-up without waiting."""
    return backfill.backfill_all(max_pages_per_series=1)


# ---------------------------------------------------------------------------
# Copytrader (Phase 10) — paper-shadows a tracked wallet's real trades.
# ---------------------------------------------------------------------------

@router.get("/copy-accounts")
def api_list_copy_accounts(include_archived: bool = False) -> list[dict[str, Any]]:
    return store.list_copy_accounts(include_archived=include_archived)


@router.post("/copy-accounts")
def api_create_copy_account(body: CreateCopyAccountIn) -> dict[str, Any]:
    if not body.wallet_address.startswith("0x") or len(body.wallet_address) != 42:
        raise HTTPException(400, "wallet_address must be a 0x-prefixed 42-character Polygon address")
    return store.create_copy_account(body.name, body.wallet_address, body.starting_balance, body.stake_usd)


@router.post("/copy-accounts/{account_id}/go-live")
def api_copy_go_live(account_id: int) -> dict[str, Any]:
    account = store.get_copy_account(account_id)
    if not account:
        raise HTTPException(404, "copy account not found")
    store.set_copy_status(account_id, "live")
    return store.get_copy_account(account_id)


@router.post("/copy-accounts/{account_id}/stop-live")
def api_copy_stop_live(account_id: int) -> dict[str, Any]:
    account = store.get_copy_account(account_id)
    if not account:
        raise HTTPException(404, "copy account not found")
    store.set_copy_status(account_id, "idle")
    return store.get_copy_account(account_id)


@router.post("/copy-accounts/{account_id}/poll")
def api_copy_poll_now(account_id: int) -> dict[str, Any]:
    """Manual/testing trigger for an immediate poll, outside the loop's own cadence."""
    account = store.get_copy_account(account_id)
    if not account:
        raise HTTPException(404, "copy account not found")
    fills = copytrade.poll_wallet(account)
    return {"fills": fills, "account": store.get_copy_account(account_id)}


@router.get("/copy-accounts/{account_id}/trades")
def api_copy_trades(account_id: int) -> list[dict[str, Any]]:
    return store.get_copy_trades(account_id)


@router.delete("/copy-accounts/{account_id}")
def api_delete_copy_account(account_id: int) -> dict[str, str]:
    store.delete_copy_account(account_id)
    return {"status": "deleted"}
