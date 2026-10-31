"""Thin client over Polymarket's public CLOB API (no key needed) — current price
and historical price-history for one outcome token.

Verified live (2026-09-23): GET /prices-history?market=<token_id>&interval=1h&
fidelity=<mins> returns {"history": [{"t": unix_seconds, "p": price_0_to_1}, ...]}
— a plain line series (this is what confirms the "line chart, not candles"
requirement: Polymarket's own price history IS a line series). GET /price?
token_id=&side=BUY|SELL returns the current best price, but 404s with
"No orderbook exists for the requested token id" once a session has resolved and
its orderbook is torn down — callers must treat that as "no live price" rather
than an error.
"""
from __future__ import annotations

from typing import Any

from app.polymarket import http

CLOB_BASE = "https://clob.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
TIMEOUT = 15
# current_price() specifically uses a tighter timeout: it's called once per open
# session (dozens at a time, fanned out over a thread pool — see radar.py/
# live_engine.py), it already has a documented "None = no live read" fallback,
# and a hung/slow request here must fail fast rather than hold a worker thread
# for the full 15s while dozens of others queue behind it.
PRICE_TIMEOUT = 6


def current_price(token_id: str, side: str = "BUY") -> float | None:
    """None if there's no live orderbook (resolved/closed market) or the request
    fails — never raises, since this is polled on a live-trading hot path."""
    try:
        data = http.get_json(f"{CLOB_BASE}/price", {"token_id": token_id, "side": side}, timeout=PRICE_TIMEOUT, retries=1, backoff=0.3)
        price = data.get("price")
        return float(price) if price is not None else None
    except Exception:  # noqa: BLE001
        return None


def prices_history(
    token_id: str, start_ts: int | None = None, end_ts: int | None = None, fidelity: int = 1
) -> list[tuple[int, float]]:
    """[(t, p), ...] ascending by time; with start_ts/end_ts uses the CLOB's own
    range params so a session's exact window is requested directly."""
    params: dict[str, Any] = {"market": token_id, "fidelity": fidelity}
    if start_ts is not None and end_ts is not None:
        params["startTs"] = int(start_ts)
        params["endTs"] = int(end_ts)
    else:
        params["interval"] = "1h"
    data = http.get_json(f"{CLOB_BASE}/prices-history", params, timeout=TIMEOUT, retries=2)
    out = [(int(r["t"]), float(r["p"])) for r in (data.get("history") or []) if "t" in r and "p" in r]
    if start_ts is not None and end_ts is not None:
        out = [(t, p) for t, p in out if start_ts - 30 <= t <= end_ts + 30]
    return sorted(out, key=lambda x: x[0])


def book_top(token_id: str) -> tuple[float | None, float | None, float, float]:
    """(best_bid, best_ask, bid_size, ask_size) from the live order book.

    /price is NOT reliable for these fast 5-minute markets — measured against /book it
    returned quotes 10+ cents away from the real top of book (an "ask" of 0.27 where the
    book's best ask was 0.15), which made paper fills unrealistic and real FAK buys find
    nothing to match. The book is the source of truth for what a taker actually pays/gets."""
    try:
        data = http.get_json(f"{CLOB_BASE}/book", {"token_id": token_id}, timeout=PRICE_TIMEOUT, retries=1, backoff=0.3)
    except Exception:  # noqa: BLE001
        return None, None, 0.0, 0.0
    bids = [(float(x["price"]), float(x["size"])) for x in (data.get("bids") or []) if float(x.get("size", 0)) > 0]
    asks = [(float(x["price"]), float(x["size"])) for x in (data.get("asks") or []) if float(x.get("size", 0)) > 0]
    bid = max(bids, key=lambda x: x[0]) if bids else None
    ask = min(asks, key=lambda x: x[0]) if asks else None
    return (bid[0] if bid else None, ask[0] if ask else None, bid[1] if bid else 0.0, ask[1] if ask else 0.0)
