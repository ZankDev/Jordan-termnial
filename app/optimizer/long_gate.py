"""The long-history gate: does a parameter set make money over YEARS, not just over the 60-day window it was found on?

Why: the fast screen sees ~60 days. A strategy can look perfect there and lose money in most years (the ES fair_value_gap
leader: profit factor 0.71 over 5 years). `promotion._with_history` only relabelled such a strategy "watch"; this module
gives the leader and confirmed tiers, and the automatic real-account follower, a hard yes / no.

long_record():  a backtest of the exact parameters over the longest verified Dukascopy window (risk-sized micro contracts at
                $500 risk, costs and slippage as in the engine). Cached for 24 h per (symbol, strategy, params).
check():        long_record + the verdict {judged, passes, why, record}. `judged=False` (no verified history yet) is never
                treated as a failure by the tiers, but the real-account follower requires judged=True.
family_dead():  True when every parameter set tried so far for this strategy on this symbol loses over the long history, so
                the trainer can stop spending cycles there (it still re-checks now and then).
Kill switch: meta key `long_gate_off` = "1" turns the gate off everywhere.
"""
from __future__ import annotations

import collections
import json
import logging
import time
from typing import Any

from app import db

logger = logging.getLogger(__name__)

MIN_TRADES = 150
MIN_PF = 1.3
MIN_YEARS_POSITIVE = 0.8      # share of (sufficiently traded) calendar years that must be profitable
MIN_YEARS_JUDGED = 3
MIN_TRADES_PER_YEAR = 10      # a calendar year with fewer trades than this is not counted either way
MAX_DRAWDOWN = 1500.0         # at $500 risk per trade; Apex 50K trailing drawdown is $2,000-2,500
CACHE_SECONDS = 24 * 3600
FAMILY_MIN_EVALS = 3
FAMILY_REVIVE_HOURS = 24 * 7  # a "dead" family is re-checked once a week

_mem: dict[str, tuple[float, dict[str, Any]]] = {}


def enabled() -> bool:
    return db.meta_get("long_gate_off") != "1"


def _key(symbol: str, strategy: str, params: dict[str, Any]) -> str:
    from app.optimizer.search import params_hash

    return f"long_rec2:{symbol}:{strategy}:{params_hash(params)}"


def _compute(symbol: str, strategy: str, params: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    from app.backtest.engine import run_backtest
    from app.backtest.sizing import default_config
    from app.optimizer import promotion
    from app.strategies import registry

    months, _cov, _rungs = promotion.deep_plan(symbol)
    if not months:
        return {"judged": False, "why": "no verified multi-year history yet"}
    df = promotion._deep_df(symbol, months)
    if df is None or df.empty:
        return {"judged": False, "why": "no multi-year bars available"}
    res = run_backtest(df, registry.get(strategy), params, symbol, 50_000.0, sizing=default_config(50_000.0))
    trades = res.trades
    if not trades:
        return {"judged": True, "n": 0, "net": 0.0, "pf": 0.0, "months": months, "years": {}}
    pnl = [float(t["pnl"]) for t in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p < 0]
    by_year: dict[str, float] = collections.defaultdict(float)
    n_year: dict[str, int] = collections.defaultdict(int)
    for t in trades:
        y = str(t["exit_time"])[:4]
        by_year[y] += float(t["pnl"])
        n_year[y] += 1
    counted = {y: v for y, v in by_year.items() if n_year[y] >= MIN_TRADES_PER_YEAR}
    eq, peak, dd = 0.0, 0.0, 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    end = df.index.max()
    last6 = sum(float(t["pnl"]) for t in trades if pd.Timestamp(t["exit_time"]) >= end - pd.DateOffset(months=6))
    last12 = sum(float(t["pnl"]) for t in trades if pd.Timestamp(t["exit_time"]) >= end - pd.DateOffset(months=12))
    return {
        "judged": True, "months": months, "n": len(pnl), "net": round(sum(pnl), 2),
        "pf": round(sum(wins) / -sum(losses), 3) if losses else 99.0,
        "wr": round(100 * len(wins) / len(pnl), 1),
        "avg_win": round(sum(wins) / len(wins), 1) if wins else 0.0, "avg_loss": round(sum(losses) / len(losses), 1) if losses else 0.0,
        "max_dd": round(dd, 1), "years": {y: round(v) for y, v in sorted(by_year.items())},
        "years_positive": sum(1 for v in counted.values() if v > 0), "years_judged": len(counted),
        "last6": round(last6, 1), "last12": round(last12, 1), "ts": time.time(),
    }


def long_record(symbol: str, strategy: str, params: dict[str, Any], force: bool = False) -> dict[str, Any]:
    k = _key(symbol, strategy, params)
    hit = _mem.get(k)
    if hit and not force and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    if not force:
        try:
            raw = db.meta_get(k)
            rec = json.loads(raw) if raw else None
        except ValueError:
            rec = None
        if rec and rec.get("judged") and time.time() - rec.get("ts", 0) < CACHE_SECONDS:
            _mem[k] = (time.time(), rec)
            return rec
    try:
        rec = _compute(symbol, strategy, params)
    except Exception as e:  # noqa: BLE001 - a backtest bug must never stop promotion; "not judged" is the safe answer
        logger.exception("long_gate: backtest failed for %s %s", symbol, strategy)
        return {"judged": False, "why": f"long-history backtest failed: {type(e).__name__}"}
    if rec.get("judged"):
        try:
            db.meta_set(k, json.dumps(rec))
        except Exception:  # noqa: BLE001
            pass
        _note_family(symbol, strategy, rec)
    _mem[k] = (time.time(), rec)
    return rec


def verdict(rec: dict[str, Any]) -> tuple[bool, str]:
    """(passes, why) for one long record. Unjudged records never pass here; callers decide what unjudged means."""
    if not rec.get("judged"):
        return False, rec.get("why", "not judged")
    if rec["n"] < MIN_TRADES:
        return False, f"only {rec['n']} trades over {rec['months']} months (need {MIN_TRADES})"
    if rec["pf"] < MIN_PF:
        return False, f"profit factor {rec['pf']:.2f} over {rec['months']} months (need {MIN_PF})"
    if rec["years_judged"] < MIN_YEARS_JUDGED:
        return False, f"only {rec['years_judged']} fully traded years (need {MIN_YEARS_JUDGED})"
    if rec["years_positive"] < MIN_YEARS_POSITIVE * rec["years_judged"]:
        return False, f"profitable in only {rec['years_positive']} of {rec['years_judged']} years"
    if rec["max_dd"] > MAX_DRAWDOWN:
        return False, f"max drawdown ${rec['max_dd']:,.0f} (limit ${MAX_DRAWDOWN:,.0f})"
    if rec["last6"] <= 0:
        return False, f"lost ${-rec['last6']:,.0f} over the newest 6 months"
    return True, f"{rec['n']} trades, PF {rec['pf']:.2f}, {rec['years_positive']}/{rec['years_judged']} years positive, max DD ${rec['max_dd']:,.0f}"


def check(symbol: str, strategy: str, params: dict[str, Any]) -> dict[str, Any]:
    rec = long_record(symbol, strategy, params)
    ok, why = verdict(rec)
    return {"judged": bool(rec.get("judged")), "passes": ok, "why": why, "record": rec}


# ------------------------------------------------------------------ families that never work
def _fam_key(symbol: str, strategy: str) -> str:
    return f"long_fam:{symbol}:{strategy}"


def _note_family(symbol: str, strategy: str, rec: dict[str, Any]) -> None:
    try:
        raw = db.meta_get(_fam_key(symbol, strategy))
        f = json.loads(raw) if raw else {"n": 0, "best_pf": 0.0, "any_good": False}
    except ValueError:
        f = {"n": 0, "best_pf": 0.0, "any_good": False}
    f["n"] += 1
    f["best_pf"] = max(f.get("best_pf", 0.0), rec.get("pf", 0.0) if rec.get("n", 0) >= 30 else 0.0)
    f["any_good"] = bool(f.get("any_good") or (rec.get("net", 0) > 0 and rec.get("pf", 0) >= 1.1 and rec.get("n", 0) >= 60))
    f["ts"] = time.time()
    db.meta_set(_fam_key(symbol, strategy), json.dumps(f))


def family_dead(symbol: str, strategy: str) -> bool:
    """Every long record seen for this strategy on this symbol is a loser (at least FAMILY_MIN_EVALS of them)."""
    try:
        raw = db.meta_get(_fam_key(symbol, strategy))
        f = json.loads(raw) if raw else None
    except ValueError:
        return False
    if not f or f["n"] < FAMILY_MIN_EVALS or f.get("any_good"):
        return False
    return (time.time() - f.get("ts", 0)) < FAMILY_REVIVE_HOURS * 3600


def symbol_has_edge(symbol: str) -> bool:
    """True once any strategy on this symbol has a long record that clears the full gate (cached records only)."""
    prefix = f"long_rec2:{symbol}:"
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT value FROM meta WHERE key LIKE ?", (prefix + "%",)).fetchall()
    except Exception:  # noqa: BLE001
        return True  # unknown: do not starve the symbol
    for r in rows:
        try:
            rec = json.loads(r[0])
        except ValueError:
            continue
        if rec.get("judged") and verdict(rec)[0]:
            return True
    return False
