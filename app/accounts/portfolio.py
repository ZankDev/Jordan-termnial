"""Combos: do 2-3 strategies traded together in ONE $50k evaluation pass more often than the best single strategy?

Idea: strategies whose losing days do not line up smooth the equity curve, and on a trailing-drawdown evaluation a
smoother curve is worth more than a slightly higher edge. Method:
  * every strategy's pooled trade record (insights._pooled_trades: live + simulated + archived, de-duplicated) is bucketed
    into daily P&L; a combo's day = the sum of its members' days (days with no trade = 0), so same-day correlation is kept;
  * block bootstrap over the combo's trading days (a day's trades stay together), 60-day evaluations, Apex rules
    (profit target $3,000, trailing drawdown $2,000 that locks at the start balance), checked at each day close;
  * each member is traded at a common risk scale; the scale with the best pass-minus-breach is reported.
Only strategies with >= MIN_TRADES trades and a positive record are combined (a loser does not become a winner by
company). Results are cached; this is research output, it never changes an account by itself.
"""
from __future__ import annotations

import itertools
import time
from collections import defaultdict
from typing import Any

import numpy as np

from app.accounts.insights import _pooled_trades

MIN_TRADES = 30
EVAL_DAYS = 60
PATHS = 2000
SCALES = (0.25, 0.5, 0.75, 1.0, 1.25)
START, TARGET, MAX_DD = 50_000.0, 3_000.0, 2_000.0
_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def _daily(trades: list[dict[str, Any]]) -> dict[str, float]:
    d: dict[str, float] = defaultdict(float)
    for t in trades:
        d[str(t["exit_time"])[:10]] += float(t["pnl"])
    return dict(d)


def _sim(days: np.ndarray, rng: np.random.Generator) -> dict[str, float]:
    """days: 1-D array of daily P&L of the combo (every calendar trading day in the members' span, zeros included)."""
    out = {}
    idx = rng.integers(0, len(days), size=(PATHS, EVAL_DAYS))
    base = days[idx]
    for s in SCALES:
        eq = START + np.cumsum(base * s, axis=1)
        peak = np.maximum.accumulate(np.maximum(eq, START), axis=1)
        floor = np.minimum(peak - MAX_DD, START)
        br = eq <= floor
        ps = eq >= START + TARGET
        big = EVAL_DAYS + 1
        b = np.where(br.any(1), br.argmax(1), big)
        p = np.where(ps.any(1), ps.argmax(1), big)
        won = p < b
        lost = (b <= p) & (b < big)
        out[s] = (float(won.mean()), float(lost.mean()), float(np.median(p[won]) + 1) if won.any() else None)
    return out


def _combos(members: dict[str, dict[str, float]], info: dict[str, dict[str, Any]], max_members: int, min_days: int) -> list[dict[str, Any]]:
    all_days = sorted({d for m in members.values() for d in m})
    rng = np.random.default_rng(11)
    rows = []
    for r in range(1, max_members + 1):
        for combo in itertools.combinations(sorted(members), r):
            if len({(info[k]["strategy"], info[k]["symbol"]) for k in combo}) < r:
                continue  # the same strategy twice (different parameters) is one bet, not a diversified pair
            spans = [(min(members[k]), max(members[k])) for k in combo]
            lo, hi = max(s[0] for s in spans), min(s[1] for s in spans)
            days = [d for d in all_days if lo <= d <= hi]
            if len(days) < min_days:
                continue
            arr = np.array([sum(members[k].get(d, 0.0) for k in combo) for d in days])
            res = _sim(arr, rng)
            best_s = max(res, key=lambda s: (res[s][0] - res[s][1], -abs(s - 1)))
            p, b, med = res[best_s]
            corr = None
            if r == 2:
                a1 = np.array([members[combo[0]].get(d, 0.0) for d in days]); a2 = np.array([members[combo[1]].get(d, 0.0) for d in days])
                if a1.std() > 0 and a2.std() > 0:
                    corr = round(float(np.corrcoef(a1, a2)[0, 1]), 2)
            yrs: dict[str, float] = defaultdict(float)
            for d, v in zip(days, arr):
                yrs[d[:4]] += v * best_s
            rows.append({"members": [info[k] for k in combo], "size": r, "days": len(days), "best_scale": best_s,
                         "pass": round(100 * p, 1), "breach": round(100 * b, 1), "edge": round(100 * (p - b), 1),
                         "median_days_to_pass": med, "daily_corr": corr, "by_year": {y: round(v) for y, v in sorted(yrs.items())},
                         "by_scale": [{"scale": s, "pass": round(100 * v[0], 1), "breach": round(100 * v[1], 1)} for s, v in res.items()]})
    rows.sort(key=lambda x: -x["edge"])
    return rows


def analyse_long(symbols: tuple[str, ...] = ("NQ", "ES"), max_members: int = 3, force: bool = False) -> dict[str, Any]:
    """Same combo test on YEARS of history: every distinct (strategy, symbol, params) an account actually runs, plus the
    trainer's champions and best long-history parameter sets, backtested over the verified Dukascopy look-back (risk-sized,
    realistic costs). Members whose own long record loses money are left out."""
    key = f"long:{','.join(symbols)}:{max_members}"
    hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    import json as _json

    from app import db
    from app.backtest.engine import run_backtest
    from app.backtest.sizing import default_config
    from app.optimizer import promotion
    from app.optimizer.search import params_hash
    from app.strategies import registry

    cands: dict[str, dict[str, Any]] = {}
    def add(strategy: str, symbol: str, params: dict[str, Any], src: str) -> None:
        k = f"{strategy}|{symbol}|{params_hash(params)}"
        cands.setdefault(k, {"strategy": strategy, "symbol": symbol, "params": params, "src": src})
    for a in db.list_accounts():
        if a.get("strategy") and a.get("symbol") in symbols:
            add(a["strategy"], a["symbol"], _json.loads(a["strategy_params_json"]), "account")
    for sym in symbols:
        for tier in ("leader", "confirmed"):
            s = db.get_slot(sym, tier)
            if s:
                add(s["strategy"], sym, s["params"], tier)
        try:
            el = _json.loads(db.meta_get(f"deep_elite_{sym}") or "{}")
        except ValueError:
            el = {}
        for strat, lst in el.items():
            if lst:
                add(strat, sym, lst[0]["params"], "deep")
    members: dict[str, dict[str, float]] = {}
    info: dict[str, dict[str, Any]] = {}
    tested: list[dict[str, Any]] = []
    accts = db.list_accounts()
    for sym in symbols:
        months, _c, _r = promotion.deep_plan(sym)
        if not months:
            continue
        df = promotion._deep_df(sym, months)
        if df is None or df.empty:
            continue
        for k, c in cands.items():
            if c["symbol"] != sym:
                continue
            try:
                res = run_backtest(df, registry.get(c["strategy"]), c["params"], sym, START, sizing=default_config(START))
            except Exception:  # noqa: BLE001
                continue
            tr = res.trades
            net = sum(t["pnl"] for t in tr)
            yrs: dict[str, float] = defaultdict(float)
            for t in tr:
                yrs[str(t["exit_time"])[:4]] += t["pnl"]
            tested.append({"strategy": c["strategy"], "symbol": sym, "src": c["src"], "n": len(tr), "net": round(net, 2), "months": months,
                           "pf": round(sum(t["pnl"] for t in tr if t["pnl"] > 0) / max(1e-9, -sum(t["pnl"] for t in tr if t["pnl"] < 0)), 2) if tr else None,
                           "years_positive": sum(1 for v in yrs.values() if v > 0), "years": len(yrs),
                           "accounts": [a["id"] for a in accts if a.get("strategy") == c["strategy"] and a.get("symbol") == sym
                                        and params_hash(_json.loads(a["strategy_params_json"])) == params_hash(c["params"])]})
            if len(tr) < 100 or net <= 0:
                continue
            members[k] = _daily(tr)
            info[k] = {"strategy": c["strategy"], "symbol": sym, "src": c["src"], "n": len(tr), "net": round(net, 2), "months": months,
                       "params": c["params"]}
    # keep the combo search small: the best 8 members by their own single-strategy edge
    singles = _combos(members, info, 1, 120)
    top = {f"{m['strategy']}|{m['symbol']}|{params_hash(m['params'])}" for s in singles[:8] for m in s["members"]}
    members = {k: v for k, v in members.items() if k in top}
    rows = _combos(members, info, max_members, 120)
    data = {"combos": rows[:40], "best_single": next((x for x in rows if x["size"] == 1), None), "n_candidates": len(members),
            "tested": len(cands), "eval_days": EVAL_DAYS, "paths": PATHS,
            "long_check": sorted(tested, key=lambda t: -t["net"]),
            "note": "Years of 5-minute history, risk-sized micro contracts with realistic costs, block bootstrap of daily P&L "
                    "through the Apex $50k rules. Parameters were chosen on part of this history, so treat the numbers as optimistic."}
    _cache[key] = (time.time(), data)
    return data


def analyse(max_members: int = 3, force: bool = False) -> dict[str, Any]:
    if not force and _cache["data"] and time.time() - _cache["ts"] < 1800:
        return _cache["data"]
    from app import db

    with db.get_conn() as conn:
        pairs = {(r["strategy"], r["symbol"]) for r in conn.execute("SELECT DISTINCT strategy, symbol FROM account_trades WHERE strategy IS NOT NULL")}
        try:
            pairs |= {(r["strategy"], r["symbol"]) for r in conn.execute("SELECT DISTINCT strategy, symbol FROM account_trades_archive WHERE strategy IS NOT NULL")}
        except Exception:  # noqa: BLE001
            pass
    members: dict[str, dict[str, float]] = {}
    info: dict[str, dict[str, Any]] = {}
    for strat, sym in sorted(pairs):
        tr = _pooled_trades(strat, sym)
        if len(tr) < MIN_TRADES or sum(t["pnl"] for t in tr) <= 0:
            continue
        k = f"{strat}|{sym}"
        members[k] = _daily(tr)
        info[k] = {"strategy": strat, "symbol": sym, "n": len(tr), "net": round(sum(t["pnl"] for t in tr), 2)}
    all_days = sorted({d for m in members.values() for d in m})
    rng = np.random.default_rng(11)
    rows = []
    keys = sorted(members)
    for r in range(1, max_members + 1):
        for combo in itertools.combinations(keys, r):
            # only the days on which ALL members were being recorded count (otherwise a member's absence looks like a flat day)
            spans = [(min(members[k]), max(members[k])) for k in combo]
            lo, hi = max(s[0] for s in spans), min(s[1] for s in spans)
            days = [d for d in all_days if lo <= d <= hi]
            if len(days) < 15:
                continue
            arr = np.array([sum(members[k].get(d, 0.0) for k in combo) for d in days])
            res = _sim(arr, rng)
            best_s = max(res, key=lambda s: (res[s][0] - res[s][1], -abs(s - 1)))
            p, b, med = res[best_s]
            corr = None
            if r == 2:
                a1 = np.array([members[combo[0]].get(d, 0.0) for d in days]); a2 = np.array([members[combo[1]].get(d, 0.0) for d in days])
                if a1.std() > 0 and a2.std() > 0:
                    corr = round(float(np.corrcoef(a1, a2)[0, 1]), 2)
            rows.append({"members": [info[k] for k in combo], "size": r, "days": len(days), "best_scale": best_s,
                         "pass": round(100 * p, 1), "breach": round(100 * b, 1), "edge": round(100 * (p - b), 1),
                         "median_days_to_pass": med, "daily_corr": corr,
                         "by_scale": [{"scale": s, "pass": round(100 * v[0], 1), "breach": round(100 * v[1], 1)} for s, v in res.items()]})
    rows.sort(key=lambda x: -x["edge"])
    best_single = next((x for x in rows if x["size"] == 1), None)
    data = {"combos": rows[:40], "best_single": best_single, "n_candidates": len(keys), "eval_days": EVAL_DAYS, "paths": PATHS,
            "note": "Block bootstrap of daily P&L through the Apex $50k rules; members traded at a common risk scale. "
                    "Research only - a combo shown here is not traded until an account is set up for it."}
    _cache.update(ts=time.time(), data=data)
    return data
