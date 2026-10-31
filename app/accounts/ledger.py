"""Demo account ledger: a $50k (by default) virtual account that a strategy trades against.

v1 "runs" a strategy against historical replay (no broker needed). Phase 4 swaps the
data/execution source for live IBKR paper-trading quotes via live_runner.py, without
changing how the ledger itself works.
"""
from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from app import db
from app.accounts import apex_rules, pnl
from app.backtest.sizing import LEGACY_MODE, config_for, size_position
from app.backtest.engine import (
    CONTRACT_MULTIPLIER,
    DEFAULT_COMMISSION_PER_TRADE,
    adjust_trade_prices,
    compute_trade_pnl,
    round_to_tick,
    run_backtest,
)
from app.data import yfinance_source
from app.optimizer import trainer_service
from app.strategies import registry

DEFAULT_STARTING_BALANCE = 50_000.0
NY_TZ = ZoneInfo("America/New_York")


def create(
    name: str,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    account_type: str = "standard",
    tier_size: int | None = None,
    daily_loss_limit: float | None = None,
    daily_profit_limit: float | None = None,
) -> dict[str, Any]:
    if account_type == "apex_eval":
        if tier_size is None:
            raise ValueError("tier_size is required for an Apex Evaluation account.")
        tier = apex_rules.tier_for_size(tier_size)  # raises ValueError if not a known tier
        return db.create_account(
            name,
            starting_balance=float(tier_size),
            account_type="apex_eval",
            profit_target=tier["profit_target"],
            max_drawdown=tier["max_drawdown"],
            max_contracts_minis=tier["max_contracts_minis"],
            daily_loss_limit=daily_loss_limit if daily_loss_limit is not None else tier.get("daily_loss_limit"),
            daily_profit_limit=daily_profit_limit,
        )
    return db.create_account(
        name, starting_balance,
        daily_loss_limit=daily_loss_limit,
        daily_profit_limit=daily_profit_limit,
    )


def list_all(include_archived: bool = False) -> list[dict[str, Any]]:
    return db.list_accounts(include_archived=include_archived)


def get(account_id: int) -> dict[str, Any] | None:
    return db.get_account(account_id)


def assign_strategy(account_id: int, strategy: str, symbol: str, params: dict[str, Any] | None) -> dict[str, Any]:
    strat = registry.get(strategy)  # raises KeyError if unknown
    resolved_params = {**strat.default_params(), **(params or {})}
    db.assign_strategy(account_id, strategy, symbol, resolved_params)
    return db.get_account(account_id)


def run_against_history(account_id: int, interval: str = "5m", lookback_days: int | None = None) -> dict[str, Any]:
    account = db.get_account(account_id)
    if not account:
        raise KeyError(f"No account with id {account_id}")
    if not account.get("strategy") or not account.get("symbol"):
        raise ValueError("Assign a strategy and symbol to this account before running it.")

    import json

    strat = registry.get(account["strategy"])
    params = json.loads(account["strategy_params_json"])
    df = yfinance_source.fetch(account["symbol"], interval)
    if lookback_days:
        import pandas as pd

        cutoff = df.index.max() - pd.Timedelta(days=lookback_days)
        df = df[df.index >= cutoff]

    result = run_backtest(
        df, strat, params, account["symbol"], starting_balance=account["starting_balance"],
        sizing=config_for(account.get("sizing_mode"), account["starting_balance"], account.get("risk_per_trade"), account.get("max_contracts_minis")),
    )
    db.record_account_run(account_id, result.metrics["ending_equity"], result.trades)
    return {
        "account": db.get_account(account_id),
        "trades": result.trades,
        "equity_curve": result.equity_curve,
        "metrics": result.metrics,
    }


def go_live(account_id: int) -> dict[str, Any]:
    account = db.get_account(account_id)
    if not account:
        raise KeyError(f"No account with id {account_id}")
    if not account.get("strategy") or not account.get("symbol"):
        raise ValueError("Assign a strategy and symbol to this account before going live.")
    if account["account_type"] == "apex_eval" and account.get("eval_status") in ("passed", "failed"):
        raise ValueError("This evaluation has already concluded — RESET the account to attempt again.")

    df = yfinance_source.fetch(account["symbol"], "5m", max_age_seconds=90)
    if df.empty:
        raise RuntimeError(f"No live data available for {account['symbol']} right now.")

    db.go_live(account_id, df.index[-1].isoformat())
    if account["account_type"] == "apex_eval" and not account.get("eval_status"):
        db.start_eval(account_id, account["starting_balance"], account["max_drawdown"])
    return db.get_account(account_id)


def stop_live(account_id: int) -> dict[str, Any]:
    account = db.get_account(account_id)
    if not account:
        raise KeyError(f"No account with id {account_id}")

    open_pos = account.get("open_position")
    if open_pos:
        df = yfinance_source.fetch(account["symbol"], "5m", max_age_seconds=90)
        last_close = round_to_tick(float(df["close"].iloc[-1]), account["symbol"]) if not df.empty else open_pos["entry_price"]
        exit_time = df.index[-1].isoformat() if not df.empty else dt.datetime.now(NY_TZ).isoformat()
        # a position carries its own size; legacy positions (no qty) are 1 mini at the flat commission
        pos_pv = pnl.position_point_value(account["symbol"], open_pos)
        commission = open_pos.get("commission", DEFAULT_COMMISSION_PER_TRADE)
        direction = 1 if open_pos["side"] == "long" else -1
        trade_pnl = (last_close - open_pos["entry_price"]) * direction * pos_pv - commission
        new_balance = round(account["balance"] + trade_pnl, 2)
        trade = {
            "entry_time": open_pos["entry_time"],
            "exit_time": exit_time,
            "side": open_pos["side"],
            "entry_price": open_pos["entry_price"],
            "exit_price": last_close,
            "pnl": round(trade_pnl, 2),
            "exit_reason": "manual_stop",
            "qty": open_pos.get("qty", 1),
            "contract": open_pos.get("contract"),
            "commission": commission,
            "risk_dollars": open_pos.get("risk_dollars"),
        }
        db.record_live_trade(account_id, trade, new_balance)

    db.stop_live(account_id)
    return db.get_account(account_id)


def backtest_evaluation(account_id: int) -> dict[str, Any]:
    """Fast preview: 'would this strategy have passed the evaluation?' — approximated
    by checking Apex's rules at each closed-trade boundary rather than every tick.
    Never touches real account_trades history; purely a projection."""
    import json

    account = db.get_account(account_id)
    if not account:
        raise KeyError(f"No account with id {account_id}")
    if account["account_type"] != "apex_eval":
        raise ValueError("Backtest evaluation is only for Apex Evaluation accounts.")
    if not account.get("strategy") or not account.get("symbol"):
        raise ValueError("Assign a strategy and symbol to this account before previewing an evaluation.")

    strat = registry.get(account["strategy"])
    params = json.loads(account["strategy_params_json"])
    symbol = account["symbol"]
    df = yfinance_source.fetch(symbol, "5m")
    trades_list = strat.generate_trades(df, params)
    trades_list.sort(key=lambda t: t.entry_time)

    multiplier = CONTRACT_MULTIPLIER.get(symbol.upper(), 20.0)
    starting_balance = account["starting_balance"]
    sizing_cfg = config_for(account.get("sizing_mode"), starting_balance, account.get("risk_per_trade"), account.get("max_contracts_minis"))
    equity = starting_balance
    eval_account = {
        "starting_balance": starting_balance,
        "max_drawdown": account["max_drawdown"],
        "profit_target": account["profit_target"],
        "peak_equity": None,
    }
    daily_pnl: dict[str, float] = {}
    equity_curve = [{"time": None, "equity": equity}]
    trade_rows = []
    result, result_day = "in_progress", None

    for t in trades_list:
        entry_price, exit_price, stop_price, target_price = adjust_trade_prices(t, symbol)
        if sizing_cfg.mode == LEGACY_MODE:
            pnl = compute_trade_pnl(entry_price, exit_price, t.side, multiplier, DEFAULT_COMMISSION_PER_TRADE)
        else:
            sp = size_position(symbol, entry_price, stop_price, sizing_cfg)
            if sp is None:
                continue  # the live engine would skip this trade too
            direction = 1 if t.side == "long" else -1
            pnl = (exit_price - entry_price) * direction * sp.qty * sp.point_value - sp.commission
        equity += pnl
        day = t.exit_time.date().isoformat()
        daily_pnl[day] = round(daily_pnl.get(day, 0) + pnl, 2)
        trade_rows.append(
            {
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "side": t.side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": round(pnl, 2),
                "equity_after": round(equity, 2),
                "exit_reason": t.exit_reason,
            }
        )
        equity_curve.append({"time": t.exit_time.isoformat(), "equity": round(equity, 2)})

        verdict = apex_rules.evaluate_tick(eval_account, equity)
        eval_account["peak_equity"] = verdict["peak_equity"]
        if verdict["breached"]:
            result, result_day = "failed", day
            break
        if verdict["passed"]:
            result, result_day = "passed", day
            break

    return {
        "result": result,
        "result_day": result_day,
        "trades_used": len(trade_rows),
        "ending_equity": round(equity, 2),
        "peak_equity": round(eval_account["peak_equity"] or starting_balance, 2),
        "drawdown_floor": round(
            apex_rules.compute_floor(eval_account["peak_equity"] or starting_balance, starting_balance, account["max_drawdown"]), 2
        ),
        "profit_target": account["profit_target"],
        "max_drawdown": account["max_drawdown"],
        "daily_pnl": daily_pnl,
        "trades": trade_rows,
        "equity_curve": equity_curve,
    }


def _open_positions_with_unrealized(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich every account's open position with live unrealized P&L, for the home-page
    tracker — reuses the same last-close-price approach live_engine.py already ticks on,
    fetching each needed symbol's price once (not once per account) since NQ/ES accounts
    share the same underlying quote."""
    open_accounts = [a for a in accounts if a.get("open_position") and a.get("symbol")]
    if not open_accounts:
        return []

    prices: dict[str, float] = {}
    for symbol in {a["symbol"] for a in open_accounts}:
        try:
            # the cached last close the live engine refreshes every tick; never waits on Yahoo (a download here made the
            # trades feed take 30-60 s whenever Yahoo was slow)
            px = yfinance_source.last_close(symbol, max_age_seconds=90)
            if px:
                prices[symbol] = px[0]
        except Exception:  # noqa: BLE001
            continue

    positions = []
    for a in open_accounts:
        price = prices.get(a["symbol"])
        pos = a["open_position"]
        unrealized = (
            pnl.unrealized(a["symbol"], pos["side"], pos["entry_price"], price, pos.get("qty", 1), pos.get("contract"))
            if price is not None else None
        )
        positions.append(
            {
                "account_id": a["id"],
                "account_name": a["name"],
                "symbol": a["symbol"],
                "strategy": a.get("strategy"),
                "side": pos["side"],
                "entry_time": pos["entry_time"],
                "entry_price": pos["entry_price"],
                "stop_price": pos["stop_price"],
                "target_price": pos["target_price"],
                "current_price": price,
                "qty": pos.get("qty", 1),
                "contract": pos.get("contract") or "mini",
                "point_value": pnl.position_point_value(a["symbol"], pos) / float(pos.get("qty") or 1),
                "unrealized_pnl": round(unrealized, 2) if unrealized is not None else None,
            }
        )
    return positions


def dashboard_summary() -> dict[str, Any]:
    accounts = db.list_accounts()
    total_equity = sum(a["balance"] for a in accounts)
    live_count = sum(1 for a in accounts if a["status"] == "live")
    open_positions_count = sum(1 for a in accounts if a.get("open_position"))
    open_positions = _open_positions_with_unrealized(accounts)
    eval_accounts = [a for a in accounts if a["account_type"] == "apex_eval"]
    eval_passed_count = sum(1 for a in eval_accounts if a.get("eval_status") == "passed")
    eval_failed_count = sum(1 for a in eval_accounts if a.get("eval_status") == "failed")
    eval_in_progress_count = sum(1 for a in eval_accounts if a.get("eval_status") == "in_progress")

    recent_trades = db.get_recent_trades_with_account(limit=300)
    today = dt.datetime.now(NY_TZ).date()
    todays_trades = []
    for t in recent_trades:
        try:
            exit_dt = dt.datetime.fromisoformat(t["exit_time"])
        except ValueError:
            continue
        if exit_dt.astimezone(NY_TZ).date() == today:
            todays_trades.append(t)

    today_pnl = sum(t["pnl"] for t in todays_trades)
    equity_before_today = total_equity - today_pnl
    today_pnl_pct = (today_pnl / equity_before_today * 100) if equity_before_today else 0.0
    # open trades marked at the last price; and, when nothing has closed yet today, the last session that did
    open_pnl = sum(p.get("unrealized_pnl") or 0 for p in open_positions)
    last_session = None
    if not todays_trades:
        days: dict[str, list[float]] = {}
        for t in recent_trades:
            try:
                d = dt.datetime.fromisoformat(t["exit_time"]).astimezone(NY_TZ).date().isoformat()
            except ValueError:
                continue
            if d < today.isoformat():
                days.setdefault(d, []).append(t["pnl"])
        if days:
            d = max(days)
            last_session = {"date": d, "pnl": round(sum(days[d]), 2), "trades": len(days[d]), "wins": sum(1 for p in days[d] if p > 0)}

    training_today = trainer_service.get_training_today_stats()

    return {
        "total_equity": round(total_equity, 2),
        "today_pnl": round(today_pnl, 2),
        "today_pnl_pct": round(today_pnl_pct, 2),
        "today_open_pnl": round(open_pnl, 2),
        "today_net": round(today_pnl + open_pnl, 2),
        "last_session": last_session,
        "open_positions_count": open_positions_count,
        "open_positions": open_positions,
        "live_accounts_count": live_count,
        "today_trades_count": len(todays_trades),
        "today_trades": todays_trades[:20],
        "eval_accounts_count": len(eval_accounts),
        "eval_passed_count": eval_passed_count,
        "eval_failed_count": eval_failed_count,
        "eval_in_progress_count": eval_in_progress_count,
        "training_runs_today": training_today["runs_today"],
        "training_backtests_today": training_today["backtests_today"],
    }


def reset(account_id: int) -> dict[str, Any]:
    db.reset_account(account_id)
    return db.get_account(account_id)


def delete(account_id: int) -> None:
    db.delete_account(account_id)


def trades(account_id: int) -> list[dict[str, Any]]:
    return db.get_account_trades(account_id)


def equity_curves() -> list[dict[str, Any]]:
    """One equity-over-time curve per tracked account (every status — idle, ran, live,
    passed/failed eval), each starting from its own starting_balance at account-creation
    time (so every line begins at the same baseline, Alpha-Arena-style) — burned/archived
    accounts are excluded by list_accounts' default, since those are hidden everywhere
    else in the UI unless the user explicitly shows archived."""
    curves = []
    for a in db.list_accounts():
        account_trades = db.get_account_trades(a["id"])
        points = [{"time": a["created_at"], "equity": a["starting_balance"]}]
        points += [{"time": t["exit_time"], "equity": t["equity_after"]} for t in account_trades]
        curves.append(
            {
                "account_id": a["id"],
                "name": a["name"],
                "starting_balance": a["starting_balance"],
                "current_equity": points[-1]["equity"],
                "points": points,
            }
        )
    return curves
