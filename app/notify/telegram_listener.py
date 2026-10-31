"""Long-polls Telegram for taps on the trade alert's \U0001F504 Refresh button and
edits that message with the freshest available status: live unrealized P&L if the
position is still open, or the final closed result if it already closed (a tap can
land well after the position closed, so both cases must produce a correct caption).

Runs as a background asyncio task alongside live_engine.live_loop() (see app/main.py),
and is a no-op loop when Telegram isn't configured.
"""
from __future__ import annotations

import asyncio
import logging

from app import db
from app.accounts import ledger, pnl
from app.data import yfinance_source
from app.notify import telegram_bot
from app.strategies import registry

logger = logging.getLogger(__name__)

_state: dict = {"offset": None}


def _handle_refresh(account_id: int, message_id: int, callback_query_id: str) -> None:
    from app.accounts import live_engine  # local import: live_engine imports telegram_bot at module load

    account = db.get_account(account_id)
    if not account or not account.get("strategy"):
        telegram_bot.answer_callback_query(callback_query_id, "Account no longer exists")
        return

    strategy = registry.get(account["strategy"])
    open_pos = account.get("open_position")
    symbol = account["symbol"]

    if open_pos and account.get("telegram_msg_id") == message_id:
        df = yfinance_source.fetch(symbol, "5m", max_age_seconds=90)
        price = float(df.iloc[-1]["close"]) if not df.empty else open_pos["entry_price"]
        unrealized = pnl.unrealized(symbol, open_pos["side"], open_pos["entry_price"], price, open_pos.get("qty", 1), open_pos.get("contract"))
        live_engine._send_telegram_pnl_update(account, symbol, strategy, message_id, open_pos, unrealized)
        telegram_bot.answer_callback_query(callback_query_id, "Updated")
        return

    trades = ledger.trades(account_id)
    if trades and trades[-1].get("stop_price") is not None:
        live_engine._send_telegram_close(account, symbol, strategy, message_id, trades[-1])
        telegram_bot.answer_callback_query(callback_query_id, "Trade already closed")
    else:
        telegram_bot.answer_callback_query(callback_query_id, "No data for this trade")


def _handle_text(msg: dict) -> None:
    """Text from the owner's chat only (TELEGRAM_CHAT_ID): WEALTH answers (credit-card spend, /balance)."""
    import os

    if str((msg.get("chat") or {}).get("id")) != str(os.environ.get("TELEGRAM_CHAT_ID")):
        return
    try:
        from app.notify import live_summary
        from app.wealth import cashflow

        text = msg.get("text") or ""
        from app.notify import commands

        if commands.handle(text):
            return
        if text.strip().lower().startswith("/summary"):
            reply = live_summary.handle_command(text)
            if reply:
                telegram_bot.send_text(reply)
            return
        reply = cashflow.handle_telegram_text(text)
        if reply:
            telegram_bot.send_text(reply)
    except Exception:
        logger.exception("Telegram text handling failed")


async def listener_loop() -> None:
    while True:
        if not telegram_bot.is_configured():
            await asyncio.sleep(60)
            continue
        try:
            updates = await asyncio.to_thread(telegram_bot.get_updates, _state["offset"])
            for update in updates:
                _state["offset"] = update["update_id"] + 1
                msg = update.get("message")
                if msg and msg.get("text"):
                    await asyncio.to_thread(_handle_text, msg)
                    continue
                cq = update.get("callback_query")
                if not cq:
                    continue
                data = cq.get("data") or ""
                if data.startswith("iq:"):  # ICEBERG study quiz answer
                    try:
                        from app.learn import iceberg

                        await asyncio.to_thread(iceberg.handle_callback, data, cq["message"]["message_id"], cq["id"])
                    except Exception:
                        logger.exception("Telegram quiz-button handling failed")
                        telegram_bot.answer_callback_query(cq["id"], "Could not record")
                    continue
                if data.startswith("plan:"):  # WEALTH › road to ₪1M daily checklist tap
                    try:
                        from app.wealth import plan

                        await asyncio.to_thread(plan.handle_callback, data, cq["message"]["message_id"], cq["id"])
                    except Exception:
                        logger.exception("Telegram plan-button handling failed")
                        telegram_bot.answer_callback_query(cq["id"], "Could not update")
                    continue
                if not data.startswith("refresh:"):
                    continue
                try:
                    account_id = int(data.split(":", 1)[1])
                    message_id = cq["message"]["message_id"]
                    await asyncio.to_thread(_handle_refresh, account_id, message_id, cq["id"])
                except Exception:
                    logger.exception("Telegram refresh-button handling failed")
                    telegram_bot.answer_callback_query(cq["id"], "Refresh failed")
        except Exception:
            logger.exception("Telegram listener loop error")
            await asyncio.sleep(5)
