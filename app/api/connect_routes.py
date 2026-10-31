"""REAL ACCOUNT SETUP API (NOSTRO > REAL ACCOUNT): connect TradersPost and Telegram from the browser, see the signals sent, and keep a record of the real balance.
Nothing here sends an order: the test signal stays behind the account's own TEST LONG button."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.notify import connections, telegram_bot

router = APIRouter(prefix="/api/connect")


class UrlIn(BaseModel):
    url: str


class TokenIn(BaseModel):
    token: str


class BalanceIn(BaseModel):
    balance: float
    note: str = ""


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/status")
def api_status():
    return connections.status()


@router.post("/traderspost")
def api_set_webhook(b: UrlIn):
    try:
        return connections.set_webhook(b.url)
    except ValueError as e:
        raise _bad(e) from e


@router.delete("/traderspost")
def api_clear_webhook():
    connections.clear_webhook()
    return {"ok": True}


@router.post("/telegram/token")
def api_tg_token(b: TokenIn):
    try:
        return connections.telegram_verify_token(b.token)
    except ValueError as e:
        raise _bad(e) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Could not reach Telegram.") from e


@router.post("/telegram/detect")
def api_tg_detect():
    try:
        return connections.telegram_detect_chat()
    except ValueError as e:
        raise _bad(e) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Could not reach Telegram.") from e


@router.post("/telegram/test")
def api_tg_test():
    if not telegram_bot.is_configured():
        raise HTTPException(status_code=409, detail="Telegram is not connected yet.")
    mid = telegram_bot.send_text_id("\u2705 JORDAN TERMINAL is connected to this chat. Alerts for real orders, failures and /report progress will arrive here. Send /status any time.")
    if not mid:
        raise HTTPException(status_code=502, detail="Telegram did not accept the message. Check the token and that you pressed START in the bot.")
    return {"ok": True}


@router.delete("/telegram")
def api_tg_clear():
    connections.clear_telegram()
    return {"ok": True}


@router.post("/balance")
def api_balance(b: BalanceIn):
    try:
        return connections.add_balance(b.balance, b.note)
    except ValueError as e:
        raise _bad(e) from e


@router.post("/balance/csv")
async def api_balance_csv(request: Request):
    data = await request.body()
    if not data or len(data) > 5_000_000:
        raise HTTPException(status_code=400, detail="Send the CSV file contents (up to 5 MB).")
    try:
        return connections.import_tradovate_csv(data)
    except ValueError as e:
        raise _bad(e) from e
