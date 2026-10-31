"""Polymarket backtest engine — replays a strategy tick-by-tick through each
resolved session's stored price trace and produces a PolymarketTrade (or none) per
session, then rolls them into the same metric shape as app/backtest/engine.py's
BacktestResult wherever the concept transfers (edge_t_stat, profit_factor,
max_drawdown_pct, ...) so the trainer/Field-page plumbing (Phases 4/8) needs no new
number formats.

Polymarket-shaped P&L: no ticks, no contract multiplier — shares = stake_usd /
entry_price, pnl = (exit_price - entry_price) * shares, long-only on whichever
outcome token the strategy bought (there is no "short" on a binary market; buying
the other side IS the short).

Only the session's Up-token price is stored (pm_session_prices); the Down price is
approximated as (1 - up_price) — ignoring the real bid/ask spread, a documented
simplification consistent with the plan's flagged risks (backfilled fidelity is
coarser than live).
"""
from __future__ import annotations

import math
from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

from app.polymarket import store
from app.polymarket.registry import get as get_strategy
from app.polymarket.strategy_base import BuzzerSession, PolymarketTrade

STAKE_USD = 5.0  # flat per-session stake for backtest/training purposes (no Kelly/CVaR sizing — see momo_strategy.py docstring)


@dataclass
class PmBacktestResult:
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


# One-tick adverse fill on every non-settlement fill (buy at the ask, sell at the
# bid): the CLOB price series the replay walks is a last-trade/mid line, and paying
# the touch on a 5-minute scalp is a real cost that flatters the strategy if ignored.
SLIPPAGE = 0.01
TRACE_TAIL = 90  # spot points handed to the gate/exit checks (>= their longest lookback)


@dataclass
class SessionData:
    """Everything one replay needs, held in memory so a candidate evaluation never
    touches the database (the trainer replays every session once per candidate)."""
    id: int
    slug: str
    asset: str
    start_ts: int
    end_ts: int
    strike: float
    sigma: float | None
    resolved_up: bool
    ticks: list[tuple[float, float]]
    spot_t: "array"
    spot_p: "array"


_CACHE: dict[int, SessionData] = {}


def clear_cache() -> None:
    _CACHE.clear()


def retain(ids: set[int]) -> None:
    """Drop cached sessions outside the current training window (bounds memory)."""
    for k in [k for k in _CACHE if k not in ids]:
        _CACHE.pop(k, None)


def preload(sessions: list[dict[str, Any]]) -> None:
    """Bulk-load price ticks + spot traces for any session not yet cached."""
    todo = [s for s in sessions if s["id"] not in _CACHE and s["resolved_up"] is not None and s["strike_price"] is not None]
    if not todo:
        return
    ids = [s["id"] for s in todo]
    prices = store.get_prices_bulk(ids)
    spots = store.get_spot_bulk(ids)
    for s in todo:
        ticks = [(t, p) for t, p in prices.get(s["id"], []) if t <= s["end_ts"]]
        sp = spots.get(s["id"]) or [(s["start_ts"], s["strike_price"]), (s["end_ts"], s["final_spot"] or s["strike_price"])]
        _CACHE[s["id"]] = SessionData(
            id=s["id"], slug=s["slug"], asset=s["asset"], start_ts=s["start_ts"], end_ts=s["end_ts"],
            strike=s["strike_price"], sigma=s["sigma_per_min_pct"], resolved_up=bool(s["resolved_up"]),
            ticks=ticks, spot_t=array("i", [int(t) for t, _ in sp]), spot_p=array("f", [p for _, p in sp]),
        )


def replay_session(sess_row: dict[str, Any], strategy_name: str, params: dict[str, Any], stake_usd: float = STAKE_USD) -> PolymarketTrade | None:
    """Walks one resolved session's real price ticks, applying the strategy's entry
    gate then (once filled) its exit ladder, settling at the true resolution ($1/$0)
    if neither fires before the session ends."""
    if sess_row["id"] not in _CACHE:
        preload([sess_row])
    strat = get_strategy(strategy_name)
    return _replay(_CACHE.get(sess_row["id"]), strat, {**strat["default_params"](), **params}, stake_usd)


def _replay(sd: "SessionData | None", strat: dict[str, Any], p: dict[str, Any], stake_usd: float) -> PolymarketTrade | None:
    if sd is None or not sd.ticks:
        return None
    gate, exit_ = strat["gate_check"], strat["exit_check"]
    lo, hi = p["momo_min_secs"], p["momo_secs"]
    pmin, pmax = p["momo_min_px"], p["momo_max_px"]
    position: dict[str, Any] | None = None
    entry_time = None

    for t, up_price in sd.ticks:
        down_price = max(0.0, min(1.0, 1.0 - up_price))
        if position is None:
            remaining = sd.end_ts - t
            if remaining > hi or remaining < lo:
                continue
            fav = up_price if up_price >= down_price else down_price
            if fav < pmin or fav > pmax:
                continue
        idx = bisect_right(sd.spot_t, t)
        spot_now = sd.spot_p[idx - 1] if idx else sd.strike
        lo_i = max(0, idx - TRACE_TAIL)
        session = BuzzerSession(
            slug=sd.slug, asset=sd.asset, start_ts=sd.start_ts, end_ts=sd.end_ts, now_ts=t, strike=sd.strike,
            spot_now=spot_now, up_price=up_price, down_price=down_price, sigma_per_min_pct=sd.sigma,
            spot_trace=list(zip(sd.spot_t[lo_i:idx], sd.spot_p[lo_i:idx])),
        )
        if position is None:
            sig = gate(session, p)
            if sig.enter:
                position = {"side": sig.side, "entry_price": min(0.99, sig.price + SLIPPAGE), "peak_pct": None}
                entry_time = t
        else:
            reason = exit_(position, session, p)
            if reason:
                mark = up_price if position["side"] == "up" else down_price
                exit_price = max(0.0, mark - SLIPPAGE)
                shares = stake_usd / position["entry_price"]
                return PolymarketTrade(
                    session_slug=sd.slug, asset=sd.asset, side=position["side"], entry_time=entry_time, exit_time=t,
                    entry_price=position["entry_price"], exit_price=exit_price, shares=shares,
                    pnl=(exit_price - position["entry_price"]) * shares, exit_reason=reason,
                )

    if position is not None:
        won = sd.resolved_up if position["side"] == "up" else not sd.resolved_up
        exit_price = 1.0 if won else 0.0
        shares = stake_usd / position["entry_price"]
        return PolymarketTrade(
            session_slug=sd.slug, asset=sd.asset, side=position["side"], entry_time=entry_time, exit_time=sd.end_ts,
            entry_price=position["entry_price"], exit_price=exit_price, shares=shares,
            pnl=(exit_price - position["entry_price"]) * shares, exit_reason="settlement",
        )
    return None


def run_pm_backtest(
    strategy_name: str, params: dict[str, Any], sessions: list[dict[str, Any]],
    starting_balance: float = 1_000.0, stake_usd: float = STAKE_USD,
) -> PmBacktestResult:
    """Replays a strategy over a pooled, cross-asset list of resolved session rows
    (see store.list_resolved_sessions), sorted by end_ts, and rolls the resulting
    trades into futures-metric-shaped numbers."""
    sessions = sorted(sessions, key=lambda s: s["end_ts"])
    preload(sessions)
    strat = get_strategy(strategy_name)
    full = {**strat["default_params"](), **params}  # merged once, not once per session
    equity = starting_balance
    peak = starting_balance
    max_dd = max_dd_pct = 0.0
    wins: list[float] = []
    losses: list[float] = []
    trade_rows: list[dict[str, Any]] = []
    equity_curve = [{"time": None, "equity": equity}]

    for sess_row in sessions:
        trade = _replay(_CACHE.get(sess_row["id"]), strat, full, stake_usd)
        if trade is None:
            continue
        equity += trade.pnl
        peak = max(peak, equity)
        dd = peak - equity
        dd_pct = (dd / peak * 100) if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)
        (wins if trade.pnl > 0 else losses).append(trade.pnl)
        trade_rows.append({
            "session_slug": trade.session_slug, "asset": trade.asset, "side": trade.side,
            "entry_time": trade.entry_time, "exit_time": trade.exit_time,
            "entry_price": round(trade.entry_price, 4), "exit_price": round(trade.exit_price, 4),
            "shares": round(trade.shares, 4), "pnl": round(trade.pnl, 4),
            "equity_after": round(equity, 2), "exit_reason": trade.exit_reason,
        })
        equity_curve.append({"time": trade.exit_time, "equity": round(equity, 2)})

    total_trades = len(trade_rows)
    net_pnl = equity - starting_balance
    win_rate = (len(wins) / total_trades * 100) if total_trades else 0.0
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)

    all_pnls = wins + losses
    edge_t_stat = 0.0
    if len(all_pnls) > 1:
        mean_pnl = sum(all_pnls) / len(all_pnls)
        variance = sum((p - mean_pnl) ** 2 for p in all_pnls) / (len(all_pnls) - 1)
        std_pnl = math.sqrt(variance)
        if std_pnl > 0:
            edge_t_stat = mean_pnl / std_pnl * math.sqrt(len(all_pnls))

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    reward_risk_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else (None if not wins else math.inf)

    metrics = {
        "total_trades": total_trades,
        "n_sessions": len(sessions),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 3) if math.isfinite(profit_factor) else None,
        "net_pnl": round(net_pnl, 4),
        "return_pct": round(net_pnl / starting_balance * 100, 2) if starting_balance else 0.0,
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "edge_t_stat": round(edge_t_stat, 3),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "reward_risk_ratio": round(reward_risk_ratio, 3) if reward_risk_ratio is not None and math.isfinite(reward_risk_ratio) else None,
    }
    return PmBacktestResult(trades=trade_rows, equity_curve=equity_curve, metrics=metrics)
