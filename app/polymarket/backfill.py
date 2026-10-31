"""Backfill of resolved 5-minute buzzer sessions — the training corpus.

Two modes, run by the backfill loop (see trainer_service.py):
  * recent: pages newest-first until it reaches sessions already cached, so the
    corpus keeps pace with live trading (~70 new sessions an hour across assets).
  * deep:   walks BACKWARD through each series' history with a moving date cursor
    (Gamma rejects large offsets), a bounded batch per call, so the corpus grows
    every cycle — more sessions means more trades per fold and a training signal
    that means something.

Per session: the 1-second Binance trace for the window (strike, final and the
intra-session path all come from ONE call), the CLOB Up-token price line, and the
coin's realised vol going into the session. Resolution is the market's own rule
(Up if spot at end >= spot at start), overridden by the CLOB's own settled price
when that is unambiguous — Polymarket resolves on Chainlink, not Binance.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from app import db
from app.polymarket import clob, gamma, spot, store
from app.polymarket.asset_map import binance_symbol

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
FETCH_WORKERS = 10
DEEP_DAYS = 30  # how far back the deep walk goes (Binance keeps 1s klines this far; bounds DB growth)


def _resolve_and_store(sess: dict) -> bool:
    asset = sess["asset"]
    session_id = store.upsert_session(sess)
    if not binance_symbol(asset):
        return False  # no spot source — keep the shell (coverage visibility), never "resolved"
    start, end = sess["start_ts"], sess["end_ts"]
    trace = spot.session_trace(asset, start, end)
    strike = spot.strike_from(trace, start) if trace else None
    final = spot.strike_from(trace, end) if trace else None
    if strike is None or final is None:
        strike, final = spot.price_at(asset, start), spot.price_at(asset, end)
    if strike is None or final is None:
        return False
    try:
        ticks = clob.prices_history(sess["up_token_id"], start, end, fidelity=1)
    except Exception:  # noqa: BLE001
        logger.warning("prices-history failed for %s", sess["slug"])
        return False  # try again on a later pass rather than store a tickless "resolved" row
    resolved_up = final >= strike
    if ticks and ticks[-1][0] >= end - 15:
        p = ticks[-1][1]
        if p >= 0.97:
            resolved_up = True
        elif p <= 0.03:
            resolved_up = False
    sigma = spot.realized_vol_per_min(asset, at_ts=start)
    store.insert_prices(session_id, ticks)
    store.insert_spot_trace(session_id, trace)
    store.set_session_outcome(session_id, strike, final, resolved_up, sigma, n_ticks=len(ticks))
    return True


def _process(pending: list[dict]) -> int:
    stored = 0
    if not pending:
        return 0
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for fut in as_completed([pool.submit(_resolve_and_store, s) for s in pending]):
            try:
                stored += 1 if fut.result() else 0
            except Exception:  # noqa: BLE001
                logger.warning("session backfill failed", exc_info=True)
    return stored


def _unresolved(page: list[dict]) -> list[dict]:
    out = []
    for sess in page:
        existing = store.get_session_by_slug(sess["slug"])
        if not (existing and existing["resolved_up"] is not None):
            out.append(sess)
    return out


def backfill_series(series_slug: str, max_pages: int = 3) -> int:
    """Recent mode: newest-first, stops once a page is already fully cached."""
    stored, offset = 0, 0
    for _ in range(max_pages):
        page = gamma.list_closed_sessions(series_slug, limit=PAGE_SIZE, offset=offset)
        if not page:
            break
        pending = _unresolved(page)
        stored += _process(pending)
        offset += PAGE_SIZE
        if not pending:
            break
    return stored


def backfill_deep(series_slug: str, budget: int = 200) -> dict[str, int | bool]:
    """Deep mode: walk back from the oldest session we hold, up to `budget` new
    sessions this call. The cursor (oldest end_ts reached) persists in meta."""
    key = f"pm_deep_cursor:{series_slug}"
    done_key = f"pm_deep_done:{series_slug}"
    if db.meta_get(done_key) == "1":
        return {"stored": 0, "exhausted": True}
    import time as _time

    cursor = db.meta_get(key)
    if cursor is not None and int(cursor) < _time.time() - DEEP_DAYS * 86400:
        db.meta_set(done_key, "1")
        return {"stored": 0, "exhausted": True}
    if cursor is None:
        with db.get_conn() as conn:
            row = conn.execute("SELECT MIN(end_ts) AS m FROM pm_sessions WHERE series_slug = ?", (series_slug,)).fetchone()
        cursor = str(row["m"]) if row and row["m"] else None
    stored = 0
    while stored < budget:
        page = gamma.list_closed_sessions(series_slug, limit=PAGE_SIZE, end_before_ts=(int(cursor) - 1) if cursor else None)
        if not page:
            db.meta_set(done_key, "1")
            return {"stored": stored, "exhausted": True}
        stored += _process(_unresolved(page))
        new_cursor = min(s["end_ts"] for s in page)
        if cursor is not None and new_cursor >= int(cursor):
            break  # no progress (safety)
        cursor = str(new_cursor)
        db.meta_set(key, cursor)
        if new_cursor < _time.time() - DEEP_DAYS * 86400:
            db.meta_set(done_key, "1")
            return {"stored": stored, "exhausted": True}
    return {"stored": stored, "exhausted": False}


def backfill_all(max_pages_per_series: int = 3) -> dict[str, int]:
    """Recent mode across every discoverable 5m series with a Binance pair."""
    out: dict[str, int] = {}
    for series in gamma.discover_5m_series():
        if not binance_symbol(series["asset"]):
            continue
        try:
            out[series["slug"]] = backfill_series(series["slug"], max_pages_per_series)
        except Exception:  # noqa: BLE001
            logger.warning("backfill failed for series %s", series["slug"], exc_info=True)
            out[series["slug"]] = 0
    return out


def deep_all(budget_per_series: int = 150) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for series in gamma.discover_5m_series():
        if not binance_symbol(series["asset"]):
            continue
        try:
            out[series["slug"]] = backfill_deep(series["slug"], budget_per_series)
        except Exception:  # noqa: BLE001
            logger.warning("deep backfill failed for %s", series["slug"], exc_info=True)
            out[series["slug"]] = {"stored": 0, "exhausted": False}
    return out
