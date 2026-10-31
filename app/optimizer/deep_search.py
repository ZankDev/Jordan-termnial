"""Deep search: the MAIN parameter search, run on the long history (up to 8 years of 5-minute bars), not the 60-day screen.

Why: the fast screen finds candidates on ~60 days and the confirmed tier tests them on 12-96 months afterwards. Nearly
everything that wins a 60-day contest is a regime fluke (measured: vwap_reversion -1.76, macd_cross -0.49 over 24
months), and after ~70,000 tries the 60-day winners' t-stats sit well under the luck bar. Searching the long history
directly puts the parameter search where the evidence is: 100+ trades per year, every year its own test.

2026-09-28 upgrade (was a 14-iteration side search every 20 minutes):
  * runs EVERY cycle with a real budget (ITERATIONS_PER_STRATEGY, TIME_BUDGET_SECONDS), rotating through the strategies;
  * remembers each strategy's best long-history parameter sets between cycles (meta 'deep_elite_<SYM>') and seeds the
    next search from them, so the search accumulates instead of restarting from random every time;
  * a survivor must also not be LOSING on the recent 60 days (recency check on the fast folds; too few recent trades =
    not judged) — the 60-day window is now a "still works lately" check, not the place parameters are chosen.

Overfitting guard (unchanged): the newest HOLDOUT_MONTHS are cut off (promotion.deep_folds_split) - this search never
sees them. A survivor must be positive and Apex-safe on that sealed window (promotion.holdout_check) before it reaches the
confirmed pipeline, which applies the full deep, Apex, stress-battery (incl. ±20% nudge, by-year, sample size) and
neighbour checks on top.
"""
from __future__ import annotations

import json
import time
from typing import Any

from app import db
from app.optimizer import field_guide, long_gate, promotion
from app.optimizer.search import _search_given_folds, build_folds, deterministic_neighbors, evaluate_candidate, is_eligible, params_hash
from app.strategies import registry

MIN_INTERVAL_SECONDS = 0          # every cycle
TIME_BUDGET_SECONDS = 900         # wall-clock cap per cycle (the cycle alternates NQ / ES)
ITERATIONS_PER_STRATEGY = 60
MIN_TEST_TRADES = 30              # per test fold; a multi-month fold with fewer trades says little
MAX_SURVIVORS = 3
ELITE_KEEP = 5                    # best long-history parameter sets remembered per strategy
RECENT_MIN_TEST_TRADES = 5
APEX_RANK_POOL = 6                # candidates that clear holdout + recency get an Apex replay before the top 3 are chosen
APEX_EXTRA_SECONDS = 180          # extra wall-clock allowed for those replays
DEAD_AFTER_ZERO_CYCLES = 6        # a strategy that found NOTHING positive this many searches in a row is parked...
DEAD_REVISIT_DAYS = 7             # ...and searched again once a week (the market changes)
FIELD_SEEDS = 4                   # survivor parameter sets from the FIELD added as seeds per strategy


def _zero_state(symbol: str, name: str) -> dict[str, Any]:
    try:
        return json.loads(db.meta_get(f"deep_zero:{symbol}:{name}") or "{}")
    except ValueError:
        return {}


def _parked(symbol: str, name: str) -> bool:
    """True while a strategy is parked: no positive long-history result for DEAD_AFTER_ZERO_CYCLES searches in a row, or every
    long record seen for it loses (long_gate.family_dead). Parked families are not searched, so their iterations go elsewhere."""
    if not long_gate.enabled():
        return False
    z = _zero_state(symbol, name)
    recent = (time.time() - z.get("ts", 0)) < DEAD_REVISIT_DAYS * 86400
    return recent and (z.get("z", 0) >= DEAD_AFTER_ZERO_CYCLES or long_gate.family_dead(symbol, name))


def _note_search(symbol: str, name: str, n_positive: int) -> None:
    z = _zero_state(symbol, name)
    z = {"z": 0 if n_positive else z.get("z", 0) + 1, "ts": time.time()}
    db.meta_set(f"deep_zero:{symbol}:{name}", json.dumps(z))


def _due(symbol: str) -> bool:
    last = float(db.meta_get(f"deep_search_at_{symbol}", "0") or 0)
    return time.time() - last >= MIN_INTERVAL_SECONDS


def _deep_ok(r: dict[str, Any]) -> bool:
    return (r["score"] > 0 and not r.get("diverged") and not r.get("too_smooth") and not r.get("regime_inconsistent")
            and not r.get("clustering_inflated"))


def _elites(symbol: str) -> dict[str, list[dict[str, Any]]]:
    try:
        return json.loads(db.meta_get(f"deep_elite_{symbol}") or "{}")
    except ValueError:
        return {}


def _remember(symbol: str, name: str, results: list[dict[str, Any]], months: int) -> None:
    data = _elites(symbol)
    pool = [e for e in data.get(name, []) if e.get("months") == months]  # scores on a different look-back are not comparable
    for r in results:
        if _deep_ok(r):
            pool.append({"params": r["params"], "score": round(r["score"], 4), "months": months, "ts": time.time()})
    best: dict[str, dict[str, Any]] = {}
    for e in pool:
        h = params_hash(e["params"])
        if h not in best or e["score"] > best[h]["score"]:
            best[h] = e
    data[name] = sorted(best.values(), key=lambda e: -e["score"])[:ELITE_KEEP]
    db.meta_set(f"deep_elite_{symbol}", json.dumps(data))


def _recent_ok(symbol: str, name: str, params: dict[str, Any], fast_folds: list | None) -> tuple[bool, str]:
    if not fast_folds:
        return True, "no recent data"
    res = evaluate_candidate(params, fast_folds, registry.get(name), symbol, 50_000.0, RECENT_MIN_TEST_TRADES, burn_gate=False)
    if res is None:
        return True, "too few recent trades - not judged"
    return res["score"] > 0, f"recent 60-day score {res['score']:.2f}"


def _apex_edge(symbol: str, cand: dict[str, Any], folds: list) -> float | None:
    """Apex replay of the candidate's long-history out-of-sample trades at the best risk size: pass rate minus breach rate."""
    from app.optimizer import apex_gate

    try:
        res = evaluate_candidate(cand["params"], folds, registry.get(cand["strategy"]), symbol, 50_000.0, MIN_TEST_TRADES,
                                 burn_gate=False, collect_trades=True)
        if res is None:
            return None
        info = apex_gate.analyse(res.get("test_trades", []), min_days_after_start=apex_gate.CONFIRM_MIN_DAYS_AFTER_START)
        if not info.get("n_starts"):
            return None
        info["judged"] = True
        cand["apex"] = {k: info.get(k) for k in ("pass_rate", "breach_rate", "unresolved_rate", "n_starts", "recommended_risk_dollars", "median_days_to_pass")}
        try:
            from app.accounts import risk_sync

            risk_sync.remember(symbol, cand["strategy"], info, source="deep")
        except Exception:  # noqa: BLE001
            pass
        return round(info["pass_rate"] - info["breach_rate"], 3)
    except Exception:  # noqa: BLE001 - ranking aid only
        return None


def run(symbol: str, run_id: int | None, fast_results: list[dict[str, Any]], df=None) -> list[dict[str, Any]]:
    """Returns survivors as [{"strategy", "params", "score"}] for promotion's confirmed step
    (empty when not due, no verified history yet, or nothing survives)."""
    if not _due(symbol):
        return []
    months, _cov, _rungs = promotion.deep_plan(symbol)
    if not months:
        return []
    search_folds, holdout = promotion.deep_folds_split(symbol, months)
    if not search_folds:
        return []
    db.meta_set(f"deep_search_at_{symbol}", str(time.time()))
    fast_folds = None
    if df is not None and not df.empty:
        try:
            fast_folds = build_folds(df)
        except Exception:  # noqa: BLE001
            fast_folds = None

    names = [s.name for s in registry.list_strategies()]
    start = int(db.meta_get(f"deep_search_rot_{symbol}", "0") or 0) % len(names)
    order = names[start:] + names[:start]
    elites = _elites(symbol)

    leader = db.get_slot(symbol, "leader")
    started = time.time()
    searched: list[str] = []
    parked: list[str] = []
    processed = 0
    found: list[dict[str, Any]] = []
    for name in order:
        if time.time() - started > TIME_BUDGET_SECONDS:
            break
        processed += 1
        if _parked(symbol, name):
            parked.append(name)
            continue
        strat = registry.get(name)
        seeds: list[dict[str, Any]] = [e["params"] for e in elites.get(name, []) if e.get("months") == months]
        for e in list(seeds[:2]):  # walk out from the best remembered sets
            seeds += deterministic_neighbors(e, strat.params)[:4]
        if leader and leader["strategy"] == name:
            seeds += deterministic_neighbors(leader["params"], strat.params)[:6] + [leader["params"]]
        seeds += [r["params"] for r in fast_results if r["strategy"] == name and is_eligible(r)][:3]
        seeds += field_guide.field_seeds(symbol, name, FIELD_SEEDS)  # regions the FIELD already saw survive
        results = _search_given_folds(
            search_folds, strat, symbol, ITERATIONS_PER_STRATEGY, 50_000.0, MIN_TEST_TRADES, top_n=5,
            seed=int(time.time()) % 100_000, seed_params=seeds, burn_gate=False,
        )
        searched.append(name)
        _remember(symbol, name, results, months)
        n_pos = 0
        for r in results:
            if _deep_ok(r):
                n_pos += 1
                found.append({"strategy": name, "params": r["params"], "score": r["score"]})
        _note_search(symbol, name, n_pos)
    # advance past everything handled this cycle, parked strategies included (they used to stall the rotation pointer)
    db.meta_set(f"deep_search_rot_{symbol}", str(start + max(1, processed)))

    # the remembered elites of strategies not searched this cycle stay in the running too
    seen = {params_hash(c["params"]) for c in found}
    for name, lst in _elites(symbol).items():
        for e in lst[:2]:
            if e.get("months") == months and params_hash(e["params"]) not in seen:
                found.append({"strategy": name, "params": e["params"], "score": e["score"]})
                seen.add(params_hash(e["params"]))

    found.sort(key=lambda c: c["score"], reverse=True)
    passed: list[dict[str, Any]] = []
    held_out = stale = 0
    for cand in found:
        if len(passed) >= APEX_RANK_POOL:
            break
        ok, _why, _info = promotion.holdout_check(symbol, cand["strategy"], cand["params"], holdout)
        if not ok:
            held_out += 1
            continue
        rec_ok, _rwhy = _recent_ok(symbol, cand["strategy"], cand["params"], fast_folds)
        if not rec_ok:
            stale += 1
            continue
        passed.append(cand)
    # Rank what survived by what the accounts need: the odds of PASSING the Apex $50k evaluation over the whole long
    # history (pass minus breach at the best risk size), the edge score only breaking ties. Also teaches risk_sync the
    # long-history recommended risk per strategy, which outranks the 60-day proxy.
    for cand in passed:
        cand["apex_edge"] = _apex_edge(symbol, cand, search_folds)
        if time.time() - started > TIME_BUDGET_SECONDS + APEX_EXTRA_SECONDS:
            break
    passed.sort(key=lambda c: (c.get("apex_edge") if c.get("apex_edge") is not None else -9, c["score"]), reverse=True)
    survivors = passed[:MAX_SURVIVORS]

    best = found[0] if found else None
    db.log_attempt(
        symbol, run_id, "confirmed", "deep_search", "found" if survivors else "skipped",
        (f"searched {len(searched)} strategies on {months} months ({len(parked)} parked as never-working; newest {promotion.HOLDOUT_MONTHS} sealed): "
         f"{len(found)} positive, {held_out} failed the sealed holdout, {stale} losing on the recent 60 days, "
         f"{len(survivors)} sent to confirmation")
        + (f"; best {best['strategy']} {best['score']:.2f}" if best else "")
        + (f"; top by Apex odds: {survivors[0]['strategy']} pass-breach {survivors[0]['apex_edge']:+.2f}" if survivors and survivors[0].get("apex_edge") is not None else ""),
        strategy=survivors[0]["strategy"] if survivors else None,
        params_hash=params_hash(survivors[0]["params"]) if survivors else None,
        cand_score=survivors[0]["score"] if survivors else None,
        detail={"months": months, "strategies": searched, "parked": parked, "positive": len(found), "holdout_failed": held_out,
                "recent_failed": stale, "seconds": round(time.time() - started)},
    )
    return survivors
