"""Real on-chain wallet copytrader — paper-shadows a tracked Polymarket wallet's
REAL trades, sourced from data-api.polymarket.com (verified live, no key, no RPC
needed — see the plan's decision #0.1), not by placing any real order.

BUY  -> open a shadow position at the wallet's own fill price, sized at the
        account's flat stake_usd (not proportional to the wallet's real size —
        "flat stake per shadowed trade to start", proportional sizing is a
        documented v2 refinement).
SELL -> close the OLDEST matching open shadow position (same market slug +
        outcome) at the wallet's own sell price — mirrors their real exit
        exactly, no slippage modeled (paper, not live execution).
A shadow position the wallet never sells (holds to resolution) stays open
indefinitely here — this copytrader only settles on the wallet's own observed
exit, not by independently resolving arbitrary Polymarket markets (that's the
buzzer-specific pm_sessions table's job, not this general-purpose one).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.polymarket import store

logger = logging.getLogger(__name__)

DATA_API_BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}


def fetch_wallet_trades(wallet_address: str, limit: int = 100) -> list[dict[str, Any]]:
    """Most recent `limit` real trades for a wallet, oldest first (the Data API
    itself returns newest first)."""
    r = requests.get(f"{DATA_API_BASE}/trades", params={"user": wallet_address, "limit": limit}, headers=UA, timeout=20)
    r.raise_for_status()
    rows = r.json() or []
    return sorted(rows, key=lambda t: t["timestamp"])


def poll_wallet(account: dict[str, Any]) -> int:
    """One poll cycle for one copy account. Returns the number of shadow fills
    (opens + closes) it made. `account["last_tx_hash"]` is used as a high-water
    mark holding the last-processed trade's unix timestamp (as a string) — safe
    to re-poll an overlapping window since nothing at-or-before it is reprocessed."""
    cursor = int(account["last_tx_hash"]) if account.get("last_tx_hash") else 0
    try:
        trades = fetch_wallet_trades(account["wallet_address"])
    except Exception:  # noqa: BLE001
        logger.warning("copytrade poll failed for account %s", account["id"], exc_info=True)
        return 0

    new_trades = [t for t in trades if t["timestamp"] > cursor]
    if not new_trades:
        return 0

    fills = 0
    for t in new_trades:
        try:
            fills += shadow_trade(account, t)
        except Exception:  # noqa: BLE001
            logger.exception("shadow_trade failed for account %s tx %s", account["id"], t.get("transactionHash"))
    store.set_copy_last_tx(account["id"], str(max(t["timestamp"] for t in new_trades)))
    return fills


def shadow_trade(account: dict[str, Any], trade: dict[str, Any]) -> int:
    slug, outcome = trade["slug"], trade["outcome"]
    side = trade["side"]
    price = float(trade["price"])

    if side == "BUY":
        shares = account["stake_usd"] / price if price > 0 else 0
        if shares <= 0:
            return 0
        store.open_copy_trade(account["id"], {
            "tx_hash": trade["transactionHash"], "slug": slug, "outcome": outcome, "price": price, "shares": shares,
        })
        return 1

    if side == "SELL":
        open_trade = store.find_open_copy_trade(account["id"], slug, outcome)
        if not open_trade:
            return 0  # the wallet sold a position opened before we started tracking it — nothing to close
        pnl = (price - open_trade["wallet_price"]) * open_trade["shadow_shares"]
        store.close_copy_trade(open_trade["id"], price, pnl)
        # Re-read the balance fresh rather than trust the caller's possibly-stale
        # snapshot — poll_wallet processes several trades per cycle, and multiple
        # closes in one cycle must each add onto the OTHERS' updates, not clobber them.
        current = store.get_copy_account(account["id"])
        store.set_copy_balance(account["id"], round((current["balance"] if current else account["balance"]) + pnl, 4))
        return 1

    return 0
