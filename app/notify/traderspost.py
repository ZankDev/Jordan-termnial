"""Thin wrapper over a TradersPost webhook (relays a signal to a connected broker —
Tradovate for this terminal, since the live accounts trade NQ/ES futures and Alpaca
cannot). TradersPost gives no fill/order-ID feedback, so this is a fire-and-forget
side-channel: the local SQLite simulation in live_engine.py stays the authoritative
decision-maker for stop/target/eod management, exactly as it is for every other
account. This module only ever talks for the one account explicitly linked via
db.set_traderspost_link — see live_engine._send_traderspost_entry/_exit.

Reads TRADERSPOST_WEBHOOK_URL from the environment on every call (cheap, and lets a
restart pick up a freshly-set value with no extra caching). If unset, every function
here is a silent no-op. Every call is wrapped so a TradersPost/network hiccup only
logs and returns False — it must never propagate into live_engine.live_loop() (same
guarantee telegram_bot.py gives).

The webhook URL already embeds its own auth (uuid + password in the path), so no
separate secret is needed beyond the one env var.
"""
from __future__ import annotations

import logging
import os

import requests

from app.backtest.sizing import MICRO_ROOT

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15


def _url() -> str | None:
    return os.environ.get("TRADERSPOST_WEBHOOK_URL") or None


def is_configured() -> bool:
    return _url() is not None


def ticker_for(symbol: str, contract: str | None) -> str:
    """The ticker string sent to TradersPost. Confirm this matches what the user's
    TradersPost Strategy actually expects (it may want a dated contract or a
    TradingView-style ticker instead of the plain root symbol) before relying on it
    for real orders — check TradersPost's Signal Log on the first live test."""
    symbol = symbol.upper()
    return MICRO_ROOT.get(symbol, symbol) if contract == "micro" else symbol


def _post(payload: dict) -> bool:
    from app.notify import connections, ops_alerts

    url = _url()
    if not url:
        return False
    what = f"{payload.get('action', '?').upper()} {payload.get('quantity', '')} {payload.get('ticker', '')} @ {payload.get('price', '')}".replace("  ", " ")
    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)
        connections.log_signal(payload, resp.status_code, resp.ok, resp.text[:200])
        resp.raise_for_status()
        sl, tp = (payload.get("stopLoss") or {}).get("stopPrice"), (payload.get("takeProfit") or {}).get("limitPrice")
        extra = f"\nstop {sl} / target {tp}\nVerify at the broker that BOTH legs exist: the stop leg has not been confirmed yet." if sl is not None else ""
        ops_alerts.alert("tp_sent", f"🔴 REAL ORDER SENT to TradersPost (HTTP {resp.status_code}): {what}{extra}", cooldown=0)
        return True
    except Exception as e:  # noqa: BLE001
        if not isinstance(e, requests.HTTPError):
            connections.log_signal(payload, None, False, f"{type(e).__name__}: {str(e)[:200]}")
        logger.exception("TradersPost webhook failed: %s", payload)
        ops_alerts.alert("tp_fail", f"❌ TradersPost webhook FAILED for a real order: {what}\nReason: {str(e)[:160]}\nThe local engine thinks the position is open but the broker may NOT have it. Check and act manually.", cooldown=0)
        return False


def send_entry(symbol: str, contract: str | None, side: str, qty: float, price: float, stop_price: float, target_price: float) -> bool:
    # `price` is required, not just advisory: some brokers behind TradersPost (e.g. Apex
    # Trader Funding / Tradovate Prop) have no live quote feed through TradersPost, so a
    # signal with no price falls back to a placeholder entry price instead of the real one.
    return _post({
        "ticker": ticker_for(symbol, contract),
        "action": "buy" if side == "long" else "sell",
        "orderType": "market",
        "price": price,
        "quantity": qty,
        "stopLoss": {"type": "stop", "stopPrice": stop_price},
        "takeProfit": {"limitPrice": target_price},
    })


def send_exit(symbol: str, contract: str | None, price: float) -> bool:
    return _post({"ticker": ticker_for(symbol, contract), "action": "exit", "price": price})
