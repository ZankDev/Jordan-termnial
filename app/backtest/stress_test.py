"""VaR-style stress testing: run a strategy specifically through real historical market
stress windows to see how it holds up under crash conditions, not just its average
60-day or multi-year performance.

Crash-window dates and Dukascopy coverage were verified empirically before picking these:
an initial check of 2018/2020/2022 windows came back with zero bars, but that turned out
to be stale cache entries left over from earlier transient fetch failures in this app's
own cache directory, not genuine gaps in Dukascopy's archive — clearing the cache and
re-fetching confirmed real tick data exists for all three classic crashes below.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from app.backtest.engine import run_backtest
from app.data import dukascopy_source
from app.db import BURN_LOSS_FRACTION
from app.strategies.base import Strategy

CRASH_WINDOWS = [
    {
        "name": "Feb 2018 Volmageddon",
        "start": dt.date(2018, 2, 1),
        "end": dt.date(2018, 2, 12),
    },
    {
        "name": "Dec 2018 selloff",
        "start": dt.date(2018, 12, 1),
        "end": dt.date(2018, 12, 26),
    },
    {
        "name": "COVID crash Feb-Mar 2020",
        "start": dt.date(2020, 2, 19),
        "end": dt.date(2020, 3, 23),
    },
]


def run_crash_windows(
    strategy: Strategy,
    params: dict[str, Any],
    symbol: str,
    interval: str = "15m",
    starting_balance: float = 50_000.0,
    sizing=None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for window in CRASH_WINDOWS:
        try:
            df = dukascopy_source.fetch_bars(symbol.upper(), window["start"], window["end"], interval)
        except (ValueError, RuntimeError) as e:
            results.append({"name": window["name"], "error": str(e)})
            continue

        if df.empty:
            results.append({"name": window["name"], "error": "no data available for this window"})
            continue

        bt = run_backtest(df, strategy, params, symbol, starting_balance=starting_balance, sizing=sizing)
        m = bt.metrics
        would_have_burned = m["max_drawdown"] > BURN_LOSS_FRACTION * starting_balance
        results.append(
            {
                "name": window["name"],
                "start": window["start"].isoformat(),
                "end": window["end"].isoformat(),
                "bars_used": len(df),
                "total_trades": m["total_trades"],
                "return_pct": m["return_pct"],
                "net_pnl": m["net_pnl"],
                "max_drawdown": m["max_drawdown"],
                "max_drawdown_pct": m["max_drawdown_pct"],
                "would_have_burned_account": would_have_burned,
            }
        )
    return results
