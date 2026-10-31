"""REAL Polymarket account API — connect a wallet, set limits, ARM, PANIC. Everything real-money
lives behind /api/polymarket/real/*. See app/polymarket/real_trading.py (safety limits, orders)
and real_engine.py (the trading loop). Off by default; nothing trades until a wallet is
connected AND armed with an explicit confirm.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.polymarket import real_engine, real_trading

router = APIRouter(prefix="/api/polymarket/real")


class RealConnectIn(BaseModel):
    private_key: str
    funder: str = ""
    signature_type: int = 1


class RealSettingsIn(BaseModel):
    stake_usd: float | None = None
    tp_min_pct: float | None = None
    tp_max_pct: float | None = None
    max_concurrent: int | None = None
    max_trades_per_day: int | None = None
    daily_loss_limit: float | None = None
    max_slippage: float | None = None
    max_overround: float | None = None
    assets: list[str] | None = None
    params_source: str | None = None


class RealArmIn(BaseModel):
    armed: bool
    confirm: bool = False


@router.get("/status")
def api_real_status(check_balance: bool = True) -> dict[str, Any]:
    return real_trading.status(check_balance=check_balance)


@router.post("/connect")
def api_real_connect(body: RealConnectIn) -> dict[str, Any]:
    key = body.private_key.strip()
    if len(key.replace("0x", "")) != 64:
        raise HTTPException(400, "a private key is 64 hex characters")
    if body.signature_type not in (0, 1, 2, 3):
        raise HTTPException(400, "signature_type must be 0, 1, 2 or 3")
    if body.signature_type != 0 and not body.funder.strip():
        raise HTTPException(400, "funder (your Polymarket profile / proxy address) is required for this signature type")
    real_trading.save_wallet(key, body.funder, body.signature_type)
    return real_trading.status()


@router.post("/import-bot-env")
def api_real_import() -> dict[str, Any]:
    try:
        return real_trading.import_from_bot_env()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/disconnect")
def api_real_disconnect() -> dict[str, Any]:
    real_trading.disconnect()
    return real_trading.status(check_balance=False)


@router.post("/settings")
def api_real_settings(body: RealSettingsIn) -> dict[str, Any]:
    real_trading.set_settings({k: v for k, v in body.model_dump().items() if v is not None})
    return real_trading.status(check_balance=False)


@router.post("/arm")
def api_real_arm(body: RealArmIn) -> dict[str, Any]:
    if body.armed:
        if not body.confirm:
            raise HTTPException(400, "arming REAL trading needs confirm=true")
        if not real_trading.is_connected():
            raise HTTPException(400, "connect a wallet first")
        st = real_trading.status()
        if st.get("reachable") is False or st.get("balance") is None:
            raise HTTPException(400, f"cannot read the account balance — {st.get('last_error') or 'Polymarket unreachable'}")
        if st["balance"] < st["settings"]["stake_usd"] + 0.05:
            raise HTTPException(400, f"balance ${st['balance']:.2f} is below the ${st['settings']['stake_usd']:.2f} stake")
        real_trading._state["consecutive_errors"] = 0
        real_trading._state["disarm_reason"] = None
        real_trading.set_settings({"armed": True})
    else:
        real_trading.set_settings({"armed": False})
    return real_trading.status(check_balance=False)


@router.post("/panic")
def api_real_panic() -> dict[str, Any]:
    return real_engine.panic()


@router.get("/trades")
def api_real_trades(limit: int = 80) -> list[dict[str, Any]]:
    return real_trading.list_trades(limit=max(1, min(limit, 300)))
