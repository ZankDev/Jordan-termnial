"""SIMULATE: replay an account's strategy over the recent 5-minute history through that account's own rules, and write the
result into the account exactly as live trading would have — trades, balance, and for an Apex evaluation the trailing
drawdown, the pass / fail verdict and the day it happened.

Why it exists: the live engine can only act while the futures market is open, so on a weekend (or right after an account is
reset) every account sits empty. A simulation gives each account a real, comparable track record immediately, and shows
which strategies would have passed the evaluation. Trades are marked source='sim' so they can never be confused with live
ones; an account that is still in progress afterwards is left LIVE and carries on from the simulated end.

Rules replayed per account (same functions the live engine and the evaluation preview use):
  * the strategy's own signals on the 5m history (adjust_trade_prices for slippage-adjusted fills),
  * the account's position sizing (fixed_1 / risk_micro, hard cap skips),
  * the daily loss limit (no new trades that day once the day's realized P&L is at the limit),
  * Apex profit target and trailing drawdown (apex_rules.evaluate_tick), checked at each closed trade — the same closed-trade
    granularity as the evaluation preview, so it is slightly optimistic about intraday drawdown.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from app import db
from app.accounts import apex_rules
from app.accounts.ledger import CONTRACT_MULTIPLIER, DEFAULT_COMMISSION_PER_TRADE, adjust_trade_prices, compute_trade_pnl
from app.backtest.sizing import LEGACY_MODE, config_for, size_position
from app.data import yfinance_source
from app.strategies import registry

logger = logging.getLogger(__name__)
TICK = {"NQ": 0.25, "ES": 0.25}

_status: dict[str, Any] = {"running": False, "done": 0, "total": 0, "current": None, "results": [], "started_at": None, "finished_at": None, "error": None}
_lock = threading.Lock()


def simulate_account(account_id: int, df=None) -> dict[str, Any]:
    account = db.get_account(account_id)
    if not account:
        raise KeyError(account_id)
    if not account.get("strategy") or not account.get("symbol"):
        raise ValueError("Assign a strategy and symbol first.")
    symbol, start = account["symbol"], float(account["starting_balance"])
    strat = registry.get(account["strategy"])
    params = json.loads(account["strategy_params_json"])
    if df is None:
        df = yfinance_source.fetch(symbol, "5m")
    if df.empty:
        raise RuntimeError(f"No data for {symbol}")
    raw = sorted(strat.generate_trades(df, params), key=lambda t: t.entry_time)

    cfg = config_for(account.get("sizing_mode"), start, account.get("risk_per_trade"), account.get("max_contracts_minis"))
    mult = CONTRACT_MULTIPLIER.get(symbol.upper(), 20.0)
    is_eval = account["account_type"] == "apex_eval"
    loss_limit = account.get("daily_loss_limit")
    eval_acct = {"starting_balance": start, "max_drawdown": account.get("max_drawdown") or 0.0,
                 "profit_target": account.get("profit_target") or 0.0, "peak_equity": None}

    equity, peak = start, start
    day_pnl: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    verdict, reason = ("in_progress" if is_eval else None), None
    skipped_size = skipped_limit = 0
    floor = apex_rules.compute_floor(start, start, eval_acct["max_drawdown"]) if is_eval else None

    for t in raw:
        entry_day = t.entry_time.date().isoformat()
        if loss_limit is not None and day_pnl.get(entry_day, 0.0) <= -float(loss_limit):
            skipped_limit += 1
            continue
        entry, exit_, stop, target = adjust_trade_prices(t, symbol)
        if cfg.mode == LEGACY_MODE:
            pnl, qty, contract, comm = compute_trade_pnl(entry, exit_, t.side, mult, DEFAULT_COMMISSION_PER_TRADE), 1, "mini", DEFAULT_COMMISSION_PER_TRADE
            risk = abs(entry - stop) * mult
        else:
            sp = size_position(symbol, entry, stop, cfg, TICK.get(symbol.upper(), 0.25))
            if sp is None:
                skipped_size += 1
                continue
            pnl = (exit_ - entry) * (1 if t.side == "long" else -1) * sp.qty * sp.point_value - sp.commission
            qty, contract, comm, risk = sp.qty, sp.contract, sp.commission, sp.risk_dollars
        equity += pnl
        exit_day = t.exit_time.date().isoformat()
        day_pnl[exit_day] = day_pnl.get(exit_day, 0.0) + pnl
        rows.append({"entry_time": t.entry_time.isoformat(), "exit_time": t.exit_time.isoformat(), "side": t.side, "entry_price": entry,
                     "exit_price": exit_, "pnl": round(pnl, 2), "equity_after": round(equity, 2), "exit_reason": t.exit_reason,
                     "stop_price": stop, "target_price": target, "qty": qty, "contract": contract, "commission": round(comm, 2),
                     "risk_dollars": round(risk, 2)})
        if is_eval:
            v = apex_rules.evaluate_tick(eval_acct, equity)
            eval_acct["peak_equity"], floor = v["peak_equity"], v["drawdown_floor"]
            if v["breached"]:
                verdict, reason = "failed", f"Drawdown floor ${floor:,.2f} breached at equity ${equity:,.2f} on {exit_day}"
                break
            if v["passed"]:
                verdict = "passed"
                break
        peak = max(peak, equity)

    last_bar = df.index[-1].isoformat()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM account_trades WHERE account_id = ?", (account_id,))
        for r in rows:
            conn.execute(
                "INSERT INTO account_trades (account_id, entry_time, exit_time, side, entry_price, exit_price, pnl, equity_after, exit_reason, "
                "stop_price, target_price, source, strategy, symbol, qty, contract, commission, risk_dollars) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'sim', ?,?,?,?,?,?)",
                (account_id, r["entry_time"], r["exit_time"], r["side"], r["entry_price"], r["exit_price"], r["pnl"], r["equity_after"],
                 r["exit_reason"], r["stop_price"], r["target_price"], account["strategy"], symbol, r["qty"], r["contract"], r["commission"], r["risk_dollars"]),
            )
        finished = verdict in ("passed", "failed")
        conn.execute(
            "UPDATE accounts SET balance = ?, open_position_json = NULL, last_bar_time = ?, last_run_at = datetime('now'), "
            "daily_realized_pnl = 0, daily_pnl_date = NULL, status = ?, eval_status = ?, eval_started_at = COALESCE(eval_started_at, datetime('now')), "
            "eval_ended_at = CASE WHEN ? THEN datetime('now') ELSE NULL END, eval_fail_reason = ?, peak_equity = ?, drawdown_floor = ? WHERE id = ?",
            (round(equity, 2), last_bar, "idle" if finished else "live", verdict, int(finished), reason,
             eval_acct["peak_equity"] if is_eval else None, floor, account_id),
        )
    return {"id": account_id, "name": account["name"], "strategy": account["strategy"], "symbol": symbol, "trades": len(rows),
            "verdict": verdict or "n/a", "balance": round(equity, 2), "net": round(equity - start, 2), "skipped_size": skipped_size,
            "skipped_daily_limit": skipped_limit, "days": len(day_pnl)}


def _run_all() -> None:
    try:
        accounts = [a for a in db.list_accounts() if a.get("strategy") and a.get("symbol")]
        with _lock:
            _status.update(total=len(accounts), done=0, results=[], error=None)
        dfs: dict[str, Any] = {}
        for a in accounts:
            with _lock:
                _status["current"] = a["name"]
            try:
                if a["symbol"] not in dfs:
                    dfs[a["symbol"]] = yfinance_source.fetch(a["symbol"], "5m")
                res = simulate_account(a["id"], dfs[a["symbol"]])
            except Exception as e:  # noqa: BLE001 - one account must not stop the rest
                logger.exception("simulation failed for account %s", a["id"])
                res = {"id": a["id"], "name": a["name"], "strategy": a["strategy"], "symbol": a["symbol"], "error": str(e)[:160]}
            with _lock:
                _status["results"].append(res)
                _status["done"] += 1
    except Exception as e:  # noqa: BLE001
        with _lock:
            _status["error"] = str(e)
    finally:
        with _lock:
            _status.update(running=False, current=None, finished_at=time.time())


def start_all() -> dict[str, Any]:
    with _lock:
        if _status["running"]:
            return dict(_status)
        _status.update(running=True, started_at=time.time(), finished_at=None, done=0, total=0, results=[], error=None)
    threading.Thread(target=_run_all, daemon=True, name="simulate-all").start()
    return status()


def status() -> dict[str, Any]:
    with _lock:
        return {**_status, "results": list(_status["results"])}
