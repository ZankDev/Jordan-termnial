"""The real-money leg of the live tick. Runs after the paper accounts, off the same market
snapshots, and does three things — in this order, every tick:

  1. MANAGE   every open real position (works even when disarmed, so nothing is ever
              abandoned): exit ladder on the live bid, or settle at the true outcome;
  2. GUARD    daily loss limit / trade cap / connection / balance;
  3. ENTER    at most `max_concurrent` positions, on allowed assets only, one attempt
              per session, with a price ceiling on the fill.

The strategy, gates and exit ladder are exactly the paper engine's; the parameter set is
the champion's (or the source defaults), with take-profit clipped to the user's +25%…+50%
window. A failure anywhere here is contained — it can never break the paper engine.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any

from app.optimizer.search import params_hash
from app.polymarket import clob, gamma, notify, real_trading as rt, spot
from app.polymarket.registry import get as get_strategy

logger = logging.getLogger(__name__)

SETTLE_GRACE = 4
OUTCOME_WAIT_SECS = 240      # wait this long for Polymarket's own resolution before estimating from spot
DUST_SHARES = 0.02           # an unsold remainder smaller than this is rounding dust, not a position
SELL_RETRY_LIMIT = 4        # exit attempts per position before it is left to settle at expiry

_attempts: dict[str, int] = {}            # buy attempts per session (a killed FAK order may retry, up to MAX_ATTEMPTS)
MAX_ATTEMPTS = 3
_sell_fails: dict[int, int] = {}


def real_params(settings: dict[str, Any]) -> dict[str, Any]:
    from app.polymarket import pm_promotion

    strat = get_strategy("momo")
    champ = pm_promotion.get_champion() if settings.get("params_source") == "champion" else None
    p = {**strat["default_params"](), **(champ["params"] if champ else {})}
    p["tp_pct"] = max(settings["tp_min_pct"], min(settings["tp_max_pct"], float(p["tp_pct"])))
    return p


def run(running: list[dict[str, Any]], snaps: dict[str, Any], traces: dict[str, list], now: float, quote, ticks: dict[str, list]) -> None:
    """quote(token, side) is the live engine's cached CLOB price reader."""
    if not rt.is_connected():
        return
    settings = rt.get_settings()
    params = real_params(settings)
    strat = get_strategy("momo")

    for row in rt.list_trades(open_only=True):
        try:
            _manage(row, snaps, traces, now, quote, ticks, strat, params)
        except Exception:  # noqa: BLE001
            logger.exception("real position management failed for trade %s", row["id"])

    ok, _why = rt.can_trade_now()
    if not ok:
        return
    try:
        _enter(settings, params, strat, running, snaps, now, quote, ticks)
    except Exception:  # noqa: BLE001
        logger.exception("real entry scan failed")


# ----------------------------------------------------------------------------- manage
def _manage(row: dict[str, Any], snaps, traces, now, quote, ticks, strat, params) -> None:
    if now >= row["end_ts"] + SETTLE_GRACE:
        _settle(row, traces, now, ticks, params)
        return
    snap = snaps.get(row["session_slug"])
    if snap is None or _sell_fails.get(row["id"], 0) >= SELL_RETRY_LIMIT:
        return
    bid = quote(row["token_id"], "SELL")
    if bid is None:
        return
    pos = {"side": row["side"], "entry_price": row["entry_price"], "peak_pct": row.get("peak_pct")}
    mark = dataclasses.replace(snap, up_price=bid if row["side"] == "up" else 1 - bid,
                               down_price=bid if row["side"] == "down" else 1 - bid)
    reason = strat["exit_check"](pos, mark, params)
    if pos.get("peak_pct") != row.get("peak_pct"):
        rt.set_peak(row["id"], pos.get("peak_pct"))
    if not reason:
        return
    _exit(row, reason, bid, now, ticks, params, pos.get("peak_pct"), snap.spot_trace)


def _exit(row, reason, bid, now, ticks, params, peak, spot_trace) -> None:
    # first try with a price floor a little under the bid; the second attempt takes any price —
    # for an exit, getting out beats getting the last cent.
    fill = rt.sell(row["token_id"], row["shares"], min_price=max(0.01, bid - 0.08))
    if not fill.get("ok") or not fill.get("shares"):
        fill = rt.sell(row["token_id"], row["shares"], min_price=None)
    if not fill.get("ok") or not fill.get("shares"):
        _sell_fails[row["id"]] = _sell_fails.get(row["id"], 0) + 1
        logger.warning("real exit failed for trade %s (%s): %s", row["id"], reason, fill.get("error"))
        return
    sold, got = fill["shares"], fill["proceeds"]
    # Polymarket sells in 0.01-share units, so a buy of 1.754386 shares can only sell 1.75; the sub-cent
    # remainder is dust, not a position — fold it into this close instead of leaving a phantom open row.
    if sold < row["shares"] - 1e-6 and row["shares"] - sold >= DUST_SHARES:
        # partial fill: keep the remainder open and book the sold part as its own closed row
        cost_sold = row["cost"] * (sold / row["shares"])
        rt.reduce_position(row["id"], row["shares"] - sold, row["cost"] - cost_sold)
        rt.record_partial(row, sold, cost_sold, got, reason)
        return
    closed = rt.close_trade(row["id"], fill["avg_price"] or bid, got, reason, peak_pct=peak)
    _announce_exit(closed, ticks, spot_trace, params)


def _announce_exit(closed, ticks, spot_trace, params) -> None:
    logger.info("REAL CLOSE %s %s: %s pnl=%.3f", closed["session_slug"], closed["side"], closed["exit_reason"], closed["pnl"])
    notify.notify_real_exit(closed, list(ticks.get(closed["session_slug"], [])),
                            [(t, p) for t, p in (spot_trace or []) if t >= closed["start_ts"] - 2], params)


def _settle(row, traces, now, ticks, params) -> None:
    """Settle at Polymarket's own resolution; fall back to an estimate from the spot path
    only after OUTCOME_WAIT_SECS. The P&L is flagged as an estimate either way — no fill
    exists for it (winning shares are redeemed by Polymarket)."""
    up_won = None
    try:
        up_won = gamma.session_outcome(row["session_slug"])
    except Exception:  # noqa: BLE001
        pass
    note = "resolved per Polymarket"
    if up_won is None:
        if now < row["end_ts"] + OUTCOME_WAIT_SECS:
            return
        trace = traces.get(row["asset"]) or spot.recent_1s(row["asset"])
        final = spot.strike_from(trace, row["end_ts"]) if trace else None
        if final is None or row.get("strike") is None:
            if now < row["end_ts"] + 900:
                return
            note = "outcome unreadable — check the position on Polymarket"
        else:
            up_won, note = final >= row["strike"], "estimated from spot (Polymarket resolution not yet readable)"
    if up_won is None:
        closed = rt.close_trade(row["id"], 0.0, 0.0, "settlement-unknown", estimate=True, note=note)
    else:
        won = up_won if row["side"] == "up" else not up_won
        closed = rt.close_trade(row["id"], 1.0 if won else 0.0, row["shares"] if won else 0.0, "settlement",
                                estimate=True, note=note)
    _announce_exit(closed, ticks, spot.recent_1s(row["asset"]), params)


# ----------------------------------------------------------------------------- enter
def _enter(settings, params, strat, running, snaps, now, quote, ticks) -> None:
    open_rows = rt.list_trades(open_only=True)
    if len(open_rows) >= settings["max_concurrent"]:
        return
    held = {r["session_slug"] for r in open_rows}
    lo, hi = params["momo_min_secs"], params["momo_secs"]
    stake = float(settings["stake_usd"])
    for sess in sorted(running, key=lambda s: s["end_ts"]):
        if len(open_rows) >= settings["max_concurrent"]:
            return
        slug = sess["slug"]
        remaining = sess["end_ts"] - now
        if sess["asset"] not in settings["assets"] or slug in held or _attempts.get(slug, 0) >= MAX_ATTEMPTS or not (lo <= remaining <= hi):
            continue
        snap = snaps.get(slug)
        if snap is None or snap.up_price is None or snap.down_price is None:
            continue
        if snap.up_price + snap.down_price - 1 > settings["max_overround"]:
            continue
        sig = strat["gate_check"](snap, params)
        if not sig.enter:
            continue
        bal = rt.balance()
        if bal is not None and bal < stake + 0.05:
            rt.disarm(f"balance ${bal:.2f} is below the ${stake:.2f} stake")
            return
        _attempts[slug] = _attempts.get(slug, 0) + 1
        if len(_attempts) > 400:
            _attempts.clear()
        token = sess["up_token_id"] if sig.side == "up" else sess["down_token_id"]
        ceiling = min(0.99, float(sig.price) + settings["max_slippage"])
        try:  # fresh book right before the order, so a no-fill can be explained from the log
            pre = clob.book_top(token)
        except Exception:  # noqa: BLE001
            pre = None
        fill = rt.buy(token, stake, ceiling)
        if not fill.get("ok") or not fill.get("shares"):
            try:
                post = clob.book_top(token)
            except Exception:  # noqa: BLE001
                post = None
            logger.warning("REAL buy did not fill %s %s signal %.3f ceiling %.3f (book before %s, after %s): %s",
                           slug, sig.side, float(sig.price), ceiling, pre, post, fill.get("error"))
            continue
        tid = rt.open_trade(sess, sig.side, token, fill, snap.strike, sig.features.get("mc_flip"), params_hash(params))
        trade = rt.get_trade(tid)
        logger.info("REAL ENTER %s %s avg %.3f spent %.2f", slug, sig.side, fill["avg_price"], fill["spent"])
        notify.notify_real_entry(sess, trade, sig.features, list(ticks.get(slug, [])), snap.spot_trace, snap.strike, params)
        open_rows.append(trade)
        held.add(slug)


# ----------------------------------------------------------------------------- panic
def panic() -> dict[str, Any]:
    """Disarm and sell every open real position at market. Used by the PANIC button."""
    rt.disarm("PANIC button pressed")
    results = []
    for row in rt.list_trades(open_only=True):
        if time.time() >= row["end_ts"]:
            results.append({"id": row["id"], "result": "session over — settles automatically"})
            continue
        fill = rt.sell(row["token_id"], row["shares"], min_price=None)
        if fill.get("ok") and fill.get("shares"):
            closed = rt.close_trade(row["id"], fill["avg_price"] or 0.0, fill["proceeds"], "panic",
                                    note="closed by the PANIC button")
            _announce_exit(closed, [], [], real_params(rt.get_settings()))
            results.append({"id": row["id"], "result": "sold", "pnl": closed["pnl"]})
        else:
            results.append({"id": row["id"], "result": "sell failed", "error": fill.get("error")})
    return {"closed": results}
