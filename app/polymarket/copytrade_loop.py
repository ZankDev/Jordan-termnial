"""Background loop polling every live copytrade account's tracked wallet — the
'copytrade' half of the 'polymarket' NOSTRO_LOOPS token (see app/main.py)."""
from __future__ import annotations

import asyncio
import logging

from app.polymarket import copytrade, store

logger = logging.getLogger(__name__)

COPYTRADE_POLL_SECONDS = 45


async def _tick_all() -> None:
    accounts = store.list_live_copy_accounts()
    for account in accounts:
        try:
            await asyncio.to_thread(copytrade.poll_wallet, account)
        except Exception:  # noqa: BLE001
            logger.exception("copytrade tick failed for account %s", account["id"])


async def copytrade_loop() -> None:
    while True:
        try:
            await _tick_all()
        except Exception:  # noqa: BLE001
            logger.exception("copytrade_loop tick failed")
        await asyncio.sleep(COPYTRADE_POLL_SECONDS)
