from __future__ import annotations

import os

from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import asyncio
import datetime as dt
import json

from app import db
from app.accounts import apex_rules, ledger, live_engine
from app.backtest.engine import run_backtest
from app.backtest.montecarlo import run_monte_carlo
from app.backtest.sizing import config_for
from app.backtest.stress_test import run_crash_windows
from app.data import dukascopy_source, fear_greed, yfinance_source
from app.notify import telegram_bot
from app.optimizer import skill_level, trainer_service
from app.optimizer.search import random_search, train_all, train_all_long_horizon
from app.strategies import registry

router = APIRouter(prefix="/api")


def _candles_from_df(df) -> list[dict[str, Any]]:
    return [
        {
            "time": int(ts.timestamp()),
            "open": round(float(row.open), 2),
            "high": round(float(row.high), 2),
            "low": round(float(row.low), 2),
            "close": round(float(row.close), 2),
        }
        for ts, row in df.iterrows()
    ]


@router.get("/symbols")
def get_symbols():
    return {"symbols": yfinance_source.available_symbols()}


@router.get("/strategies")
def get_strategies():
    stats_by_symbol = {sym: db.get_strategy_stats(sym) for sym in trainer_service.SYMBOL_CYCLE}
    return {
        "strategies": [
            {
                "name": s.name,
                "description": s.description,
                "params": [
                    {
                        "name": p.name,
                        "default": p.default,
                        "min": p.min,
                        "max": p.max,
                        "step": p.step,
                        "kind": p.kind,
                    }
                    for p in s.params
                ],
                "skill": skill_level.strategy_skill_summary(
                    {sym: stats_by_symbol[sym].get(s.name) for sym in trainer_service.SYMBOL_CYCLE}
                ),
            }
            for s in registry.list_strategies()
        ]
    }


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str
    interval: str = "5m"
    lookback_days: int | None = None
    starting_balance: float = 50_000.0
    params: dict[str, Any] | None = None
    entry_style: str = "market"  # "market" or "limit" — see engine.LIMIT_ENTRY_IMPROVEMENT_TICKS
    htf_confirm_bars: int = 0  # 0 = disabled; N = require N consecutive same-direction higher-TF candles before entry
    htf_interval_multiplier: int = 4
    # "risk_micro" (default) sizes each trade to ~1% risk in micro contracts - the same scale the
    # optimizer scores on, so the Trainer hero replay matches the champion's stored numbers.
    # "fixed_1" is the legacy 1-mini-per-trade behaviour.
    sizing: str = "risk_micro"


@router.post("/backtest")
def post_backtest(req: BacktestRequest):
    try:
        strategy = registry.get(req.strategy)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        df = yfinance_source.fetch(req.symbol, req.interval)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if req.lookback_days:
        cutoff = df.index.max() - pd.Timedelta(days=req.lookback_days)
        df = df[df.index >= cutoff]

    if df.empty:
        raise HTTPException(status_code=422, detail="No data available for that symbol/interval/lookback.")

    params = {**strategy.default_params(), **(req.params or {})}
    result = run_backtest(
        df, strategy, params, req.symbol, starting_balance=req.starting_balance,
        entry_style=req.entry_style,
        htf_confirm_bars=req.htf_confirm_bars,
        htf_interval_multiplier=req.htf_interval_multiplier,
        sizing=config_for(req.sizing, req.starting_balance),
    )

    return {
        "symbol": req.symbol.upper(),
        "strategy": req.strategy,
        "interval": req.interval,
        "params_used": params,
        "bars_used": len(df),
        "data_range": {
            "start": df.index.min().isoformat(),
            "end": df.index.max().isoformat(),
        },
        "candles": _candles_from_df(df),
        "trades": result.trades,
        "equity_curve": result.equity_curve,
        "metrics": result.metrics,
    }


class MonteCarloRequest(BaseModel):
    symbol: str
    strategy: str
    interval: str = "5m"
    lookback_days: int | None = None
    starting_balance: float = 50_000.0
    params: dict[str, Any] | None = None
    n_simulations: int = 1000
    sizing: str = "risk_micro"


@router.post("/backtest/montecarlo")
def post_backtest_montecarlo(req: MonteCarloRequest):
    """Run the strategy's normal backtest, then bootstrap-resample its trade P&Ls
    thousands of times to report a VaR/CVaR-style distribution of plausible outcomes —
    not just the single equity-curve path the historical data happened to trace."""
    try:
        strategy = registry.get(req.strategy)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        df = yfinance_source.fetch(req.symbol, req.interval)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if req.lookback_days:
        cutoff = df.index.max() - pd.Timedelta(days=req.lookback_days)
        df = df[df.index >= cutoff]

    if df.empty:
        raise HTTPException(status_code=422, detail="No data available for that symbol/interval/lookback.")

    params = {**strategy.default_params(), **(req.params or {})}
    result = run_backtest(
        df, strategy, params, req.symbol, starting_balance=req.starting_balance,
        sizing=config_for(req.sizing, req.starting_balance),
    )
    trade_pnls = [t["pnl"] for t in result.trades]
    mc = run_monte_carlo(trade_pnls, starting_balance=req.starting_balance, n_simulations=req.n_simulations)

    return {
        "symbol": req.symbol.upper(),
        "strategy": req.strategy,
        "actual_metrics": result.metrics,
        "monte_carlo": mc,
    }


class StressTestRequest(BaseModel):
    symbol: str
    strategy: str
    interval: str = "15m"
    starting_balance: float = 50_000.0
    params: dict[str, Any] | None = None
    sizing: str = "risk_micro"


@router.post("/backtest/stress-test")
def post_stress_test(req: StressTestRequest):
    """Run the strategy through real historical market-stress windows (see
    app/backtest/stress_test.py for exactly which, and why those specific dates)."""
    try:
        strategy = registry.get(req.strategy)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    params = {**strategy.default_params(), **(req.params or {})}
    windows = run_crash_windows(
        strategy, params, req.symbol, req.interval, starting_balance=req.starting_balance,
        sizing=config_for(req.sizing, req.starting_balance),
    )
    return {"symbol": req.symbol.upper(), "strategy": req.strategy, "windows": windows}


@router.get("/candles")
def get_candles(symbol: str, interval: str = "5m", lookback_days: int | None = None):
    try:
        df = yfinance_source.fetch(symbol, interval)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if lookback_days:
        cutoff = df.index.max() - pd.Timedelta(days=lookback_days)
        df = df[df.index >= cutoff]

    return {"symbol": symbol.upper(), "interval": interval, "candles": _candles_from_df(df)}


# ---------------------------------------------------------------- optimizer

class OptimizeRequest(BaseModel):
    symbol: str
    strategy: str
    interval: str = "5m"
    lookback_days: int | None = None
    n_iterations: int = 200
    train_frac: float = 0.7
    starting_balance: float = 50_000.0
    min_test_trades: int = 15
    top_n: int = 15


@router.post("/optimizer/run")
def post_optimize(req: OptimizeRequest):
    try:
        strategy = registry.get(req.strategy)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        data = yfinance_source.fetch(req.symbol, req.interval)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if req.lookback_days:
        cutoff = data.index.max() - pd.Timedelta(days=req.lookback_days)
        data = data[data.index >= cutoff]

    if data.empty:
        raise HTTPException(status_code=422, detail="No data available for that symbol/interval/lookback.")

    results = random_search(
        data,
        strategy,
        req.symbol,
        n_iterations=req.n_iterations,
        train_frac=req.train_frac,
        starting_balance=req.starting_balance,
        min_test_trades=req.min_test_trades,
        top_n=req.top_n,
    )
    run_id = db.save_optimizer_run(req.symbol.upper(), req.strategy, req.interval, req.n_iterations, results)
    return {"run_id": run_id, "results": results}


class TrainAllRequest(BaseModel):
    symbol: str
    interval: str = "5m"
    lookback_days: int | None = None
    n_iterations_per_strategy: int = 40
    train_frac: float = 0.7
    starting_balance: float = 50_000.0
    min_test_trades: int = 15
    top_n_per_strategy: int = 5
    top_n_overall: int = 30


@router.post("/optimizer/train-all")
def post_train_all(req: TrainAllRequest):
    try:
        data = yfinance_source.fetch(req.symbol, req.interval)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if req.lookback_days:
        cutoff = data.index.max() - pd.Timedelta(days=req.lookback_days)
        data = data[data.index >= cutoff]

    if data.empty:
        raise HTTPException(status_code=422, detail="No data available for that symbol/interval/lookback.")

    results = train_all(
        data,
        req.symbol,
        n_iterations_per_strategy=req.n_iterations_per_strategy,
        train_frac=req.train_frac,
        starting_balance=req.starting_balance,
        min_test_trades=req.min_test_trades,
        top_n_per_strategy=req.top_n_per_strategy,
    )[: req.top_n_overall]

    n_strategies = len(registry.list_strategies())
    run_id = db.save_optimizer_run(
        req.symbol.upper(), "ALL_STRATEGIES", req.interval,
        req.n_iterations_per_strategy * n_strategies, results,
    )
    return {"run_id": run_id, "strategies_trained": n_strategies, "results": results}


@router.get("/optimizer/runs")
def get_optimizer_runs():
    return {"runs": db.list_optimizer_runs()}


@router.get("/optimizer/runs/{run_id}")
def get_optimizer_run(run_id: int):
    run = db.get_optimizer_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# ------------------------------------------------------------------ trainer
# The always-on background trainer (app/optimizer/trainer_service.py) trains
# continuously on a timer; these endpoints just read its state and, for
# run-now, kick an extra cycle off immediately.

@router.get("/trainer/status")
def get_trainer_status():
    status = trainer_service.get_status()
    status["champion_version"] = int(db.meta_get("champion_version", "0") or 0)
    return status


@router.get("/trainer/champion")
def get_trainer_champion(symbol: str = "NQ"):
    champion = db.get_champion(symbol.upper())
    if not champion:
        raise HTTPException(status_code=404, detail=f"No champion yet for {symbol.upper()} — training hasn't completed a cycle.")
    return champion


@router.get("/trainer/promotions")
def get_trainer_promotions(symbol: str | None = None, tier: str | None = None, limit: int = 40):
    """Every promotion decision (including 'nothing eligible this cycle') with its reason."""
    return {"attempts": db.list_attempts(symbol=symbol.upper() if symbol else None, tier=tier, limit=min(limit, 200))}


@router.get("/trainer/pipeline")
def get_trainer_pipeline(symbol: str = "NQ"):
    """Leader + confirmed slots, recent decisions, and the aggregated reasons the champion
    hasn't upgraded - the answer to 'why is the best strategy still N hours old?'."""
    symbol = symbol.upper()
    recent = db.list_attempts(symbol=symbol, limit=40)
    blockers: dict[tuple[str, str, str], dict[str, Any]] = {}
    for a in recent:
        if a["outcome"] in ("promoted", "kept"):
            continue
        key = (a["tier"], a["stage"], (a["reason"] or "")[:60])
        entry = blockers.setdefault(key, {"tier": a["tier"], "stage": a["stage"], "text": a["reason"], "count": 0})
        entry["count"] += 1
    return {
        "symbol": symbol,
        "leader": db.get_slot(symbol, "leader"),
        "confirmed": db.get_slot(symbol, "confirmed"),
        "attempts": recent[:15],
        "blockers": sorted(blockers.values(), key=lambda b: -b["count"])[:6],
        "recent_window": len(recent),
        "champion_version": int(db.meta_get("champion_version", "0") or 0),
    }


@router.get("/trainer/history")
def get_trainer_history(symbol: str | None = None, limit: int = 20):
    return {"runs": db.list_optimizer_runs(limit=limit, strategy=trainer_service.ALL_STRATEGIES_TAG, symbol=symbol)}


class RunNowRequest(BaseModel):
    symbol: str = "NQ"


@router.post("/trainer/run-now")
async def post_trainer_run_now(req: RunNowRequest):
    if os.environ.get("NOSTRO_TRAINER_IN_WORKER") == "1":
        raise HTTPException(status_code=409, detail="Training runs non-stop in the background worker; a cycle is always in progress or about to start.")
    try:
        result = await trainer_service.run_now(req.symbol.upper())
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


# ------------------------------------------------------------- long-horizon
# Multi-year walk-forward mode: trains on the older of two ~1-year windows,
# validates on the newer. Runs on real tick data from Dukascopy's free public
# feed (their own index-CFD instrument, not the CME futures contract used
# everywhere else) since Yahoo's free API caps 5m/15m/30m at 60 days. This is
# slow (a fresh multi-year backfill fetches thousands of hourly files) and
# runs on demand only — it does not join the 24/7 background cycle.

class LongHorizonRequest(BaseModel):
    symbol: str = "NQ"
    interval: str = "15m"
    # Empirically verified real Dukascopy coverage for this instrument goes back to at
    # least 2018 — 8 years from "now" — so this is "as much data as possible" for the
    # walk-forward trainer, not an arbitrary number. A fresh 8-year backfill is slow the
    # first time (see dukascopy_source's caching) and fast on every call after.
    years: int = 8
    n_iterations_per_strategy: int = 150


def _run_long_horizon_sync(req: "LongHorizonRequest") -> dict[str, Any]:
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * req.years + 5)
    df = dukascopy_source.fetch_bars(req.symbol.upper(), start, end, req.interval)
    if df.empty:
        raise RuntimeError(f"No Dukascopy data returned for {req.symbol.upper()} ({req.interval}).")

    results = train_all_long_horizon(
        df, req.symbol.upper(),
        n_iterations_per_strategy=req.n_iterations_per_strategy,
        years=req.years,
    )
    n_strategies = len(registry.list_strategies())
    winner = results[0] if results else None
    run_id = db.save_optimizer_run(
        req.symbol.upper(), "ALL_STRATEGIES_LONG_HORIZON", req.interval,
        req.n_iterations_per_strategy * n_strategies, results,
        top_strategy=winner["strategy"] if winner else None,
        top_score=winner["score"] if winner else None,
    )
    return {
        "run_id": run_id,
        "bars_used": len(df),
        "data_range": {"start": df.index.min().isoformat(), "end": df.index.max().isoformat()},
        "data_source": "Dukascopy index-CFD (proxy, not the CME futures contract)",
        "results": results,
    }


@router.post("/trainer/long-horizon/run")
async def post_long_horizon_run(req: LongHorizonRequest):
    if req.interval not in dukascopy_source.INTERVAL_PANDAS:
        raise HTTPException(status_code=422, detail=f"interval must be one of {list(dukascopy_source.INTERVAL_PANDAS)}")
    try:
        return await asyncio.to_thread(_run_long_horizon_sync, req)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/trainer/long-horizon/history")
def get_long_horizon_history(symbol: str | None = None, limit: int = 20):
    return {"runs": db.list_optimizer_runs(limit=limit, strategy="ALL_STRATEGIES_LONG_HORIZON", symbol=symbol)}


# ----------------------------------------------------------------- accounts

class CreateAccountRequest(BaseModel):
    name: str
    starting_balance: float = 50_000.0
    account_type: str = "standard"
    tier_size: int | None = None
    daily_loss_limit: float | None = None
    daily_profit_limit: float | None = None


@router.post("/accounts")
def post_account(req: CreateAccountRequest):
    try:
        return ledger.create(
            req.name, req.starting_balance, req.account_type, req.tier_size,
            daily_loss_limit=req.daily_loss_limit,
            daily_profit_limit=req.daily_profit_limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/apex/tiers")
def get_apex_tiers():
    return {"tiers": apex_rules.TIERS}


@router.get("/accounts")
def get_accounts(include_archived: bool = False):
    return {"accounts": ledger.list_all(include_archived=include_archived)}


@router.get("/accounts/equity-curves")
def get_equity_curves():
    return {"curves": ledger.equity_curves()}


@router.get("/accounts/{account_id}")
def get_account(account_id: int):
    acc = ledger.get(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    return acc


class AssignStrategyRequest(BaseModel):
    strategy: str
    symbol: str
    params: dict[str, Any] | None = None


def _guard_system(account_id: int) -> None:
    """The AUTO account is driven by the champion pipeline; hand edits (or a reset/run, which
    wipe its trades) would corrupt the forward record it exists to build."""
    acct = ledger.get(account_id)
    if acct and acct.get("is_system"):
        raise HTTPException(status_code=409, detail="This account is managed by AUTO (it follows the current champion).")


@router.post("/accounts/{account_id}/strategy")
def post_assign_strategy(account_id: int, req: AssignStrategyRequest):
    if not ledger.get(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    _guard_system(account_id)
    try:
        return ledger.assign_strategy(account_id, req.strategy, req.symbol, req.params)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


class RunAccountRequest(BaseModel):
    interval: str = "5m"
    lookback_days: int | None = None


@router.post("/accounts/{account_id}/run")
def post_run_account(account_id: int, req: RunAccountRequest):
    if not ledger.get(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    _guard_system(account_id)  # a history run wipes the account's trades
    try:
        return ledger.run_against_history(account_id, req.interval, req.lookback_days)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/accounts/{account_id}/reset")
def post_reset_account(account_id: int):
    acct = ledger.get(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    _guard_system(account_id)
    if acct.get("traderspost_linked"):
        raise HTTPException(status_code=409, detail="This account is linked to TradersPost for real execution - resetting it would wipe its real trade history. Unlink it first if you need to reset.")
    return ledger.reset(account_id)


@router.post("/accounts/{account_id}/go-live")
def post_go_live(account_id: int):
    if not ledger.get(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        return ledger.go_live(account_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/accounts/{account_id}/stop-live")
def post_stop_live(account_id: int):
    if not ledger.get(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    _guard_system(account_id)
    return ledger.stop_live(account_id)


@router.post("/accounts/{account_id}/backtest-evaluation")
def post_backtest_evaluation(account_id: int):
    if not ledger.get(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        return ledger.backtest_evaluation(account_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/accounts/{account_id}/calendar")
def get_account_calendar(account_id: int, year: int, month: int):
    if not ledger.get(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    trades = db.get_trades_by_month(account_id, year, month)
    days: dict[str, dict[str, Any]] = {}
    for t in trades:
        day = t["exit_time"][:10]
        d = days.setdefault(day, {"pnl": 0.0, "trade_count": 0, "trades": []})
        d["pnl"] = round(d["pnl"] + t["pnl"], 2)
        d["trade_count"] += 1
        d["trades"].append(t)
    return {"year": year, "month": month, "days": days}


@router.get("/live/status")
def get_live_status():
    return live_engine.get_status()


@router.get("/dashboard/summary")
def get_dashboard_summary():
    return ledger.dashboard_summary()


@router.get("/fear-greed")
def get_fear_greed(symbol: str = "NQ"):
    try:
        return fear_greed.compute(symbol.upper())
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int):
    if not ledger.get(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    _guard_system(account_id)
    ledger.delete(account_id)
    return {"deleted": account_id}


@router.get("/accounts/{account_id}/trades")
def get_account_trades(account_id: int):
    if not ledger.get(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    return {"trades": ledger.trades(account_id)}


@router.post("/notify/telegram/test/{account_id}")
def post_telegram_test(account_id: int):
    """Sends a synthetic 'trade opened' Telegram message for this account using its
    real current market data, so the alert format can be checked without waiting for
    a real strategy signal to fire. Does not touch the account's actual balance/position."""
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.get("symbol") or not account.get("strategy"):
        raise HTTPException(status_code=422, detail="Account has no strategy/symbol assigned")
    if not telegram_bot.is_configured():
        raise HTTPException(status_code=422, detail="TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")

    symbol = account["symbol"]
    strategy = registry.get(account["strategy"])
    params = json.loads(account["strategy_params_json"]) if account.get("strategy_params_json") else strategy.default_params()
    df = yfinance_source.fetch(symbol, "5m", max_age_seconds=300)
    if df.empty:
        raise HTTPException(status_code=502, detail="No market data available")

    price = round(float(df.iloc[-1]["close"]), 2)
    open_pos = {
        "side": "long",
        "entry_price": price,
        "stop_price": round(price - price * 0.003, 2),
        "target_price": round(price + price * 0.006, 2),
    }
    msg_id = live_engine._send_telegram_open(account, symbol, strategy, params, open_pos, df)
    if not msg_id:
        raise HTTPException(status_code=502, detail="Telegram send failed — check server logs")
    return {"message_id": msg_id}
