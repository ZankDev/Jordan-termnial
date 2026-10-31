"""Schedules the morning brief: built at 8:15 New York time and sent at 8:30, every day (weekends as a watchlist).
The day's send is recorded in morning_briefs.sent, so a restart after 8:30 does not send it twice; a server that was down at
8:30 still sends that day's brief when it comes back (until noon, after which the morning brief is stale and skipped)."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app import db
from app.morning import brief

logger = logging.getLogger(__name__)
SEND_AT = (8, 30)
BUILD_AT = (8, 15)
STALE_AFTER = (12, 0)
_state: dict = {"last_error": None, "last_sent": None, "built_day": None}


def _sent(day: str) -> bool:
    brief.ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT sent FROM morning_briefs WHERE day = ?", (day,)).fetchone()
    return bool(r and r["sent"])


def _tick() -> None:
    now = dt.datetime.now(brief.NY)
    day, hm = now.date().isoformat(), (now.hour, now.minute)
    if hm < BUILD_AT or hm >= STALE_AFTER or _sent(day):
        return
    if _state["built_day"] != day:
        b = brief.build(day)
        _state["built_day"], _state["brief"] = day, b
    if hm >= SEND_AT:
        b = _state.get("brief") or brief.build(day)
        if hm >= (8, 45):  # built long ago or the server was down: rebuild so prices are fresh
            b = brief.build(day)
        if brief.send(b):
            _state["last_sent"] = day
            logger.info("morning brief sent for %s", day)


def status() -> dict:
    return {k: v for k, v in _state.items() if k != "brief"}


async def morning_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(_tick)
            _state["last_error"] = None
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = str(e)
            logger.exception("morning brief tick failed")
        try:
            from app.morning import earnings_report

            await asyncio.to_thread(earnings_report.tick)
            earnings_report._state["last_error"] = None
        except Exception as e:  # noqa: BLE001
            earnings_report._state["last_error"] = str(e)
            logger.exception("earnings report tick failed")
        await asyncio.sleep(60)
