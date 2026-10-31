"""Turns a strategy's trade list into an equity curve and performance metrics."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.backtest.sizing import LEGACY_MODE, SizingConfig, size_position
from app.strategies.base import Strategy, Trade

# CME futures contract multipliers ($ per index point)
CONTRACT_MULTIPLIER = {"NQ": 20.0, "ES": 50.0}

# Real minimum price increments — every fill should land on one of these,
# not an arbitrary float, so results reflect prices you could actually get.
TICK_SIZE = {"NQ": 0.25, "ES": 0.25}

DEFAULT_COMMISSION_PER_TRADE = 5.0  # flat round-turn estimate
STOP_SLIPPAGE_TICKS = 1  # conservative: stops fill a tick worse than the exact level
ENTRY_SLIPPAGE_TICKS = 1  # market entries: spread + slippage (see adjust_trade_prices)
# A resting limit order gets filled at (or better than) its price instead of paying the
# market spread/slippage a market order eats on entry — approximated here as a fixed
# favorable tick improvement on the entry fill. Doesn't model the real risk of a limit
# order never filling at all; that tradeoff is a documented simplification, not modeled.
LIMIT_ENTRY_IMPROVEMENT_TICKS = 2


@dataclass
class BacktestResult:
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def round_to_tick(price: float, symbol: str) -> float:
    tick = TICK_SIZE.get(symbol.upper(), 0.25)
    return round(round(price / tick) * tick, 2)


def adjust_trade_prices(trade: Trade, symbol: str, entry_style: str = "market") -> tuple[float, float, float, float]:
    """Snap a trade's prices to real tick increments, and apply one tick of
    adverse slippage on stop-loss fills (targets fill clean — a resting limit,
    not a stop, so no slippage assumption there). entry_style="limit" applies a
    favorable tick improvement to the entry fill (see LIMIT_ENTRY_IMPROVEMENT_TICKS)."""
    tick = TICK_SIZE.get(symbol.upper(), 0.25)
    entry_price = trade.entry_price
    direction = 1 if trade.side == "long" else -1
    if entry_style == "limit":
        entry_price -= direction * LIMIT_ENTRY_IMPROVEMENT_TICKS * tick
    else:
        # a market entry pays the spread + slippage: NQ/ES quote a 1-tick spread, so fill ~1 tick worse than the signal.
        # (Futures carry no overnight swap; exchange/clearing fees are inside the commission.)
        entry_price += direction * ENTRY_SLIPPAGE_TICKS * tick
    entry_price = round_to_tick(entry_price, symbol)
    exit_price = trade.exit_price
    if trade.exit_reason == "stop":
        exit_price += -tick if trade.side == "long" else tick
    exit_price = round_to_tick(exit_price, symbol)
    stop_price = round_to_tick(trade.stop_price, symbol)
    target_price = round_to_tick(trade.target_price, symbol)
    return entry_price, exit_price, stop_price, target_price


def _resample_higher_timeframe(df: pd.DataFrame, multiplier: int) -> pd.DataFrame:
    freq = df.index.to_series().diff().median()
    rule = freq * multiplier
    htf = df.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    return htf


def _htf_confirms(htf: pd.DataFrame, entry_time, side: str, confirm_bars: int) -> bool:
    """Require the last `confirm_bars` CLOSED higher-timeframe candles before entry_time
    to all close in the trade's direction (up-candles for a long, down-candles for a
    short) — a simple higher-timeframe trend-confirmation filter."""
    prior = htf[htf.index < entry_time]
    if len(prior) < confirm_bars:
        return False
    recent = prior.iloc[-confirm_bars:]
    if side == "long":
        return bool((recent["close"] > recent["open"]).all())
    return bool((recent["close"] < recent["open"]).all())


def compute_trade_pnl(entry_price: float, exit_price: float, side: str, multiplier: float, commission: float) -> float:
    direction = 1 if side == "long" else -1
    points = (exit_price - entry_price) * direction
    return points * multiplier - commission


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    params: dict[str, Any],
    symbol: str,
    starting_balance: float = 50_000.0,
    commission_per_trade: float = DEFAULT_COMMISSION_PER_TRADE,
    entry_style: str = "market",
    htf_confirm_bars: int = 0,
    htf_interval_multiplier: int = 4,
    sizing: SizingConfig | None = None,
) -> BacktestResult:
    """sizing=None (or LEGACY) is the historical behaviour: every trade is 1 mini at a flat
    commission. A risk_micro SizingConfig sizes each trade to a fixed dollar risk in micro
    contracts (see app/backtest/sizing.py), skipping trades whose minimum size still risks
    too much."""
    multiplier = CONTRACT_MULTIPLIER.get(symbol.upper(), 20.0)
    legacy = sizing is None or sizing.mode == LEGACY_MODE
    tick = TICK_SIZE.get(symbol.upper(), 0.25)
    trades = strategy.generate_trades(df, params)
    trades.sort(key=lambda t: t.entry_time)

    if htf_confirm_bars > 0 and trades:
        htf = _resample_higher_timeframe(df, htf_interval_multiplier)
        trades = [t for t in trades if _htf_confirms(htf, t.entry_time, t.side, htf_confirm_bars)]

    equity = starting_balance
    peak = starting_balance
    max_dd = 0.0
    max_dd_pct = 0.0
    wins: list[float] = []
    losses: list[float] = []
    total_commission = 0.0
    trade_rows: list[dict[str, Any]] = []
    equity_curve = [{"time": None, "equity": equity}]
    taken = 0
    skipped_oversize = 0
    sum_risk = sum_qty = 0.0
    # R-based accumulators (additive, so metrics from several folds pool exactly)
    n_wins_gross = 0
    sum_win_r = sum_loss_r = sum_win_r_gross = sum_loss_r_gross = 0.0
    sum_r = sum_r_gross = sum_cost_r = 0.0

    for t in trades:
        entry_price, exit_price, stop_price, target_price = adjust_trade_prices(t, symbol, entry_style)
        if legacy:
            qty, pv, commission, contract = 1.0, multiplier, commission_per_trade, "mini"
        else:
            sp = size_position(symbol, entry_price, stop_price, sizing, tick)
            if sp is None:
                skipped_oversize += 1
                continue
            qty, pv, commission, contract = sp.qty, sp.point_value, sp.commission, sp.contract
        direction = 1 if t.side == "long" else -1
        pnl = (exit_price - entry_price) * direction * qty * pv - commission
        risk_dollars = abs(entry_price - stop_price) * qty * pv
        taken += 1
        sum_risk += risk_dollars
        sum_qty += qty
        if risk_dollars > 0:
            r_net, r_gross = pnl / risk_dollars, (pnl + commission) / risk_dollars
            sum_r += r_net
            sum_r_gross += r_gross
            sum_cost_r += commission / risk_dollars
            if r_net > 0:
                sum_win_r += r_net
            else:
                sum_loss_r += -r_net
            if r_gross > 0:
                n_wins_gross += 1
                sum_win_r_gross += r_gross
            else:
                sum_loss_r_gross += -r_gross
        mfe_pnl = mae_pnl = None
        if t.mfe_price is not None and t.mae_price is not None:
            mfe_pnl = round((t.mfe_price - entry_price) * direction * qty * pv, 2)
            mae_pnl = round((t.mae_price - entry_price) * direction * qty * pv, 2)
        total_commission += commission
        equity += pnl
        peak = max(peak, equity)
        dd = peak - equity
        dd_pct = (dd / peak * 100) if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)

        (wins if pnl > 0 else losses).append(pnl)

        trade_rows.append(
            {
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "side": t.side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "exit_reason": t.exit_reason,
                "pnl": round(pnl, 2),
                "equity_after": round(equity, 2),
                "qty": qty,
                "contract": contract,
                "commission": commission,
                "risk_dollars": round(risk_dollars, 2),
                "mfe_pnl": mfe_pnl,
                "mae_pnl": mae_pnl,
            }
        )
        equity_curve.append({"time": t.exit_time.isoformat(), "equity": round(equity, 2)})

    total_trades = taken
    net_pnl = equity - starting_balance
    win_count = len(wins)
    win_rate = (win_count / total_trades * 100) if total_trades else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)

    all_pnls = wins + losses
    if len(all_pnls) > 1:
        mean_pnl = sum(all_pnls) / len(all_pnls)
        variance = sum((p - mean_pnl) ** 2 for p in all_pnls) / (len(all_pnls) - 1)
        std_pnl = math.sqrt(variance)
        # Kept for display/backward compatibility. NOT sound for ranking: sqrt(252) is the
        # annualization constant for *daily* return series, not per-trade pnls, so it
        # over-inflates confidence the smaller the trade count is (backwards from what a
        # ranking metric should do). Use edge_t_stat below for anything that ranks candidates.
        sharpe = (mean_pnl / std_pnl * math.sqrt(252)) if std_pnl > 0 else 0.0
        # One-sample t-statistic for "is mean pnl different from zero", using the actual
        # number of trades instead of a fixed constant. Naturally shrinks small-sample luck
        # (e.g. a 5-trade win streak) instead of amplifying it, while still rewarding a large,
        # consistent sample. This is what search.py should rank candidates by.
        edge_t_stat = (mean_pnl / std_pnl * math.sqrt(len(all_pnls))) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0
        edge_t_stat = 0.0

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    # Reward:risk ratio — repeatedly the deciding factor in whether a strategy survives
    # a mediocre win rate or loses despite a high one; surfaced as its own metric instead
    # of leaving it implicit in avg_win/avg_loss.
    reward_risk_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else (None if not wins else math.inf)

    metrics = {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 3) if math.isfinite(profit_factor) else None,
        "net_pnl": round(net_pnl, 2),
        # Cost-adjusted (net) vs. before-commission (gross) result, so the cost impact is
        # visible directly rather than a raw backtest silently understating what commissions
        # actually cost — slippage on stop fills is already baked into both since it comes
        # from the fill price itself, not a separate deduction.
        "gross_pnl": round(net_pnl + total_commission, 2),
        "total_commission": round(total_commission, 2),
        "return_pct": round(net_pnl / starting_balance * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_approx": round(sharpe, 3),
        "edge_t_stat": round(edge_t_stat, 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "reward_risk_ratio": round(reward_risk_ratio, 3) if reward_risk_ratio is not None and math.isfinite(reward_risk_ratio) else None,
        "ending_equity": round(equity, 2),
        # --- sizing / R-multiple metrics (feed the Field page and the Apex replay) ---
        "skipped_oversize": skipped_oversize,
        "avg_qty": round(sum_qty / taken, 2) if taken else 0.0,
        "avg_risk_dollars": round(sum_risk / taken, 2) if taken else 0.0,
        "win_rate_gross_pct": round(n_wins_gross / taken * 100, 2) if taken else 0.0,
        "expectancy_r_net": round(sum_r / taken, 4) if taken else 0.0,
        "expectancy_r_gross": round(sum_r_gross / taken, 4) if taken else 0.0,
        "avg_cost_r": round(sum_cost_r / taken, 4) if taken else 0.0,
        "rr_net_r": round((sum_win_r / win_count) / (sum_loss_r / (taken - win_count)), 3)
        if win_count and taken - win_count and sum_loss_r > 0 else None,
        "rr_gross_r": round((sum_win_r_gross / n_wins_gross) / (sum_loss_r_gross / (taken - n_wins_gross)), 3)
        if n_wins_gross and taken - n_wins_gross and sum_loss_r_gross > 0 else None,
        "n_wins": win_count,
        "n_wins_gross": n_wins_gross,
        "sum_win_r": round(sum_win_r, 4),
        "sum_loss_r": round(sum_loss_r, 4),
        "sum_win_r_gross": round(sum_win_r_gross, 4),
        "sum_loss_r_gross": round(sum_loss_r_gross, 4),
        "sum_r": round(sum_r, 4),
        "sum_r_gross": round(sum_r_gross, 4),
        "sum_cost_r": round(sum_cost_r, 4),
    }

    return BacktestResult(trades=trade_rows, equity_curve=equity_curve, metrics=metrics)
