"""PA objective: judge a strategy / portfolio by what an Apex PA payout account needs, not by a plain pass-the-evaluation score.

A PA account is paid by the day: a steady positive day, a hard daily loss stop, a daily profit cap, at least one trade a day, a trailing drawdown that must
never be hit, and Apex's payout rules (5 qualifying days, no single day above 50% of the profit). `evaluate()` replays trades through those daily rules and
returns one metrics dict; `meets()` says whether the user's targets are hit; `fitness()` ranks candidates that miss them (higher is better, and any candidate that
would breach the drawdown is pushed far down). Used by scripts/pa_search.py (the PA strategy search) - the trainer's candidates can call the same functions.

Targets live in TARGETS and can be overridden through the meta key `pa_targets` (JSON). Trades are (entry_time, exit_time, pnl, symbol) tuples at the $500-risk size.
"""
from __future__ import annotations

import collections
import json
from typing import Any, Iterable

import numpy as np
import pandas as pd

TARGETS: dict[str, float] = {
    "avg_day": 300.0,          # mean $ per trading day (all weekdays, no-trade days count as 0)
    "loss_limit": 600.0,       # stop trading for the day once closed P&L is this far down
    "profit_cap": 1200.0,      # stop trading for the day once closed P&L is this far up
    "min_trade_days": 0.85,    # share of weekdays with at least one trade
    "max_breach60": 0.10,      # chance of hitting the trailing drawdown inside 60 trading days
    "drawdown": 2500.0,        # Apex 50K trailing drawdown
    "min_qualifying_day": 150.0,   # PA payout: a qualifying day is at least this much profit
    "max_best_day_share": 0.50,    # PA consistency rule: best day / total profit
}


def targets() -> dict[str, float]:
    t = dict(TARGETS)
    try:
        from app import db

        raw = db.meta_get("pa_targets")
        if raw:
            t.update({k: float(v) for k, v in json.loads(raw).items() if k in t})
    except Exception:  # noqa: BLE001 - a bad override must never break a search
        pass
    return t


def daily_rules(trades: Iterable[tuple], loss_limit: float, profit_cap: float, scale: float = 1.0):
    """-> ({day: pnl}, {day: n_trades}). A trade is skipped when the day's CLOSED P&L is already at the loss limit / cap when it would start, and a trade never
    overlaps another on the same symbol. The day label is the entry date in New York."""
    ts = sorted(((pd.Timestamp(a), pd.Timestamp(b), p * scale, s) for a, b, p, s in trades), key=lambda x: x[0])
    last_exit: dict[str, Any] = {}
    by_day: dict[str, list] = collections.defaultdict(list)
    out: dict[str, float] = collections.defaultdict(float)
    n: dict[str, int] = collections.defaultdict(int)
    for a, b, p, s in ts:
        day = str(a.date())
        if s in last_exit and a < last_exit[s]:
            continue
        closed = sum(x for (e, x) in by_day[day] if e <= a)
        if closed <= -loss_limit or closed >= profit_cap:
            continue
        by_day[day].append((b, p))
        last_exit[s] = b
        out[day] += p
        n[day] += 1
    return out, n


def evaluate(trades: list[tuple], days: list[str], scale: float = 1.0, tg: dict[str, float] | None = None, seed: int = 1, boots: int = 2000) -> dict[str, Any]:
    tg = tg or targets()
    d, n = daily_rules(trades, tg["loss_limit"], tg["profit_cap"], scale)
    s = np.array([d.get(x, 0.0) for x in days])
    cnt = np.array([n.get(x, 0) for x in days])
    if len(s) == 0:
        return {"mean": 0.0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(boots, 60))
    eq = np.cumsum(s[idx], axis=1)
    peak = np.maximum.accumulate(np.maximum(eq, 0))
    breach = float(((peak - eq) > tg["drawdown"]).any(axis=1).mean())
    cum = np.cumsum(s)
    total = float(s.sum())
    return {
        "mean": round(float(s.mean()), 1), "tpd": round(float(cnt.mean()), 2), "trade_days": round(float((cnt >= 1).mean()), 3),
        "best": round(float(s.max())), "worst": round(float(s.min())), "dd": round(float((np.maximum.accumulate(cum) - cum).max())),
        "breach60": round(breach, 3), "median60": round(float(np.median(eq[:, -1]))),
        "qualifying_days": int((s >= tg["min_qualifying_day"]).sum()),
        "best_day_share": round(float(s.max() / total), 2) if total > 0 else 1.0,
        "scale": scale, "total": round(total),
    }


def meets(m: dict[str, Any], tg: dict[str, float] | None = None) -> tuple[bool, list[str]]:
    """(all targets hit, the list of targets still missed)."""
    tg = tg or targets()
    miss = []
    if m.get("mean", 0) < tg["avg_day"]:
        miss.append(f"average day ${m.get('mean', 0):.0f} < ${tg['avg_day']:.0f}")
    if m.get("trade_days", 0) < tg["min_trade_days"]:
        miss.append(f"trades on {m.get('trade_days', 0) * 100:.0f}% of days < {tg['min_trade_days'] * 100:.0f}%")
    if m.get("breach60", 1) > tg["max_breach60"]:
        miss.append(f"drawdown breach {m.get('breach60', 1) * 100:.0f}% > {tg['max_breach60'] * 100:.0f}%")
    if m.get("best_day_share", 1) > tg["max_best_day_share"]:
        miss.append(f"best day is {m.get('best_day_share', 1) * 100:.0f}% of profit (> {tg['max_best_day_share'] * 100:.0f}%)")
    return (not miss), miss


def fitness(m: dict[str, Any], tg: dict[str, float] | None = None) -> float:
    """Higher is better. Average day, discounted by the drawdown-breach chance and by days without a trade; a likely breach is crushed."""
    tg = tg or targets()
    if m.get("mean", 0) <= 0:
        return m.get("mean", 0) - 1000
    f = m["mean"] * (1 - min(1.0, m["breach60"] / max(tg["max_breach60"], 1e-6)) * 0.9) * (0.5 + 0.5 * min(1.0, m["trade_days"] / tg["min_trade_days"]))
    return round(f - (1000 if m["breach60"] > 0.5 else 0), 2)
