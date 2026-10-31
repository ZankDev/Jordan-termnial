"""Background loop that trades 'live' demo accounts forward in real time.

This is deliberately NOT a backtest. Each tick, for every account with
status == 'live', it asks: "if I ran this strategy's normal batch backtest
through right now, would a trade have just started on the very last bar?" —
if so, that's a fresh signal and the account opens a position. An open
position is checked against the newest bar each tick for a stop/target hit.
None of the 10 strategies need to be rewritten into a streaming form; this
just reuses their existing generate_trades(df, params) as-is.

For 'apex_eval' accounts, each tick also runs the Apex intraday-trailing
drawdown check (app.accounts.apex_rules) against current mark-to-market
equity, and force-flattens any open position at 4:59pm ET regardless of what
the strategy itself would do — Apex's own daily flatten rule.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import time
from typing import Any

import pandas as pd

from app import db
from app.accounts import apex_rules, pnl
from app.backtest.engine import CONTRACT_MULTIPLIER, DEFAULT_COMMISSION_PER_TRADE, ENTRY_SLIPPAGE_TICKS, TICK_SIZE, round_to_tick
from app.backtest.sizing import LEGACY_MODE, config_for, size_position
from app.data import yfinance_source
from app.notify import chart_overlays, chart_render, telegram_bot, traderspost
from app.strategies import registry

logger = logging.getLogger(__name__)

LIVE_POLL_SECONDS = 60
LIVE_FRESHNESS_SECONDS = 90  # live accounts need fresher data than the default 1h cache
TELEGRAM_TIMEFRAME_LABEL = "5m"  # accounts have no timeframe column; live_engine always trades 5m bars

# Tail-risk backstop, independent of the normal stop/target check: if a position's
# mark-to-market loss ever exceeds this multiple of what its own stop was sized to
# risk, force-flatten at the real market price immediately rather than trusting the
# bar-based stop check to catch it. This is what would have capped a position like
# "SL 26.25pts away, -$2,725 unrealized" (5x its intended risk) instead of leaving it
# open and getting worse.
EMERGENCY_STOP_RISK_MULTIPLE = 1.5

# Refuse to open a new position whose entry-to-stop dollar risk exceeds this fraction
# of the account's current balance — a strategy with an unusually wide stop shouldn't
# get to risk more of the account than a normal trade would, no matter what its
# backtested edge looks like.
MAX_RISK_PCT_OF_BALANCE = 0.02

_state: dict[str, Any] = {
    "last_tick_at": None,
    "last_error": None,
    "account_errors": {},  # account_id -> last error message
}


def _finalize_exit_price(
    exit_reason: str, side: str, stop: float, target: float, bar_close: float, bar_extreme: float, symbol: str
) -> float:
    tick = TICK_SIZE.get(symbol.upper(), 0.25)
    if exit_reason in ("stop", "emergency_stop"):
        one_tick_fill = stop + (-tick if side == "long" else tick)
        # A real stop order fills at the next available price, which in a fast move
        # or gap can be well worse than one tick past the configured level — use the
        # worse of {stop + 1 tick, the bar's actual worst print} so a big single-bar
        # move doesn't get simulated as a clean, unrealistically cheap stop-out.
        raw = min(one_tick_fill, bar_extreme) if side == "long" else max(one_tick_fill, bar_extreme)
    elif exit_reason == "target":
        raw = target
    else:  # eod_flatten
        raw = bar_close
    return round_to_tick(raw, symbol)


CATCHUP_BARS = 3  # a signal on one of the last few bars is still taken if the tick that would have caught it was late


def _pick_entry(trades, account: dict[str, Any], last_bar_ts, last_bar):
    """The entry the live engine should take now: (trade, was_late).

    The engine only wakes once per poll and a full pass over every live account can take longer than a 5-minute bar
    (or the server was restarting), so a rare, once-a-day signal used to be lost whenever its exact bar was skipped —
    which is why the session strategies (NY-open / ORB / order-block) almost never traded live. Now any entry that
    appeared on a bar not yet seen by the previous tick, at most CATCHUP_BARS old, is taken — provided the market has
    not already run away from it (price must still be between the stop and half-way to the target)."""
    if not trades:
        return None, False
    prev = account.get("last_bar_time")
    prev_ts = pd.Timestamp(prev) if prev else None
    horizon = last_bar_ts - pd.Timedelta(minutes=5 * CATCHUP_BARS)
    fresh = [t for t in trades if t.entry_time == last_bar_ts]
    if fresh:
        return max(fresh, key=lambda t: t.entry_time), False
    if prev_ts is None:
        return None, False
    late = [t for t in trades if prev_ts < t.entry_time < last_bar_ts and t.entry_time >= horizon]
    if not late:
        return None, False
    t = max(late, key=lambda t: t.entry_time)
    px = float(last_bar["close"])
    if t.side == "long":
        ok = t.stop_price < px < t.entry_price + 0.5 * (t.target_price - t.entry_price)
    else:
        ok = t.entry_price - 0.5 * (t.entry_price - t.target_price) < px < t.stop_price
    return (t, True) if ok else (None, False)


def _process_account(account: dict[str, Any]) -> None:
    symbol = account["symbol"]
    strategy = registry.get(account["strategy"])
    params = json.loads(account["strategy_params_json"])

    df = yfinance_source.fetch(symbol, "5m", max_age_seconds=LIVE_FRESHNESS_SECONDS)
    if df.empty:
        return

    last_bar = df.iloc[-1]
    last_bar_ts = df.index[-1]
    last_bar_time = last_bar_ts.isoformat()
    # Yahoo keeps updating THIS SAME bar's OHLC in place for its whole ~5-minute life
    # before a new timestamp appears, so this is only true once per bar. It gates
    # fresh ENTRIES below (no point re-evaluating the same still-forming bar's signal
    # every 60s) but must never gate managing an already-open position — that has to
    # be checked every tick against the bar's current high/low, otherwise a violent
    # move that happens mid-bar goes completely unmonitored until the next bar
    # appears, by which point the loss can have run far past the configured stop.
    is_new_bar = account.get("last_bar_time") != last_bar_time

    account_id = account["id"]
    is_eval = account["account_type"] == "apex_eval" and account.get("eval_status") == "in_progress"
    must_flatten = is_eval and (last_bar_ts.hour, last_bar_ts.minute) >= (apex_rules.FLATTEN_HOUR_ET, apex_rules.FLATTEN_MINUTE_ET)

    multiplier = CONTRACT_MULTIPLIER.get(symbol.upper(), 20.0)  # 1 mini; legacy sizing and the entry guard
    open_pos = account.get("open_position")
    # dollars per point for the WHOLE open position (qty x contract). A position without a
    # qty key predates sizing and is 1 mini, so existing open trades keep their exact P&L.
    pos_pv = pnl.position_point_value(symbol, open_pos) if open_pos else multiplier
    new_balance = account["balance"]
    telegram_msg_id = account.get("telegram_msg_id")
    just_opened = False
    just_closed_trade: dict[str, Any] | None = None

    # Daily loss/profit circuit breaker: once today's realized P&L crosses either
    # configured threshold, stop opening NEW positions for the rest of the day. An
    # already-open position still manages to its normal stop/target/eod exit below —
    # the breaker only withholds fresh entries, it doesn't force-flatten.
    trading_date = session_date(last_bar_ts)
    daily_pnl = account.get("daily_realized_pnl") or 0.0 if account.get("daily_pnl_date") == trading_date else 0.0
    loss_limit = account.get("daily_loss_limit")
    profit_limit = account.get("daily_profit_limit")
    circuit_tripped = (loss_limit is not None and daily_pnl <= -loss_limit) or (
        profit_limit is not None and daily_pnl >= profit_limit
    )

    if open_pos:
        side = open_pos["side"]
        stop, target = open_pos["stop_price"], open_pos["target_price"]
        bar_close = float(last_bar["close"])
        bar_high, bar_low = float(last_bar["high"]), float(last_bar["low"])

        # Checked on EVERY tick against the bar's current high/low — not gated on
        # is_new_bar — so a move that develops mid-bar gets caught within one poll
        # interval instead of up to ~5 minutes late (see is_new_bar's docstring above).
        hit_stop = bar_low <= stop if side == "long" else bar_high >= stop
        hit_target = bar_high >= target if side == "long" else bar_low <= target

        # Tail-risk backstop (EMERGENCY_STOP_RISK_MULTIPLE): fires independently of the
        # above, so a data gap or an already-stale stop level can't leave a position
        # bleeding unmonitored — this is what would have capped the incident that
        # motivated it (a position sitting at 5x its own intended risk).
        direction = 1 if side == "long" else -1
        unrealized_now = (bar_close - open_pos["entry_price"]) * direction * pos_pv
        intended_risk = abs(open_pos["entry_price"] - stop) * pos_pv
        emergency = (
            not hit_stop and not hit_target
            and intended_risk > 0
            and -unrealized_now > intended_risk * EMERGENCY_STOP_RISK_MULTIPLE
        )

        exit_reason = (
            "stop" if hit_stop
            else "target" if hit_target
            else "emergency_stop" if emergency
            else ("eod_flatten" if must_flatten else None)
        )
        if exit_reason:
            bar_extreme = bar_low if side == "long" else bar_high
            exit_price = _finalize_exit_price(exit_reason, side, stop, target, bar_close, bar_extreme, symbol)
            commission = open_pos.get("commission", DEFAULT_COMMISSION_PER_TRADE)
            trade_pnl = (exit_price - open_pos["entry_price"]) * direction * pos_pv - commission
            new_balance = round(account["balance"] + trade_pnl, 2)
            trade = {
                "entry_time": open_pos["entry_time"],
                "exit_time": last_bar_time,
                "side": side,
                "entry_price": open_pos["entry_price"],
                "exit_price": exit_price,
                "pnl": round(trade_pnl, 2),
                "exit_reason": exit_reason,
                "qty": open_pos.get("qty", 1),
                "contract": open_pos.get("contract"),
                "commission": commission,
                "risk_dollars": open_pos.get("risk_dollars"),
                # stop/target are stored too (and shown on the Telegram close message)
                "stop_price": stop,
                "target_price": target,
            }
            db.record_live_trade(account_id, trade, new_balance)
            _send_traderspost_exit(account, symbol, trade)
            db.update_daily_pnl(account_id, trading_date, trade_pnl)
            db.check_and_archive_if_burned(account_id, new_balance, account["starting_balance"])
            just_closed_trade = trade
            open_pos = None

            # The circuit breaker itself is gated on realized P&L only, so it can only
            # newly cross either threshold at the instant a trade closes — check right
            # here with the just-updated running total, not the start-of-tick value
            # `circuit_tripped` above was computed from.
            updated_daily_pnl = daily_pnl + trade_pnl
            newly_tripped = (loss_limit is not None and updated_daily_pnl <= -loss_limit) or (
                profit_limit is not None and updated_daily_pnl >= profit_limit
            )
            if newly_tripped and account.get("circuit_notified_date") != trading_date:
                _send_circuit_notification(
                    account, trading_date, updated_daily_pnl, loss_limit, profit_limit, new_balance
                )
                db.set_circuit_notified(account_id, trading_date)

            # Payout target: stop live trading for good once equity reaches this level (not a
            # daily reset like the circuit breaker above) and flag the account for payout.
            payout_target = account.get("payout_target")
            if payout_target and not account.get("awaiting_payout") and new_balance >= payout_target:
                db.set_awaiting_payout(account_id)
                account["status"] = "idle"  # so the rest of this tick (below) treats it as stopped
    elif not must_flatten and not circuit_tripped and is_new_bar and _blackout_blocks(account_id):
        pass  # around CPI / FOMC / NFP: no NEW entries (open positions are still managed above)
    elif not must_flatten and not circuit_tripped and is_new_bar:
        trades = strategy.generate_trades(df, params)
        latest, stale_entry = _pick_entry(trades, account, last_bar_ts, last_bar)
        if latest is not None:
            # a caught-up entry fills at the current price (what a real order would get), not the missed bar's
            # same fill model as the backtest: a market entry pays ~1 tick of spread/slippage
            slip = ENTRY_SLIPPAGE_TICKS * TICK_SIZE.get(symbol.upper(), 0.25) * (1 if latest.side == "long" else -1)
            entry_p = round_to_tick((float(last_bar["close"]) if stale_entry else latest.entry_price) + slip, symbol)
            stop_p = round_to_tick(latest.stop_price, symbol)
            sizing_mode = account.get("sizing_mode") or LEGACY_MODE
            size_fields: dict[str, Any] | None
            if sizing_mode == LEGACY_MODE:
                # legacy: 1 mini, skip if the stop risks more than 2% of the CURRENT balance
                risk_dollars = abs(latest.entry_price - latest.stop_price) * multiplier
                max_risk_dollars = account["balance"] * MAX_RISK_PCT_OF_BALANCE
                if risk_dollars > max_risk_dollars:
                    logger.info(
                        "account %s: skipping %s entry, risk $%.2f exceeds %.1f%% of balance ($%.2f)",
                        account_id, latest.side, risk_dollars, MAX_RISK_PCT_OF_BALANCE * 100, max_risk_dollars,
                    )
                    size_fields = None
                else:
                    size_fields = {"qty": 1, "contract": "mini", "commission": DEFAULT_COMMISSION_PER_TRADE,
                                   "risk_dollars": round(abs(entry_p - stop_p) * multiplier, 2)}
            else:
                cfg = config_for(sizing_mode, account["starting_balance"], account.get("risk_per_trade"), account.get("max_contracts_minis"))
                sp = size_position(symbol, entry_p, stop_p, cfg, TICK_SIZE.get(symbol.upper(), 0.25))
                if sp is None:
                    logger.info("account %s: skipping %s entry, even 1 micro risks more than the hard cap", account_id, latest.side)
                    size_fields = None
                else:
                    size_fields = {"qty": sp.qty, "contract": sp.contract, "commission": sp.commission,
                                   "risk_dollars": round(sp.risk_dollars, 2)}
            if size_fields is not None:
                open_pos = {
                    "side": latest.side,
                    "entry_time": latest.entry_time.isoformat(),
                    "entry_price": entry_p,
                    "stop_price": stop_p,
                    "target_price": round_to_tick(latest.target_price, symbol),
                    **size_fields,
                }
                db.set_open_position(account_id, open_pos)
                _send_traderspost_entry(account, symbol, open_pos)
                just_opened = True

    if is_eval:
        unrealized = 0.0
        if open_pos:
            direction = 1 if open_pos["side"] == "long" else -1
            unrealized = (float(last_bar["close"]) - open_pos["entry_price"]) * direction * pnl.position_point_value(symbol, open_pos)
        current_equity = new_balance + unrealized
        # must_flatten is true only once the 4:59pm ET window is reached, after which no new
        # entries are taken for the rest of the day - matching Apex's real "EOD close" boundary
        # where the peak/floor are allowed to ratchet up, instead of every intraday tick.
        verdict = apex_rules.evaluate_tick(account, current_equity, is_eod_close=must_flatten)
        db.update_eval_progress(account_id, verdict["peak_equity"], verdict["drawdown_floor"])
        if verdict["breached"]:
            db.finish_eval(
                account_id, "failed",
                f"Drawdown floor ${verdict['drawdown_floor']:,.2f} breached at equity ${current_equity:,.2f}",
            )
        elif verdict["passed"]:
            db.finish_eval(account_id, "passed")

    if just_opened:
        new_msg_id = _send_telegram_open(account, symbol, strategy, params, open_pos, df)
        if new_msg_id:
            db.set_telegram_msg(account_id, new_msg_id)
    elif just_closed_trade is not None and telegram_msg_id:
        _send_telegram_close(account, symbol, strategy, params, telegram_msg_id, just_closed_trade, df)
        db.set_telegram_msg(account_id, None)
    elif open_pos and telegram_msg_id:
        current_price = float(last_bar["close"])
        unrl = pnl.unrealized(symbol, open_pos["side"], open_pos["entry_price"], current_price,
                              open_pos.get("qty", 1), open_pos.get("contract"))
        _send_telegram_pnl_update(account, symbol, strategy, telegram_msg_id, open_pos, unrl)

    db.set_last_bar_time(account_id, last_bar_time)


def _rr_ratio(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return round(reward / risk, 2) if risk else 0.0


def _pnl_emoji(value: float) -> str:
    if value > 0:
        return "\U0001F7E2"
    if value < 0:
        return "\U0001F534"
    return "⚪"


def _setup_description(strategy, side: str, entry: float, stop: float, target: float) -> str:
    risk_pts = round(abs(entry - stop), 2)
    reward_pts = round(abs(target - entry), 2)
    return (
        f"{html.escape(strategy.description)}\n"
        f"Entering {side.upper()} at {entry}. Risking {risk_pts} pts to stop, targeting {reward_pts} pts."
    )


def _build_caption(
    account_name: str, symbol: str, side: str, entry_price: float, stop_price: float, target_price: float,
    setup_text: str, status_line: str,
) -> str:
    side_emoji = "🟢" if side == "long" else "🔴"
    return (
        f"{side_emoji} <b>{html.escape(account_name)}</b> took a trade\n"
        f"<b>{symbol}</b> · {TELEGRAM_TIMEFRAME_LABEL} · {side.upper()}\n"
        f"{status_line}"
    )


def _send_traderspost_entry(account: dict[str, Any], symbol: str, open_pos: dict[str, Any]) -> None:
    """Fire-and-forget real order for the one account explicitly linked to TradersPost.
    Every other account has traderspost_linked = 0 and this is a complete no-op for them."""
    if not account.get("traderspost_linked") or not traderspost.is_configured():
        return
    try:
        traderspost.send_entry(symbol, open_pos.get("contract"), open_pos["side"], open_pos.get("qty", 1), open_pos["entry_price"], open_pos["stop_price"], open_pos["target_price"])
    except Exception:
        logger.exception("TradersPost entry webhook failed")


def _send_traderspost_exit(account: dict[str, Any], symbol: str, trade: dict[str, Any]) -> None:
    if not account.get("traderspost_linked") or not traderspost.is_configured():
        return
    try:
        traderspost.send_exit(symbol, trade.get("contract"), trade["exit_price"])
    except Exception:
        logger.exception("TradersPost exit webhook failed")


def _send_telegram_open(
    account: dict[str, Any], symbol: str, strategy, params: dict[str, Any], open_pos: dict[str, Any], df
) -> int | None:
    if not telegram_bot.is_configured():
        return None
    try:
        entry_time = df.index[-1]
        photo = chart_render.render_trade_card(
            df, account["name"], symbol, TELEGRAM_TIMEFRAME_LABEL, open_pos["side"],
            open_pos["entry_price"], open_pos["stop_price"], open_pos["target_price"], entry_time,
            description=strategy.description.split(".")[0],
        )
        setup_text = _setup_description(
            strategy, open_pos["side"], open_pos["entry_price"], open_pos["stop_price"], open_pos["target_price"]
        )
        status_line = f"{_pnl_emoji(0)} PnL: <code>$0.00</code> (open)"
        caption = _build_caption(
            account["name"], symbol, open_pos["side"], open_pos["entry_price"],
            open_pos["stop_price"], open_pos["target_price"], setup_text, status_line,
        )
        return telegram_bot.send_trade_open(caption, photo, account_id=account["id"])
    except Exception:
        logger.exception("Telegram trade-open notification failed")
        return None


def _send_telegram_pnl_update(
    account: dict[str, Any], symbol: str, strategy, msg_id: int, open_pos: dict[str, Any], unrealized_pnl: float
) -> None:
    try:
        setup_text = _setup_description(
            strategy, open_pos["side"], open_pos["entry_price"], open_pos["stop_price"], open_pos["target_price"]
        )
        status_line = f"{_pnl_emoji(unrealized_pnl)} PnL: <code>{unrealized_pnl:+.2f}</code> (open)"
        caption = _build_caption(
            account["name"], symbol, open_pos["side"], open_pos["entry_price"],
            open_pos["stop_price"], open_pos["target_price"], setup_text, status_line,
        )
        telegram_bot.edit_caption(msg_id, caption, account_id=account["id"])
    except Exception:
        logger.exception("Telegram PnL update failed")



SESSION_TZ = "Asia/Jerusalem"  # the daily loss/profit limits reset at 00:00 here (= 17:00 New York, the Apex trading-day close)


def session_date(ts) -> str:
    """Trading-day label for a bar/trade time: the calendar date in Israel, so the daily limits reset at 00:00 Israel time."""
    import pandas as pd

    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("America/New_York")
    return t.tz_convert(SESSION_TZ).date().isoformat()


def _session_date_str(ts_text) -> str:
    try:
        return session_date(ts_text)
    except Exception:  # noqa: BLE001
        return str(ts_text)[:10]


def _send_circuit_notification(
    account: dict[str, Any],
    trading_date: str,
    updated_daily_pnl: float,
    loss_limit: float | None,
    profit_limit: float | None,
    balance_after: float,
) -> None:
    try:
        is_profit = profit_limit is not None and updated_daily_pnl >= profit_limit
        limit_value = profit_limit if is_profit else loss_limit
        todays_trades = [t for t in db.get_account_trades(account["id"]) if _session_date_str(t["exit_time"]) == trading_date]
        wins = sum(1 for t in todays_trades if t["pnl"] >= 0)
        losses = len(todays_trades) - wins
        balance_before = round(balance_after - updated_daily_pnl, 2)
        photo = chart_render.render_circuit_card(
            account["name"], "profit" if is_profit else "loss", limit_value, updated_daily_pnl,
            len(todays_trades), wins, losses, balance_before, balance_after, trading_date,
        )
        telegram_bot.send_circuit_card(photo)
    except Exception:
        logger.exception("Telegram circuit-card notification failed")


def _send_telegram_close(
    account: dict[str, Any], symbol: str, strategy, params: dict[str, Any], msg_id: int, trade: dict[str, Any], df
) -> None:
    """The entry message is deleted and replaced by a Jordan Terminal WIN/LOSS result card (no refresh button —
    a closed trade has nothing left to refresh)."""
    win = trade["pnl"] >= 0
    caption = (
        f"{'✅ CLOSED — WIN' if win else '❌ CLOSED — LOSS'} ({trade['exit_reason']})\n"
        f"<b>{html.escape(account['name'])}</b> · <b>{symbol}</b> · {TELEGRAM_TIMEFRAME_LABEL} · {trade['side'].upper()}\n"
        f"{_pnl_emoji(trade['pnl'])} PnL: <code>{trade['pnl']:+.2f}</code>"
    )
    try:
        photo = chart_render.render_trade_card(
            df, account["name"], symbol, TELEGRAM_TIMEFRAME_LABEL, trade["side"],
            trade["entry_price"], trade["stop_price"], trade["target_price"], pd.Timestamp(trade["entry_time"]),
            description=strategy.description.split(".")[0],
            exit_time=pd.Timestamp(trade["exit_time"]), exit_price=trade["exit_price"],
            exit_reason=trade["exit_reason"], pnl=trade["pnl"],
        )
        if telegram_bot.send_trade_close(caption, photo):
            telegram_bot.delete_message(msg_id)
    except Exception:
        logger.exception("Telegram close card failed")


async def _tick_all() -> None:
    accounts = await asyncio.to_thread(db.list_live_accounts)
    # The one account linked to TradersPost sends a real order; process it first each tick so
    # its signal check and webhook fire with minimal queueing delay behind the rest of the fleet.
    accounts.sort(key=lambda a: 0 if a.get("traderspost_linked") else 1)
    for account in accounts:
        try:
            await asyncio.to_thread(_process_account, account)
            _state["account_errors"].pop(account["id"], None)
        except Exception as e:  # one bad account must never stop the others
            _state["account_errors"][account["id"]] = str(e)


def _blackout_blocks(account_id: int) -> bool:
    """True while a high-impact release (CPI, FOMC, NFP...) is inside its blackout window. Reads only the stored calendar."""
    try:
        from app import event_blackout

        ev = event_blackout.current()
    except Exception:  # noqa: BLE001 - never let the calendar stop the engine
        return False
    if ev:
        logger.info("account %s: new entries paused, blackout for %s %s", account_id, ev.get("country"), ev.get("title"))
    return bool(ev)


async def live_loop() -> None:
    while True:
        try:
            await _tick_all()
            _state["last_error"] = None
            try:  # queued strategy switches happen the moment an account is flat
                from app.accounts import reassign

                await asyncio.to_thread(reassign.apply_pending)
            except Exception:
                logger.exception("reassign.apply_pending failed")
            try:  # combo evaluations: shared Apex drawdown over their member accounts
                from app.accounts import combo_eval

                await asyncio.to_thread(combo_eval.tick)
            except Exception:
                logger.exception("combo_eval.tick failed")
            try:  # the AUTO account follows the champion; it must never break the live tick
                from app.accounts import auto_account

                await asyncio.to_thread(auto_account.tick)
            except Exception:
                logger.exception("auto_account.tick failed")
            try:  # the real-money account follows a clearly better strategy by itself (flat only; off with /realauto off)
                from app.accounts import real_follower

                await asyncio.to_thread(real_follower.tick)
            except Exception:
                logger.exception("real_follower.tick failed")
            try:  # hard safety net: nothing stays open past 23:59 Israel time, whatever the account type
                from app.accounts import eod_flatten

                await asyncio.to_thread(eod_flatten.check_and_flatten)
            except Exception:
                logger.exception("eod_flatten.check_and_flatten failed")
        except Exception as e:
            _state["last_error"] = str(e)
        _state["last_tick_at"] = time.time()
        await asyncio.sleep(LIVE_POLL_SECONDS)


def get_status() -> dict[str, Any]:
    live = db.list_live_accounts()
    return {
        "poll_seconds": LIVE_POLL_SECONDS,
        "last_tick_at": _state["last_tick_at"],
        "last_error": _state["last_error"],
        "live_account_count": len(live),
        "open_position_count": sum(1 for a in live if a.get("open_position")),
        "account_errors": _state["account_errors"],
    }
