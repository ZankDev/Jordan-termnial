"""Operational Telegram alerts for the real-money path: what the system did, and when it is not working. Everything here is best-effort and never raises.

  * alert(kind, text, cooldown): a rate-limited message (the same kind is not repeated within `cooldown` seconds)
  * startup_notice(): one message when the server starts, with the state of the real-money account
  * monitor_loop(): every minute checks that the live engine is still ticking and that the real account has no position open past the end of the session;
    sends one message when something looks wrong and one when it recovers
An external watchdog (launcher/watchdog.py) covers the case where the whole server is down, which nothing inside the server can report."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from zoneinfo import ZoneInfo

from app import db
from app.notify import telegram_bot

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
_last: dict[str, float] = {}


def alert(kind: str, text: str, cooldown: float = 600.0) -> bool:
    """Send `text` unless the same `kind` was sent less than `cooldown` seconds ago. Returns True if it was sent."""
    now = time.time()
    if cooldown and now - _last.get(kind, 0.0) < cooldown:
        return False
    _last[kind] = now
    try:
        telegram_bot.send_text(text)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("ops alert failed")
        return False


def _linked() -> dict | None:
    try:
        with db.get_conn() as conn:
            r = conn.execute("SELECT id, name, status, open_position_json, awaiting_payout FROM accounts WHERE traderspost_linked = 1 LIMIT 1").fetchone()
        return dict(r) if r else None
    except Exception:  # noqa: BLE001
        return None


def startup_notice() -> None:
    from app.notify import traderspost

    acc = _linked()
    lines = ["\U0001F7E2 JORDAN TERMINAL started", dt.datetime.now(NY).strftime("%Y-%m-%d %H:%M NY")]
    if acc:
        pos = json.loads(acc["open_position_json"]) if acc.get("open_position_json") else None
        lines.append(f"Real-money account: {acc['name']} ({acc['status']})")
        lines.append("Open position: " + (f"{pos.get('side')} {pos.get('qty')} @ {pos.get('entry_price')} (stop {pos.get('stop_price')}, target {pos.get('target_price')})" if pos else "none"))
        if pos:
            lines.append("The local engine resumes managing it. Check the same position at the broker.")
    else:
        lines.append("No account is linked for real orders.")
    lines.append("TradersPost webhook: " + ("configured" if traderspost.is_configured() else "NOT configured"))
    alert("startup", "\n".join(lines), cooldown=0)


async def monitor_loop() -> None:
    from app.accounts import live_engine

    await asyncio.sleep(120)  # let the first ticks happen after a start
    stale = False
    stuck = False
    while True:
        try:
            st = live_engine.status() if hasattr(live_engine, "status") else {}
            last = st.get("last_tick_at") or live_engine._state.get("last_tick_at")
            ny = dt.datetime.now(NY)
            market_hours = ny.weekday() < 5 and dt.time(9, 25) <= ny.time() <= dt.time(16, 45)
            if market_hours and last and time.time() - last > 300:
                if not stale:
                    stale = True
                    alert("engine_stale", f"⚠ The live engine has not ticked for {int((time.time() - last) / 60)} minutes during market hours. Open positions are not being managed. Restart the terminal.", cooldown=900)
            elif stale:
                stale = False
                alert("engine_ok", "✅ The live engine is ticking again.", cooldown=0)
            acc = _linked()
            late = ny.weekday() < 5 and ny.time() >= dt.time(17, 5) and bool(acc and acc.get("open_position_json"))
            if late and not stuck:
                stuck = True
                alert("pos_after_close", f"⚠ {acc['name']} still has a position open in the system after 5:05 pm NY (the session should be flat by 4:59 pm). Check the position at the broker and close it manually if it is still open.", cooldown=3600)
            elif not late:
                stuck = False
        except Exception:  # noqa: BLE001
            logger.exception("ops monitor tick failed")
        try:  # follow manual test signals against the market (closes them when the stop or target trades)
            from app.notify import signal_tracker

            await asyncio.to_thread(signal_tracker.tick)
        except Exception:  # noqa: BLE001
            logger.exception("signal tracker failed")
        await asyncio.sleep(60)


STARTED = time.time()


def status_text() -> str:
    """Everything worth knowing from a phone in one message: is it alive, is the engine ticking, what the real-money account is doing, is the trainer finding anything."""
    from app.accounts import live_engine
    from app.notify import traderspost

    ny = dt.datetime.now(NY)
    last = live_engine._state.get("last_tick_at")
    lines = [f"JORDAN TERMINAL status, {ny:%a %H:%M} NY", f"Server up for {int((time.time() - STARTED) / 3600)}h {int((time.time() - STARTED) / 60) % 60}m"]
    lines.append("Live engine: " + (f"last tick {int(time.time() - last)}s ago" if last else "no tick yet") + (f", last error: {live_engine._state.get('last_error')}" if live_engine._state.get("last_error") else ""))
    try:
        with db.get_conn() as conn:
            r = conn.execute("SELECT name, status, balance, open_position_json, awaiting_payout, payout_target FROM accounts WHERE traderspost_linked = 1 LIMIT 1").fetchone()
            f = conn.execute("SELECT symbol, n_total, n_survivors, datetime(ts, 'unixepoch') t FROM field_cycle_stats ORDER BY id DESC LIMIT 1").fetchone()
            surv = conn.execute("SELECT COALESCE(SUM(n_survivors), 0) FROM field_cycle_stats WHERE ts > strftime('%s', 'now', '-1 day')").fetchone()[0]
    except Exception:  # noqa: BLE001
        r = f = None
        surv = None
    if r:
        pos = json.loads(r["open_position_json"]) if r["open_position_json"] else None
        lines.append(f"Real-money account {r['name']}: {r['status']}, simulated balance ${r['balance']:,.0f}" + (f", target ${r['payout_target']:,.0f}" if r["payout_target"] else "") + (", WAITING FOR PAYMENT" if r["awaiting_payout"] else ""))
        lines.append("Position: " + (f"{pos.get('side')} {pos.get('qty')} {pos.get('contract') or ''} @ {pos.get('entry_price')}, stop {pos.get('stop_price')}, target {pos.get('target_price')}" if pos else "flat"))
    else:
        lines.append("No account linked for real orders.")
    lines.append("TradersPost: " + ("configured" if traderspost.is_configured() else "NOT configured"))
    if f:
        lines.append(f"Trainer: last cycle {f['symbol']} {f['n_total']} candidates, {f['n_survivors']} survivors; {surv} survivors in 24h")
    return "\n".join(lines)
