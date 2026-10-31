"""Hard daily safety net: no NOSTRO position survives past 23:59 Israel time — the same thing real prop firms do
automatically at day's end. This is on TOP of the existing Apex evaluation flatten in live_engine.py (16:59 ET, only for
apex_eval accounts in progress): combo-evaluation members and plain 'standard' accounts are not apex_eval, so nothing
was force-closing them, and they were sitting on positions carried past midnight. This module force-flattens EVERY
account, of every type, once per Israel calendar day, and sends a Telegram summary right after.

Runs from live_engine.live_loop() every tick (~60s), so the check has minute-level granularity. Idempotent: a meta flag
keyed by the Israel date means it can only fire once per day even if the tick is late or the server restarts mid-window.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from zoneinfo import ZoneInfo

from app import db
from app.accounts import pnl
from app.accounts.ledger import CONTRACT_MULTIPLIER, DEFAULT_COMMISSION_PER_TRADE, NY_TZ, round_to_tick
from app.data import yfinance_source

logger = logging.getLogger(__name__)
IL = ZoneInfo("Asia/Jerusalem")
CUTOFF_HOUR, CUTOFF_MINUTE = 23, 59
EXIT_REASON = "eod_flatten_il"


def _price(symbol: str, cache: dict[str, tuple[float, str] | None]) -> tuple[float, str] | None:
    if symbol in cache:
        return cache[symbol]
    px = None
    try:
        px = yfinance_source.last_close(symbol, max_age_seconds=120)
        if not px:
            df = yfinance_source.fetch(symbol, "5m", max_age_seconds=120)
            if not df.empty:
                px = (float(df["close"].iloc[-1]), df.index[-1].isoformat())
    except Exception:  # noqa: BLE001 - no price: fall back to the entry price below, never crash the flatten
        pass
    cache[symbol] = px
    return px


def _flatten_one(account: dict[str, Any], cache: dict[str, tuple[float, str] | None], reason: str | None = None) -> dict[str, Any] | None:
    pos = account.get("open_position")
    if not pos:
        return None
    symbol = account["symbol"]
    px = _price(symbol, cache)
    last_close = round_to_tick(px[0], symbol) if px else float(pos["entry_price"])
    exit_time = px[1] if px else dt.datetime.now(NY_TZ).isoformat()
    pos_pv = pnl.position_point_value(symbol, pos)
    commission = pos.get("commission", DEFAULT_COMMISSION_PER_TRADE)
    direction = 1 if pos["side"] == "long" else -1
    trade_pnl = (last_close - pos["entry_price"]) * direction * pos_pv - commission
    new_balance = round(account["balance"] + trade_pnl, 2)
    trade = {
        "entry_time": pos["entry_time"], "exit_time": exit_time, "side": pos["side"], "entry_price": pos["entry_price"],
        "exit_price": last_close, "pnl": round(trade_pnl, 2), "exit_reason": reason or EXIT_REASON,
        "qty": pos.get("qty", 1), "contract": pos.get("contract"), "commission": commission, "risk_dollars": pos.get("risk_dollars"),
    }
    # record_live_trade updates the balance and clears open_position_json; status is left as 'live' so the account
    # keeps trading tomorrow — this is a nightly flatten, not a shutdown.
    db.record_live_trade(account["id"], trade, new_balance)
    return {"id": account["id"], "name": account["name"], **trade}


def _fmt(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def _send_summary(now_il: dt.datetime, closed: list[dict[str, Any]]) -> None:
    from app.accounts import ledger
    from app.notify import telegram_bot

    if not telegram_bot.is_configured():
        return
    d = ledger.dashboard_summary()
    L = [f"NOSTRO END OF DAY — 23:59 Israel time ({now_il.strftime('%a %d %b')})"]
    if closed:
        total = sum(t["pnl"] for t in closed)
        L.append(f"\nForce-closed {len(closed)} position(s) still open at the cutoff: {_fmt(total)}")
        for t in sorted(closed, key=lambda t: t["pnl"]):
            name = t["name"].split(" (was")[0].replace("combo·", "")[:34]
            L.append(f"  {name} — {t['side'].upper()} {_fmt(t['pnl'])}")
    else:
        L.append("\nNothing was open at the cutoff — every account was already flat.")
    L.append(f"\nToday (NY session): closed {_fmt(d['today_pnl'])} across {d['today_trades_count']} trades")
    L.append(f"Net incl. tonight's force-close: {_fmt(d['today_net'])}")
    L.append(f"Total equity: ${d['total_equity']:,.2f} · {d['live_accounts_count']} live · {d['eval_passed_count']} passed")
    if d["today_trades_count"]:
        wins = sum(1 for t in d["today_trades"] if t["pnl"] > 0)
        L.append(f"Win rate today: {round(100 * wins / d['today_trades_count'])}% ({wins}W / {d['today_trades_count'] - wins}L)")
    telegram_bot.send_text("\n".join(L))


def check_and_flatten(now_il: dt.datetime | None = None) -> list[dict[str, Any]] | None:
    """Call every live-engine tick. Once the Israel clock passes 23:59, flattens every open NOSTRO position (any
    account type) at the last known price and sends a Telegram summary — once per Israel calendar day."""
    now_il = now_il or dt.datetime.now(IL)
    if (now_il.hour, now_il.minute) < (CUTOFF_HOUR, CUTOFF_MINUTE):
        return None
    day = now_il.date().isoformat()
    key = f"eod_flatten_il_{day}"
    if db.meta_get(key):
        return None
    db.meta_set(key, "1")  # set the gate before doing any work: a crash mid-flatten must never retry-loop all night

    accounts = [a for a in db.list_accounts(include_archived=False) if a.get("open_position")]
    cache: dict[str, tuple[float, str] | None] = {}
    closed: list[dict[str, Any]] = []
    for a in accounts:
        try:
            t = _flatten_one(a, cache)
            if t:
                closed.append(t)
        except Exception:
            logger.exception("EOD flatten (23:59 IL) failed for account %s", a["id"])
    if closed:
        logger.info("EOD flatten (23:59 IL): force-closed %d position(s)", len(closed))
    try:
        _send_summary(now_il, closed)
    except Exception:
        logger.exception("EOD summary send failed")
    return closed
