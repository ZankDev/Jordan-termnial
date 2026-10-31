"""Polymarket live paper-trading engine.

One loop, three jobs, sharing a single market read per tick:
  1. RADAR   — a fresh snapshot of every running 5-minute session (share prices,
               spot vs strike, the momo gate verdict) that the UI reads instantly;
  2. RECORDER— the live Up-token price of every in-window session is written to
               pm_session_prices every tick. The historical CLOB feed is ~1 point a
               minute; recording live gives the trainer ~4-second ticks going forward;
  3. ACCOUNTS— each live paper account runs its strategy's real entry gate and exit
               ladder against those snapshots (several positions at once, at most
               MAX_CONCURRENT and MAX_SAME_DIR like the source bot; one attempt per
               session), fills at the live ask, exits at the live bid, and settles at
               the true outcome when the session closes. Nothing here ever places a
               real order.

Spot comes from ONE Binance 1-second fetch per asset per tick (spot.recent_1s), so the
momentum/reversal features see a dense 1 Hz trace exactly like the source JS bot.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.polymarket import clob, gamma, notify, spot, store
from app.polymarket.asset_map import binance_symbol
from app.polymarket.registry import get as get_strategy
from app.polymarket.strategy_base import BuzzerSession

logger = logging.getLogger(__name__)

LIVE_POLL_SECONDS = 4
MAX_CONCURRENT = 2       # open positions per account (source bot: BUZZER_MOMO_MAX_CONCURRENT)
MAX_SAME_DIR = 1         # ...of which at most this many on the same favoured side
QUOTE_TTL = 2.5
PAPER_ENTRY_SLIPPAGE = 0.03   # same tolerance the real engine uses (max_slippage): a paper entry never pays more than this over the signal
RECORD_WINDOW_SECS = 250  # record/quote sessions once they are this close to their end
SETTLE_GRACE = 4         # seconds after end_ts before a position is settled
# Thin books (BNB/DOGE/XRP at times) quote an ask far above the bid — buying at the ask and
# being marked at the bid turns the spread itself into a "stop-loss". up_ask + down_ask - 1
# is the round-trip cost of the market right now; refuse entries when it is wide.
MAX_OVERROUND = 0.05

_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="pm-live")
_lock = threading.Lock()
_sessions_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_quote_cache: dict[tuple[str, str], tuple[float, float | None]] = {}
_ticks: dict[str, list[tuple[float, float, float]]] = {}      # slug -> [(t, up, down)] (chart material)
_pending_settle_since: dict[str, float] = {}

# What the UI / radar reads: refreshed every tick.
LIVE: dict[str, Any] = {"ts": 0.0, "rows": [], "n_upcoming": 0, "error": None, "tick_ms": None,
                        "recorded": 0, "accounts_live": 0}


# --------------------------------------------------------------------------- market reads
def _quote(token: str, side: str = "BUY") -> float | None:
    key = (token, side)
    now = time.time()
    hit = _quote_cache.get(key)
    if hit and now - hit[0] < QUOTE_TTL:
        return hit[1]
    # Real top of book: BUY = what a taker pays (best ask), SELL = what a taker gets (best bid).
    # /price was found unreliable on these markets (see clob.book_top). One /book call serves both sides.
    other = _quote_cache.get((token, "BOOK"))
    if other and now - other[0] < QUOTE_TTL:
        bid, ask = other[1]
    else:
        bid, ask, _bs, _as = clob.book_top(token)
        _quote_cache[(token, "BOOK")] = (now, (bid, ask))
    price = ask if side == "BUY" else bid
    _quote_cache[key] = (now, price)
    if len(_quote_cache) > 600:
        for k in [k for k, v in _quote_cache.items() if now - v[0] > 60]:
            _quote_cache.pop(k, None)
    return price


_sessions_refreshing = False


def _refresh_sessions() -> None:
    global _sessions_cache, _sessions_refreshing
    try:
        _sessions_cache = (time.time(), gamma.list_active_sessions())
    except Exception:  # noqa: BLE001
        logger.warning("gamma session list failed", exc_info=True)
    finally:
        _sessions_refreshing = False


def _sessions() -> list[dict[str, Any]]:
    """The session list, refreshed in the background: Gamma can be slow, and a tick must
    never wait on it (it would freeze every paper account). Only the very first call blocks."""
    global _sessions_refreshing
    now = time.time()
    if not _sessions_cache[1]:
        _refresh_sessions()
    elif now - _sessions_cache[0] > 8 and not _sessions_refreshing:
        _sessions_refreshing = True
        _pool.submit(_refresh_sessions)
    return _sessions_cache[1]


def _snapshot(sess: dict[str, Any], traces: dict[str, list], now: float, with_quotes: bool) -> BuzzerSession | None:
    trace_all = traces.get(sess["asset"]) or []
    if not trace_all:
        return None
    strike = spot.strike_from(trace_all, sess["start_ts"])
    if strike is None:
        return None
    trace = [(t, p) for t, p in trace_all if sess["start_ts"] - 2 <= t <= now]
    if not trace:
        return None
    up = down = None
    if with_quotes:
        up, down = _quote(sess["up_token_id"]), _quote(sess["down_token_id"])
    return BuzzerSession(
        slug=sess["slug"], asset=sess["asset"], start_ts=sess["start_ts"], end_ts=sess["end_ts"], now_ts=now,
        strike=strike, spot_now=trace[-1][1], up_price=up, down_price=down,
        sigma_per_min_pct=spot.realized_vol_per_min(sess["asset"]), spot_trace=trace,
    )


def _account_params(account: dict[str, Any], strat: dict[str, Any]) -> dict[str, Any]:
    raw = account.get("strategy_params_json")
    return {**strat["default_params"](), **(json.loads(raw) if raw else {})}


def _fetch_traces(assets: list[str]) -> dict[str, list]:
    """One 1-second trace per asset, in parallel, each bounded — a stuck Binance call
    yields the last good trace (or none) instead of stalling the tick."""
    futs = {a: _pool.submit(spot.recent_1s, a) for a in assets}
    out: dict[str, list] = {}
    for a, f in futs.items():
        try:
            out[a] = f.result(timeout=8)
        except Exception:  # noqa: BLE001
            out[a] = (spot._recent_cache.get(a) or (0, []))[1]
    return out


_loop_running = False


# --------------------------------------------------------------------------- one tick
def _tick() -> None:
    t0 = time.time()
    now = time.time()
    sessions = _sessions()
    running = [s for s in sessions if s["start_ts"] <= now < s["end_ts"] + SETTLE_GRACE and binance_symbol(s["asset"])]
    upcoming = [s for s in sessions if s["start_ts"] > now]
    assets = sorted({s["asset"] for s in running})

    # positions may sit in sessions that already left the active list — keep their assets' traces fresh too
    live_accounts = store.list_live_pm_accounts()
    for a in live_accounts:
        for p in a["open_positions"]:
            if p["asset"] not in assets and binance_symbol(p["asset"]):
                assets.append(p["asset"])

    traces = _fetch_traces(assets)
    in_window = [s for s in running if 0 < s["end_ts"] - now <= RECORD_WINDOW_SECS]

    snaps: dict[str, BuzzerSession] = {}
    futures = {s["slug"]: _pool.submit(_snapshot, s, traces, now, s in in_window) for s in running}
    for slug, fut in futures.items():
        try:
            snap = fut.result(timeout=10)
        except Exception:  # noqa: BLE001
            logger.warning("snapshot failed/timed out for %s", slug)
            continue
        if snap is not None:
            snaps[slug] = snap

    # RECORDER: live Up price per in-window session -> the training corpus
    recorded = 0
    for s in in_window:
        snap = snaps.get(s["slug"])
        if snap is None or snap.up_price is None:
            continue
        tick = _ticks.setdefault(s["slug"], [])
        tick.append((now, snap.up_price, snap.down_price if snap.down_price is not None else 1 - snap.up_price))
        try:
            sid = store.upsert_session(s)
            store.insert_prices(sid, [(int(now), snap.up_price)])
            recorded += 1
        except Exception:  # noqa: BLE001
            logger.warning("tick record failed for %s", s["slug"], exc_info=True)
    if len(_ticks) > 40:
        for slug in sorted(_ticks, key=lambda k: _ticks[k][-1][0] if _ticks[k] else 0)[: len(_ticks) - 40]:
            _ticks.pop(slug, None)

    # RADAR rows (gate verdict under the champion's params, or the strategy defaults)
    from app.polymarket import pm_promotion

    strat = get_strategy("momo")
    champ = pm_promotion.get_champion()
    ref_params = champ["params"] if champ else strat["default_params"]()
    held_by: dict[str, list[str]] = {}
    for a in live_accounts:
        for p in a["open_positions"]:
            held_by.setdefault(p["session_slug"], []).append(a["name"].replace("AUTO · ", ""))
    rows = []
    for s in sorted(running, key=lambda x: x["end_ts"]):
        snap = snaps.get(s["slug"])
        row: dict[str, Any] = {"slug": s["slug"], "asset": s["asset"], "end_ts": s["end_ts"], "start_ts": s["start_ts"],
                               "remaining_secs": max(0, round(s["end_ts"] - now)), "held_by": held_by.get(s["slug"], [])}
        if snap is None:
            row["gate_status"] = "no-spot-read"
        else:
            sig = strat["gate_check"](snap, ref_params)
            wide = snap.up_price is not None and snap.down_price is not None and snap.up_price + snap.down_price - 1 > MAX_OVERROUND
            row.update(up_price=snap.up_price, down_price=snap.down_price, move_pct=round(snap.move_pct, 4),
                       strike=snap.strike, spot=snap.spot_now, features=sig.features,
                       gate_status=("skip:wide-spread" if (sig.enter and wide) else ("entered:" + (sig.side or "")) if sig.enter else f"skip:{sig.skip_reason}"))
        rows.append(row)

    # ACCOUNTS
    for account in live_accounts:
        try:
            _run_account(account, sessions, running, snaps, traces, now)
        except Exception:  # noqa: BLE001
            logger.exception("polymarket live tick failed for account %s", account["id"])

    # REAL account leg (no-op unless a wallet is connected; entries need it ARMED). Fully contained.
    try:
        from app.polymarket import real_engine

        real_engine.run(running, snaps, traces, now, _quote, _ticks)
    except Exception:  # noqa: BLE001
        logger.exception("real-trading leg failed")

    with _lock:
        LIVE.update(ts=now, rows=rows, n_upcoming=len(upcoming), error=None, tick_ms=round((time.time() - t0) * 1000),
                    recorded=recorded, accounts_live=len(live_accounts))


# --------------------------------------------------------------------------- accounts
def _run_account(account: dict[str, Any], sessions: list[dict[str, Any]], running: list[dict[str, Any]],
                 snaps: dict[str, BuzzerSession], traces: dict[str, list], now: float) -> None:
    if not account.get("strategy"):
        return  # a live account with no strategy assigned has nothing to run
    strat = get_strategy(account["strategy"])
    params = _account_params(account, strat)
    positions = list(account["open_positions"])
    changed = False

    # ---- manage open positions
    for pos in list(positions):
        end_ts = pos["end_ts"]
        if now >= end_ts + SETTLE_GRACE:
            if _settle(account, pos, traces, now, params):
                positions.remove(pos)
                changed = True
            continue
        snap = snaps.get(pos["session_slug"])
        if snap is None:
            continue
        token = pos["up_token_id"] if pos["side"] == "up" else pos["down_token_id"]
        bid = _quote(token, "SELL")
        if bid is None:
            continue
        mark = dataclasses.replace(snap, up_price=bid if pos["side"] == "up" else 1 - bid,
                                   down_price=bid if pos["side"] == "down" else 1 - bid)
        reason = strat["exit_check"](pos, mark, params)
        if reason:
            _close(account, pos, bid, reason, now, params)
            positions.remove(pos)
            changed = True
        else:
            changed = True  # peak_pct may have moved — persist it

    # ---- look for entries
    if len(positions) < MAX_CONCURRENT:
        lo, hi = params["momo_min_secs"], params["momo_secs"]
        held = {p["session_slug"] for p in positions}
        for sess in sorted(running, key=lambda s: s["end_ts"]):
            if len(positions) >= MAX_CONCURRENT:
                break
            remaining = sess["end_ts"] - now
            if not (lo <= remaining <= hi) or sess["slug"] in held:
                continue
            snap = snaps.get(sess["slug"])
            if snap is None or snap.up_price is None or snap.down_price is None:
                continue
            sig = strat["gate_check"](snap, params)
            if not sig.enter:
                continue
            if snap.up_price + snap.down_price - 1 > MAX_OVERROUND:
                continue  # wide spread: the book is too thin to trade honestly
            if sum(1 for p in positions if p["side"] == sig.side) >= MAX_SAME_DIR:
                continue
            if store.account_has_traded(account["id"], sess["slug"]):
                continue
            # A paper fill must be one the real account could get: re-read the book right now (the snapshot can be
            # several seconds old and these markets run away in seconds) and pay the real ask, or skip the entry.
            token = sess["up_token_id"] if sig.side == "up" else sess["down_token_id"]
            try:
                fresh_ask = clob.book_top(token)[1]
            except Exception:  # noqa: BLE001
                fresh_ask = None
            if fresh_ask is None or fresh_ask > float(sig.price) + PAPER_ENTRY_SLIPPAGE:
                continue
            entry_price = min(0.99, fresh_ask)
            stake = float(account.get("stake_usd") or 5.0)
            pos = {
                "session_slug": sess["slug"], "asset": sess["asset"], "side": sig.side, "entry_price": entry_price,
                "shares": round(stake / entry_price, 4), "entry_time": _now_iso(), "entry_ts": now,
                "start_ts": sess["start_ts"], "end_ts": sess["end_ts"], "strike": snap.strike,
                "up_token_id": sess["up_token_id"], "down_token_id": sess["down_token_id"], "peak_pct": None,
                "flip_prob": sig.features.get("mc_flip"),
            }
            positions.append(pos)
            held.add(sess["slug"])
            changed = True
            logger.info("polymarket %s ENTER %s %s @ %.3f", account["name"], sess["slug"], sig.side, entry_price)
            notify.notify_entry(account, sess, pos, sig.features, list(_ticks.get(sess["slug"], [])),
                                snap.spot_trace, snap.strike, params)

    if changed:
        store.set_pm_open_positions(account["id"], positions)


def _settle(account: dict[str, Any], pos: dict[str, Any], traces: dict[str, list], now: float, params: dict[str, Any]) -> bool:
    """Settle at the session's true outcome. Returns True once closed; False = try
    again next tick (no final price readable yet; gives up after 90 s and marks 0.5)."""
    trace = traces.get(pos["asset"]) or spot.recent_1s(pos["asset"])
    final = spot.strike_from(trace, pos["end_ts"]) if trace else None
    if final is None or trace[0][0] > pos["end_ts"]:
        final = spot.price_at(pos["asset"], int(pos["end_ts"]))
    first = _pending_settle_since.setdefault(pos["session_slug"] + str(account["id"]), now)
    if final is None:
        if now - first < 90:
            return False
        _close(account, pos, 0.5, "settlement-unreadable", now, params)
        return True
    won = (final >= pos["strike"]) if pos["side"] == "up" else (final < pos["strike"])
    _close(account, pos, 1.0 if won else 0.0, "settlement", now, params)
    _pending_settle_since.pop(pos["session_slug"] + str(account["id"]), None)
    return True


def _close(account: dict[str, Any], pos: dict[str, Any], exit_price: float, reason: str, now: float, params: dict[str, Any]) -> None:
    fresh = store.get_pm_account(account["id"]) or account
    pnl = (exit_price - pos["entry_price"]) * pos["shares"]
    new_balance = round(fresh["balance"] + pnl, 4)
    trade = {
        "session_slug": pos["session_slug"], "asset": pos["asset"], "side": pos["side"], "entry_time": pos["entry_time"],
        "exit_time": _now_iso(), "entry_price": pos["entry_price"], "exit_price": exit_price, "shares": pos["shares"],
        "pnl": pnl, "exit_reason": reason, "flip_prob": pos.get("flip_prob"), "peak_pct": pos.get("peak_pct"),
    }
    store.record_pm_trade(account["id"], trade, new_balance)
    if not fresh.get("is_auto"):
        store.archive_pm_account_if_burned(account["id"], new_balance, fresh["starting_balance"])
    logger.info("polymarket %s CLOSE %s %s: %s pnl=%.3f", account["name"], pos["session_slug"], pos["side"], reason, pnl)
    trace = (spot.recent_1s(pos["asset"]) or [])
    notify.notify_exit(
        {**account, "balance": fresh["balance"]}, pos["session_slug"], pos["asset"],
        {**trade, "entry_ts": pos.get("entry_ts"), "exit_ts": min(now, pos["end_ts"])},
        list(_ticks.get(pos["session_slug"], [])), [(t, p) for t, p in trace if t >= pos["start_ts"] - 2],
        pos.get("strike"), pos["start_ts"], pos["end_ts"], params,
    )


def _now_iso() -> str:
    import datetime as dt

    return dt.datetime.now(dt.timezone.utc).isoformat()


# --------------------------------------------------------------------------- public helpers
def live_view() -> dict[str, Any]:
    """The radar's data: the last tick's rows (computed on demand if the loop has
    not produced one recently, e.g. the engine is not running in this process)."""
    with _lock:
        fresh = time.time() - LIVE["ts"] < 20
    if not fresh and not _loop_running:
        try:
            _tick_readonly()
        except Exception:  # noqa: BLE001
            logger.warning("radar on-demand tick failed", exc_info=True)
    with _lock:
        return dict(LIVE)


def _tick_readonly() -> None:
    """A tick without touching accounts or the recorder — used when the loop is off."""
    now = time.time()
    running = [s for s in _sessions() if s["start_ts"] <= now < s["end_ts"] + SETTLE_GRACE and binance_symbol(s["asset"])]
    assets = sorted({s["asset"] for s in running})
    traces = _fetch_traces(assets)
    from app.polymarket import pm_promotion

    strat = get_strategy("momo")
    champ = pm_promotion.get_champion()
    params = champ["params"] if champ else strat["default_params"]()
    futs = {s["slug"]: _pool.submit(_snapshot, s, traces, now, 0 < s["end_ts"] - now <= RECORD_WINDOW_SECS) for s in running}
    rows = []
    for s in sorted(running, key=lambda x: x["end_ts"]):
        snap = futs[s["slug"]].result()
        row = {"slug": s["slug"], "asset": s["asset"], "end_ts": s["end_ts"], "start_ts": s["start_ts"],
               "remaining_secs": max(0, round(s["end_ts"] - now)), "held_by": []}
        if snap is None:
            row["gate_status"] = "no-spot-read"
        else:
            sig = strat["gate_check"](snap, params)
            row.update(up_price=snap.up_price, down_price=snap.down_price, move_pct=round(snap.move_pct, 4),
                       strike=snap.strike, spot=snap.spot_now, features=sig.features,
                       gate_status=("entered:" + (sig.side or "")) if sig.enter else f"skip:{sig.skip_reason}")
        rows.append(row)
    with _lock:
        LIVE.update(ts=now, rows=rows, error=None)


async def live_loop() -> None:
    global _loop_running
    _loop_running = True
    while True:
        started = time.time()
        try:
            await asyncio.to_thread(_tick)
        except Exception as e:  # noqa: BLE001
            logger.exception("polymarket live_loop tick failed")
            with _lock:
                LIVE["error"] = str(e)
        await asyncio.sleep(max(0.5, LIVE_POLL_SECONDS - (time.time() - started)))
