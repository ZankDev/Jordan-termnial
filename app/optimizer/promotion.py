"""Champion promotion: how the trainer decides what the "best strategy" is, and keeps
that answer fresh.

Why this exists: the old logic compared each cycle's fast-screen score against the
score stored when the champion was crowned. Those scores came from a different scoring
era (a much larger scale), nothing ever re-scored the champion, only the first eligible
candidate per cycle was ever tried, and every rejection vanished silently - so the
champion sat unchanged for 113+ hours while ~1,100 cycles ran.

Two tiers, both stored in champion_slots:
  LEADER     the current best gate-passing strategy. Moves whenever a genuinely better one
             appears. The incumbent is re-scored EVERY cycle on the same fresh folds its
             challengers were scored on (same window, same scale), so it is compared like
             for like, and it is dropped if it stops passing.
  CONFIRMED  the strict tier: multi-year deep validation, Apex pass-rate and parameter
             robustness. Only attempted when the Dukascopy history is actually complete
             (coverage gate) - otherwise the system runs leader-only, visibly.

Every decision (including "nothing eligible this cycle") is written to
promotion_attempts with its reason, so "why hasn't it upgraded?" is answerable.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any

from app import db
from app.data import dukascopy_source
from app.optimizer import apex_gate, long_gate, stress_battery
from app.optimizer.epoch import SCORING_EPOCH
from app.optimizer.search import build_folds, deterministic_neighbors, evaluate_candidate, is_eligible, params_hash
from app.strategies import registry

# --- leader ---------------------------------------------------------------
LEADER_MARGIN_ABS = 0.25  # challenger must beat the re-scored incumbent by max(ABS, REL*incumbent)
LEADER_MARGIN_REL = 0.15
LEADER_MIN_DWELL_SECONDS = 1800  # anti-churn: a fresh leader is safe from a marginal challenger this long
LEADER_FAIL_GRACE = 3  # consecutive failed re-validations (with no replacement) before the slot is vacated
LEADER_TOP_K = 5
FAST_MIN_TEST_TRADES = 15  # must match random_search's default so re-scores use the same trade-count bar

# --- confirmed ------------------------------------------------------------
DEEP_YEARS = 8
DEEP_INTERVAL = "5m"
DEEP_MIN_TEST_TRADES = 50
DEEP_PER_CYCLE = 2  # deep-validation attempts per cycle
CONFIRM_RETRY_HOURS = 12  # don't re-run an expensive validation on the same params sooner than this
CONFIRM_MARGIN = 0.15
CONFIRMED_RESCORE_INTERVAL_HOURS = 6
DEMOTE_AFTER_FAILS = 2
NEIGHBOR_PASS_FRACTION = 0.75  # this fraction of neighbours must hold up...
NEIGHBOR_MIN_FRACTION = 0.5  # ...at this fraction of the candidate's deep score
DATA_MIN_COVERAGE = 0.85
DEEP_LADDER_MONTHS = (96, 60, 36, 24, 12)  # try the longest look-back that is actually complete
CERT_CACHE_SECONDS = 3600  # a candidate's battery result is reused for this long
CERT_RANK = {"certified": 2, "watch": 1, "failed": 0}
REFINE_MAX_NEIGHBORS = 24
DEEP_TIME_BUDGET_SECONDS = 300
DEEP_FOLDS_CACHE_SECONDS = 6 * 3600

_deep_cache: dict[str, tuple[float, list, int]] = {}
_deep_df_cache: dict[str, tuple[float, int, Any]] = {}
_split_cache: dict[str, tuple[float, int, list, list]] = {}
_cert_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_hist_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
HISTORY_CACHE_SECONDS = 6 * 3600
HOLDOUT_MONTHS = 6  # newest months sealed away from the deep SEARCH; used only to confirm (was 3: too short to mean much)
HOLDOUT_MIN_TRADES = 10
_repair_thread: threading.Thread | None = None


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _seconds_since(sql_ts: str | None) -> float:
    if not sql_ts:
        return float("inf")
    try:
        t = dt.datetime.strptime(sql_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return float("inf")
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds()


def _flag_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("diverged", "too_smooth", "exceeds_burn_limit", "regime_inconsistent", "clustering_inflated")
    out = {"n_results": len(results), "n_eligible": sum(1 for r in results if is_eligible(r))}
    out.update({k: sum(1 for r in results if r.get(k)) for k in keys})
    return out


def _slot_from_result(r: dict[str, Any], run_id: int | None, version: int) -> dict[str, Any]:
    return {
        "strategy": r["strategy"],
        "params": r["params"],
        "params_hash": params_hash(r["params"]),
        "score": r["score"],
        "score_epoch": SCORING_EPOCH,
        "window": r.get("window", "60d"),
        "train_metrics": r.get("train_metrics"),
        "test_metrics": r.get("test_metrics"),
        "fold_scores": r.get("fold_scores"),
        "apex": r.get("apex_fast"),
        "run_id": run_id,
        "version": version,
        "last_rescored_at": _utcnow(),
        "rescore_history": [{"ts": _utcnow(), "score": r["score"], "ok": True}],
        "consecutive_fails": 0,
    }


def _certify(symbol: str, strategy: str, params: dict[str, Any], folds: list, score: float,
             recommended_risk: float | None, force: bool = False, tag: str = "fast") -> dict[str, Any]:
    """Stress battery on the fast folds, cached so an unchanged candidate is not re-attacked every cycle."""
    key = (symbol, f"{tag}:{params_hash(params)}")
    hit = _cert_cache.get(key)
    if hit and not force and time.time() - hit[0] < CERT_CACHE_SECONDS:
        return hit[1]
    try:
        cert = stress_battery.run_battery(strategy, params, symbol, folds, score, recommended_risk, deep=(tag == "deep"))
    except Exception as e:  # noqa: BLE001 - a battery bug must never stop promotion; fall back to unjudged
        cert = {"status": "watch", "passed": 0, "of": 0, "hard_failed": [], "checks": [], "error": f"{type(e).__name__}: {e}"}
    _cert_cache[key] = (time.time(), cert)
    if len(_cert_cache) > 200:
        for k in sorted(_cert_cache, key=lambda k: _cert_cache[k][0])[:100]:
            _cert_cache.pop(k, None)
    return cert


def _history_check(symbol: str, strategy: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Does the candidate also make money over the longest complete Dukascopy look-back (12-96
    months, whatever is verified so far)? The fast screen sees ~60 days; a strategy that loses
    over two years is a regime fluke however good its 60 days look. None = no verified history
    yet. Judged with the same deep rules as the confirmed tier (score > 0, no divergence,
    not too smooth, regime-consistent), so 'history ok' and 'deep validation passes' agree."""
    key = (symbol, params_hash(params))
    hit = _hist_cache.get(key)
    if hit and time.time() - hit[0] < HISTORY_CACHE_SECONDS:
        return hit[1]
    months, _cov, _rungs = deep_plan(symbol)
    if not months:
        return None
    folds = deep_folds(symbol, months)
    if not folds:
        return None
    res = evaluate_candidate(params, folds, registry.get(strategy), symbol, 50_000.0, DEEP_MIN_TEST_TRADES, burn_gate=False)
    if res is None:
        check = stress_battery._check("history", True, True, f"too few trades over {months} months - not judged",
                                      {"judged": False, "months": months})
    else:
        ok = _deep_ok(res)
        check = stress_battery._check("history", ok, True, f"{months}-month walk-forward score {res['score']:.2f}"
                                      + ("" if ok else " (needs > 0 with clean gates)"),
                                      {"judged": True, "months": months, "score": round(res["score"], 3)})
    _hist_cache[key] = (time.time(), check)
    return check


def _with_history(cert: dict[str, Any], check: dict[str, Any] | None) -> dict[str, Any]:
    """Cert plus the history check, status recomputed (history is a hard check)."""
    if check is None or not cert.get("checks"):
        return cert
    checks = [c for c in cert["checks"] if c["name"] != "history"] + [check]
    hard_failed = [c["name"] for c in checks if c["hard"] and not c["ok"]]
    return {**cert, "checks": checks, "hard_failed": hard_failed, "passed": sum(1 for c in checks if c["ok"]),
            "of": len(checks), "status": "certified" if not hard_failed else ("watch" if len(hard_failed) == 1 else "failed"),
            "history_ok": check["ok"], "history_months": check["detail"].get("months")}


def _refine_incumbent(symbol: str, inc: dict[str, Any] | None, fast_folds: list) -> list[dict[str, Any]]:
    """Local search around the current leader: every one-step parameter neighbour scored on the
    same fresh folds. Random search rarely lands exactly on the crest of a good region; this
    walks the leader up it. Results join the normal candidate pool, so they need the same
    margin, Apex proxy and certification as anything else to take the crown."""
    if not inc:
        return []
    strat = registry.get(inc["strategy"])
    out: list[dict[str, Any]] = []
    for n in deterministic_neighbors(inc["params"], strat.params)[:REFINE_MAX_NEIGHBORS]:
        res = evaluate_candidate(n, fast_folds, strat, symbol, 50_000.0, FAST_MIN_TEST_TRADES)
        if res is not None and is_eligible(res) and res["score"] > 0:
            res["strategy"], res["window"], res["refined"] = inc["strategy"], "60d", True
            out.append(res)
    return out


# ------------------------------------------------------------------ leader tier
def _leader_step(symbol: str, run_id: int | None, results: list[dict[str, Any]], fast_folds: list) -> bool:
    """Returns True if the leader slot changed (promotion or vacated)."""
    counts = _flag_counts(results)
    inc = db.get_slot(symbol, "leader")
    refined = _refine_incumbent(symbol, inc, fast_folds)
    counts["n_refined"] = len(refined)
    pooled: dict[str, dict[str, Any]] = {}
    for r in [*(x for x in results if is_eligible(x) and x["score"] > 0 and x.get("window", "60d") == "60d"), *refined]:
        h = params_hash(r["params"])
        if h not in pooled or r["score"] > pooled[h]["score"]:
            pooled[h] = r
    ranked = sorted(pooled.values(), key=lambda r: r["score"], reverse=True)
    candidates = ranked[:LEADER_TOP_K]
    # the best candidate of every other strategy is Apex-replayed too, only to learn its recommended risk (risk_sync) —
    # it does not enter the promotion pool
    top_ids = {id(r) for r in candidates}
    extra: dict[str, dict[str, Any]] = {}
    for r in ranked:
        if id(r) not in top_ids and r["strategy"] not in extra and all(c["strategy"] != r["strategy"] for c in candidates):
            extra[r["strategy"]] = r
    # Apex fast proxy: a candidate that keeps breaching the trailing drawdown even at its best
    # risk level is not a leader, however good its edge score looks.
    pool: list[dict[str, Any]] = []
    apex_rejected: list[dict[str, Any]] = []
    apex_seen: set[str] = set()
    for r in [*candidates, *extra.values()]:
        in_pool = id(r) in top_ids
        res = evaluate_candidate(r["params"], fast_folds, registry.get(r["strategy"]), symbol, 50_000.0,
                                 FAST_MIN_TEST_TRADES, collect_trades=True)
        if res is None:
            continue
        ok, info = apex_gate.fast_proxy(res.get("test_trades", []))
        db.update_strategy_apex(symbol, r["strategy"], info.get("pass_rate"))
        r["apex_fast"] = info
        if r["strategy"] not in apex_seen:  # candidates are score-sorted: first per strategy = its best
            apex_seen.add(r["strategy"])
            try:
                from app.accounts import risk_sync

                risk_sync.remember(symbol, r["strategy"], info)
            except Exception:  # noqa: BLE001 - bookkeeping must never break promotion
                pass
        if in_pool:
            (pool if ok else apex_rejected).append(r)
    if apex_rejected and not pool:
        top = apex_rejected[0]
        db.log_attempt(symbol, run_id, "leader", "apex", "rejected", top["apex_fast"]["reason"],
                       strategy=top["strategy"], params_hash=params_hash(top["params"]), cand_score=top["score"],
                       detail={"n_apex_rejected": len(apex_rejected), **counts})
    counts["n_apex_rejected"] = len(apex_rejected)

    # Long-history gate: a candidate that does not make money over YEARS is a 60-day fluke, however good it looks now.
    # (history not verified yet = not judged = allowed through, exactly as before)
    if long_gate.enabled():
        kept_pool = []
        for r in pool:
            lg = long_gate.check(symbol, r["strategy"], r["params"])
            r["long"] = lg
            if not lg["judged"] or lg["passes"]:
                kept_pool.append(r)
        counts["n_long_failed"] = len(pool) - len(kept_pool)
        if pool and not kept_pool:
            worst = pool[0]
            db.log_attempt(symbol, run_id, "leader", "long_history", "rejected", worst["long"]["why"], strategy=worst["strategy"],
                           params_hash=params_hash(worst["params"]), cand_score=worst["score"], detail=counts)
        pool = kept_pool

    # Deeper testing: the stress battery. Robust beats lucky - candidates are ranked by
    # certification first and score second.
    for r in pool:
        ap = r.get("apex_fast") or {}
        r["cert"] = _with_history(
            _certify(symbol, r["strategy"], r["params"], fast_folds, r["score"], ap.get("recommended_risk_dollars")),
            _history_check(symbol, r["strategy"], r["params"]),
        )
    # within a certification level, prefer the candidate with the better Apex odds (pass minus breach at its best risk),
    # then the edge score: the goal is accounts that PASS the evaluation, not the prettiest t-stat
    def _apex_edge(r: dict[str, Any]) -> float:
        ap = r.get("apex_fast") or {}
        return round((ap.get("pass_rate") or 0) - (ap.get("breach_rate") or 0), 1) if ap.get("judged") else 0.0

    pool.sort(key=lambda r: (CERT_RANK.get(r["cert"]["status"], 1), _apex_edge(r), r["score"]), reverse=True)
    counts["n_certified"] = sum(1 for r in pool if r["cert"]["status"] == "certified")
    counts["n_cert_failed"] = sum(1 for r in pool if r["cert"]["status"] == "failed")
    best = pool[0] if pool else None

    inc_now: float | None = None
    inc_ok = False
    inc_cert: dict[str, Any] | None = None
    vacated = False  # the incumbent was retired by the long-history gate this cycle
    if inc:
        strat = registry.get(inc["strategy"])
        res = evaluate_candidate(inc["params"], fast_folds, strat, symbol, 50_000.0, FAST_MIN_TEST_TRADES,
                                 collect_trades=True)
        inc_apex_ok, inc_apex = (True, None)
        if res is not None:
            inc_apex_ok, inc_apex = apex_gate.fast_proxy(res.get("test_trades", []))
            db.update_strategy_apex(symbol, inc["strategy"], inc_apex.get("pass_rate"))
        inc_ok = res is not None and is_eligible(res) and res["score"] > 0 and inc_apex_ok
        inc_now = res["score"] if res is not None else None
        if inc_ok:
            inc_cert = _with_history(
                _certify(symbol, inc["strategy"], inc["params"], fast_folds, inc_now,
                         (inc_apex or {}).get("recommended_risk_dollars")),
                _history_check(symbol, inc["strategy"], inc["params"]),
            )
        history = (inc.get("rescore_history") or [])[-47:] + [{"ts": _utcnow(), "score": inc_now, "ok": inc_ok}]
        fails = 0 if inc_ok else (inc.get("consecutive_fails") or 0) + 1
        patch: dict[str, Any] = {
            "last_rescored_at": _utcnow(), "rescore_history": history, "consecutive_fails": fails,
        }
        if res is not None:
            # keep the stored numbers (and therefore the Trainer hero) on the freshest scale
            patch.update(score=res["score"], train_metrics=res["train_metrics"],
                         test_metrics=res["test_metrics"], fold_scores=res["fold_scores"], apex=inc_apex)
        if inc_cert is not None:
            patch["certification"] = inc_cert
        db.patch_slot(symbol, "leader", **patch)

        # an incumbent that loses over the long history is retired at once (it used to stay on as "watch")
        if long_gate.enabled():
            lg = long_gate.check(symbol, inc["strategy"], inc["params"])
            if lg["judged"] and not lg["passes"]:
                db.retire_slot(symbol, "leader", "long_history")
                db.bump_champion_version()
                db.log_attempt(symbol, run_id, "leader", "long_history", "demoted", lg["why"], strategy=inc["strategy"],
                               params_hash=inc["params_hash"], inc_score=inc_now, detail=counts)
                inc, inc_ok, inc_now, inc_cert, vacated = None, False, None, None, True

    def promote(reason: str) -> bool:
        version = db.bump_champion_version()
        if inc:
            db.retire_slot(symbol, "leader", f"superseded:{reason}")
        new_slot = _slot_from_result(best, run_id, version)
        new_slot["certification"] = best.get("cert")
        db.put_slot(symbol, "leader", new_slot)
        db.log_attempt(
            symbol, run_id, "leader", "pool", "promoted", reason, strategy=best["strategy"],
            params_hash=params_hash(best["params"]), cand_score=best["score"], inc_score=inc_now, detail=counts,
        )
        return True

    if not inc:
        if best:
            return promote("no_incumbent")
        db.log_attempt(symbol, run_id, "leader", "pool", "skipped",
                       f"no eligible candidate this cycle ({counts['n_eligible']} of {counts['n_results']} passed the gates)",
                       detail=counts)
        return vacated

    if not inc_ok:
        if best and params_hash(best["params"]) != inc["params_hash"]:
            return promote("incumbent_failed_revalidation")
        fails = (inc.get("consecutive_fails") or 0) + 1
        if fails >= LEADER_FAIL_GRACE:
            db.retire_slot(symbol, "leader", "decayed")
            db.bump_champion_version()
            db.log_attempt(symbol, run_id, "leader", "rescore", "demoted",
                           f"failed re-validation {fails} cycles in a row with no replacement",
                           strategy=inc["strategy"], params_hash=inc["params_hash"], inc_score=inc_now, detail=counts)
            return True
        db.log_attempt(symbol, run_id, "leader", "rescore", "held",
                       f"incumbent failed re-validation ({fails}/{LEADER_FAIL_GRACE}), no replacement yet",
                       strategy=inc["strategy"], params_hash=inc["params_hash"], inc_score=inc_now, detail=counts)
        return False

    # incumbent still passes. A more robust challenger (better certification) takes over at once -
    # robustness is the whole point of the battery; otherwise only a clearly better score does.
    inc_rank = CERT_RANK.get((inc_cert or {}).get("status"), 1)
    if best and params_hash(best["params"]) != inc["params_hash"]:
        if CERT_RANK.get(best["cert"]["status"], 1) > inc_rank:
            return promote(f"more_robust:{best['cert']['status']}_over_{(inc_cert or {}).get('status', 'unknown')}")
        if CERT_RANK.get(best["cert"]["status"], 1) < inc_rank:
            db.log_attempt(symbol, run_id, "leader", "certify", "kept",
                           f"best challenger is less robust ({best['cert']['status']}) than the incumbent",
                           strategy=best["strategy"], params_hash=params_hash(best["params"]),
                           cand_score=best["score"], inc_score=inc_now, detail=counts)
            return False
        margin = max(LEADER_MARGIN_ABS, LEADER_MARGIN_REL * inc_now)
        if best["score"] >= inc_now + margin:
            if _seconds_since(inc.get("promoted_at")) >= LEADER_MIN_DWELL_SECONDS:
                return promote("outscored")
            db.log_attempt(symbol, run_id, "leader", "dwell", "held",
                           "challenger clears the margin but the incumbent is inside its dwell window",
                           strategy=best["strategy"], params_hash=params_hash(best["params"]),
                           cand_score=best["score"], inc_score=inc_now, margin=margin, detail=counts)
            return False
        db.log_attempt(symbol, run_id, "leader", "margin", "kept",
                       f"best challenger {best['score']:.2f} is under incumbent {inc_now:.2f} + margin {margin:.2f}",
                       strategy=best["strategy"], params_hash=params_hash(best["params"]),
                       cand_score=best["score"], inc_score=inc_now, margin=margin, detail=counts)
        return False
    db.log_attempt(symbol, run_id, "leader", "rescore", "kept", "incumbent re-verified and still the best",
                   strategy=inc["strategy"], params_hash=inc["params_hash"], inc_score=inc_now, detail=counts)
    return False


# --------------------------------------------------------------- confirmed tier
def _kick_repair(symbol: str) -> None:
    """Heal the Dukascopy cache a little each cycle in a background thread, so a slow or
    failing download can never block a training cycle. One thread serves both symbols, so it
    goes to whichever has NO usable deep window yet (repairing only its newest 12 months first)
    before extending anyone's history further back."""
    global _repair_thread
    if _repair_thread is not None and _repair_thread.is_alive():
        return
    target, years = symbol, DEEP_YEARS
    for other in ("NQ", "ES"):
        try:
            months, _cov, _rungs = deep_plan(other)
        except Exception:  # noqa: BLE001
            continue
        if not months:
            target, years = other, 1
            break

    def work() -> None:
        try:
            dukascopy_source.repair_step(target, years=years, max_months=3, min_gap_seconds=120)
        except Exception:  # noqa: BLE001 - background best-effort
            pass

    _repair_thread = threading.Thread(target=work, daemon=True, name="dukascopy-repair")
    _repair_thread.start()


def deep_plan(symbol: str) -> tuple[int, float, list[dict[str, Any]]]:
    """Choose the longest look-back (of DEEP_LADDER_MONTHS) whose Dukascopy history is
    >= DATA_MIN_COVERAGE complete. Returns (months or 0, coverage of the chosen/shortest rung,
    per-rung coverage) - so deep testing starts as soon as ONE clean year exists and gets deeper
    by itself as the background repair fills in older months."""
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * DEEP_YEARS + 5)
    per_month = dukascopy_source.coverage_by_month(symbol, start, end, DEEP_INTERVAL)  # newest first
    rungs: list[dict[str, Any]] = []
    chosen = 0
    for months in sorted(DEEP_LADDER_MONTHS):
        window = per_month[:months]
        total = sum(w[3] for w in window)
        cov = sum(w[2] * w[3] for w in window) / total if total else 0.0
        rungs.append({"months": months, "coverage": round(cov, 3)})
        if cov >= DATA_MIN_COVERAGE:
            chosen = months
    shortest = rungs[0]["coverage"] if rungs else 0.0
    return chosen, shortest, rungs


def _deep_df(symbol: str, months: int):
    hit = _deep_df_cache.get(symbol)
    if hit and hit[1] == months and time.time() - hit[0] < DEEP_FOLDS_CACHE_SECONDS:
        return hit[2]
    end = dt.date.today()
    start = end - dt.timedelta(days=int(months * 30.5) + 5)
    df = dukascopy_source.fetch_bars(symbol, start, end, DEEP_INTERVAL, max_repair_months=0)
    _deep_df_cache[symbol] = (time.time(), months, df)
    return df


def deep_folds_split(symbol: str, months: int, holdout_months: int = HOLDOUT_MONTHS) -> tuple[list, list]:
    """(search_folds, holdout_folds). The newest `holdout_months` are cut off: the deep SEARCH
    never sees them, so passing them afterwards is real out-of-sample evidence that the
    candidate still works NOW - the classic failure of a searched-on-history strategy."""
    hit = _split_cache.get(symbol)
    if hit and hit[1] == months and time.time() - hit[0] < DEEP_FOLDS_CACHE_SECONDS:
        return hit[2], hit[3]
    from app.optimizer.search import _split_walk_forward_year_folds, split_walk_forward_span_folds
    import pandas as pd

    df = _deep_df(symbol, months)
    if df.empty:
        return [], []
    cut = df.index.max() - pd.DateOffset(months=holdout_months)
    search_df, hold_df = df[df.index < cut], df[df.index >= cut]
    span = months - holdout_months
    search = (_split_walk_forward_year_folds(search_df, span // 12) if span >= 24
              else split_walk_forward_span_folds(search_df, 4))
    holdout = [(search_df, hold_df)] if not search_df.empty and not hold_df.empty else []
    if search:
        _split_cache[symbol] = (time.time(), months, search, holdout)
    return search, holdout


def holdout_check(symbol: str, strategy: str, params: dict[str, Any], holdout: list) -> tuple[bool, str, dict[str, Any]]:
    """Does the candidate still make money, without breaching Apex, in the sealed newest months?"""
    if not holdout:
        return True, "no holdout data - not judged", {"judged": False}
    res = evaluate_candidate(params, holdout, registry.get(strategy), symbol, 50_000.0, HOLDOUT_MIN_TRADES,
                             burn_gate=False, collect_trades=True)
    if res is None:
        return False, f"fewer than {HOLDOUT_MIN_TRADES} trades in the newest {HOLDOUT_MONTHS} months", {"judged": True}
    if res["score"] <= 0:
        return False, f"newest {HOLDOUT_MONTHS} months score {res['score']:.2f} <= 0", {"judged": True, "score": round(res["score"], 3)}
    info = apex_gate.analyse(res.get("test_trades", []), min_days_after_start=3, max_starts=60)
    if info["n_starts"] >= apex_gate.LEADER_MIN_STARTS and info["breach_rate"] > apex_gate.CONFIRM_MAX_BREACH_RATE:
        return False, (f"newest {HOLDOUT_MONTHS} months breach the Apex drawdown from {info['breach_rate']:.0%} of start dates "
                       f"(limit {apex_gate.CONFIRM_MAX_BREACH_RATE:.0%})"), {"judged": True, "score": round(res["score"], 3)}
    return True, f"newest {HOLDOUT_MONTHS} months score {res['score']:.2f}", {"judged": True, "score": round(res["score"], 3)}


def deep_folds(symbol: str, months: int) -> list | None:
    """Walk-forward folds over the chosen look-back from the local Dukascopy cache only (no
    network in the cycle - repair is a background job), memoised for a few hours so one
    cycle's deep attempts share a single parquet read."""
    hit = _deep_cache.get(symbol)
    if hit and hit[2] == months and time.time() - hit[0] < DEEP_FOLDS_CACHE_SECONDS:
        return hit[1]
    from app.optimizer.search import _split_walk_forward_year_folds, split_walk_forward_span_folds

    df = _deep_df(symbol, months)
    if df.empty:
        return None
    folds = (_split_walk_forward_year_folds(df, months // 12) if months >= 24 else split_walk_forward_span_folds(df, 4)) or None
    if folds:
        _deep_cache[symbol] = (time.time(), folds, months)
    return folds


def _deep_ok(res: dict[str, Any] | None) -> bool:
    return (
        res is not None and not res.get("diverged") and not res.get("too_smooth")
        and not res.get("regime_inconsistent") and not res.get("clustering_inflated") and res["score"] > 0
    )


def _robustness(candidate: dict[str, Any], symbol: str, folds: list, deep_score: float) -> dict[str, Any]:
    """Same-scale neighbourhood check: nudge each param one grid step and re-score on the SAME
    deep folds. (The first version compared deep-fold neighbours against the inflated
    fast-screen score, which no candidate could ever satisfy.)"""
    strat = registry.get(candidate["strategy"])
    neighbors = deterministic_neighbors(candidate["params"], strat.params)
    if not neighbors:
        return {"robust": True, "n_neighbors": 0, "pass_fraction": 1.0, "worst": deep_score}
    scores = []
    for n in neighbors:
        res = evaluate_candidate(n, folds, strat, symbol, 50_000.0, DEEP_MIN_TEST_TRADES, burn_gate=False)
        scores.append(res["score"] if res is not None else 0.0)
    holding = sum(1 for s in scores if s >= NEIGHBOR_MIN_FRACTION * deep_score)
    frac = holding / len(scores)
    return {"robust": frac >= NEIGHBOR_PASS_FRACTION, "n_neighbors": len(scores),
            "pass_fraction": round(frac, 3), "worst": min(scores)}


def _confirmed_step(symbol: str, run_id: int | None, results: list[dict[str, Any]],
                    extra: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    if db.meta_get("apex_gate_version") != str(apex_gate.GATE_VERSION):
        # the gate changed: earlier rejections were judged by different rules, so forget them as retry-cache hits
        db.meta_set("apex_gate_version", str(apex_gate.GATE_VERSION))
        db.meta_set("apex_gate_since", _utcnow())
    _kick_repair(symbol)
    months, shortest_cov, rungs = deep_plan(symbol)
    if not months:
        db.log_attempt(symbol, run_id, "confirmed", "data", "skipped",
                       f"Dukascopy history is {shortest_cov:.0%} complete over the newest {DEEP_LADDER_MONTHS[-1]} months "
                       f"(< {DATA_MIN_COVERAGE:.0%}); confirmed tier waits, leader keeps upgrading",
                       detail={"coverage": shortest_cov, "rungs": rungs})
        return None
    folds = deep_folds(symbol, months)
    if not folds:
        db.log_attempt(symbol, run_id, "confirmed", "data", "skipped", "no walk-forward folds available")
        return None

    started = time.time()
    conf = db.get_slot(symbol, "confirmed")

    # Keep the confirmed incumbent honest: re-score on the same deep folds now and then.
    conf_now: float | None = conf["deep_score"] if conf else None
    if conf and _seconds_since(conf.get("last_rescored_at")) >= CONFIRMED_RESCORE_INTERVAL_HOURS * 3600:
        strat = registry.get(conf["strategy"])
        res = evaluate_candidate(conf["params"], folds, strat, symbol, 50_000.0, DEEP_MIN_TEST_TRADES, burn_gate=False)
        ok = _deep_ok(res)
        fails = 0 if ok else (conf.get("consecutive_fails") or 0) + 1
        if ok:
            conf_now = res["score"]
            db.patch_slot(symbol, "confirmed", deep_score=conf_now, last_rescored_at=_utcnow(), consecutive_fails=0)
        else:
            db.patch_slot(symbol, "confirmed", last_rescored_at=_utcnow(), consecutive_fails=fails)
            if fails >= DEMOTE_AFTER_FAILS:
                db.retire_slot(symbol, "confirmed", "demoted")
                db.bump_champion_version()
                db.log_attempt(symbol, run_id, "confirmed", "rescore", "demoted",
                               f"failed multi-year re-validation {fails} times", strategy=conf["strategy"],
                               params_hash=conf["params_hash"])
                conf, conf_now = None, None

    if conf and long_gate.enabled():
        lg = long_gate.check(symbol, conf["strategy"], conf["params"])
        if lg["judged"] and not lg["passes"]:
            db.retire_slot(symbol, "confirmed", "long_history")
            db.bump_champion_version()
            db.log_attempt(symbol, run_id, "confirmed", "long_history", "demoted", lg["why"], strategy=conf["strategy"],
                           params_hash=conf["params_hash"])
            conf, conf_now = None, None

    leader = db.get_slot(symbol, "leader")
    pool = sorted((r for r in results if is_eligible(r) and r["score"] > 0 and r.get("window", "60d") == "60d"),
                  key=lambda r: r["score"], reverse=True)[:1]
    candidates: list[dict[str, Any]] = list(extra or [])  # deep-search survivors go first
    if leader:
        candidates.append({"strategy": leader["strategy"], "params": leader["params"], "score": leader["score"]})
    candidates += [{"strategy": r["strategy"], "params": r["params"], "score": r["score"]} for r in pool]

    seen: set[str] = set()
    summary: dict[str, Any] | None = None
    attempts = 0
    for cand in candidates:
        h = params_hash(cand["params"])
        if h in seen or (conf and h == conf["params_hash"]):
            continue
        seen.add(h)
        if attempts >= DEEP_PER_CYCLE or time.time() - started > DEEP_TIME_BUDGET_SECONDS:
            break
        if db.attempted_recently(symbol, "confirmed", h, CONFIRM_RETRY_HOURS):
            continue
        attempts += 1
        strat = registry.get(cand["strategy"])
        deep = evaluate_candidate(cand["params"], folds, strat, symbol, 50_000.0, DEEP_MIN_TEST_TRADES,
                                  burn_gate=False, collect_trades=True)
        if not _deep_ok(deep):
            failed = [k for k in ("diverged", "too_smooth", "regime_inconsistent") if deep and deep.get(k)]
            if deep is None:
                why = f"fewer than {DEEP_MIN_TEST_TRADES} trades on at least one year-fold"
            elif failed:
                why = "deep gates failed: " + ", ".join(failed)
            else:
                why = f"deep score {deep['score']:.2f} <= 0"
            db.log_attempt(symbol, run_id, "confirmed", "deep", "rejected", why, strategy=cand["strategy"],
                           params_hash=h, cand_score=cand["score"], inc_score=conf_now,
                           detail={"deep_score": deep["score"] if deep else None})
            summary = {"attempted": True, "passed": False, "score": deep["score"] if deep else None}
            continue

        _search_f, hold_folds = deep_folds_split(symbol, months)
        hold_ok, hold_why, hold_info = holdout_check(symbol, cand["strategy"], cand["params"], hold_folds)
        if not hold_ok:
            db.log_attempt(symbol, run_id, "confirmed", "holdout", "rejected", hold_why, strategy=cand["strategy"],
                           params_hash=h, cand_score=cand["score"], inc_score=conf_now,
                           detail={"deep_score": deep["score"], **hold_info})
            summary = {"attempted": True, "passed": True, "robust": False, "score": deep["score"]}
            continue

        if long_gate.enabled():
            lg = long_gate.check(symbol, cand["strategy"], cand["params"])
            if lg["judged"] and not lg["passes"]:
                db.log_attempt(symbol, run_id, "confirmed", "long_history", "rejected", lg["why"], strategy=cand["strategy"],
                               params_hash=h, cand_score=cand["score"], inc_score=conf_now, detail={"deep_score": deep["score"], **lg["record"]})
                summary = {"attempted": True, "passed": True, "robust": False, "score": deep["score"]}
                continue

        apex_ok, apex_info = apex_gate.confirmed_gate(deep.get("test_trades", []))
        db.update_strategy_apex(symbol, cand["strategy"], apex_info.get("pass_rate"))
        if not apex_ok:
            db.log_attempt(symbol, run_id, "confirmed", "apex", "rejected", apex_info["reason"], strategy=cand["strategy"],
                           params_hash=h, cand_score=cand["score"], inc_score=conf_now, detail=apex_info)
            summary = {"attempted": True, "passed": False, "score": deep["score"]}
            continue

        cert = _certify(symbol, cand["strategy"], cand["params"], folds, deep["score"],
                        apex_info.get("recommended_risk_dollars"), force=True, tag="deep")
        if cert["status"] != "certified":
            db.log_attempt(symbol, run_id, "confirmed", "certify", "rejected",
                           f"stress battery on {months} months: {cert['status']} ({', '.join(cert.get('hard_failed') or []) or 'error'})",
                           strategy=cand["strategy"], params_hash=h, cand_score=cand["score"], inc_score=conf_now,
                           detail={"months": months, "checks": [(c["name"], c["ok"], c["value"]) for c in cert.get("checks", [])]})
            summary = {"attempted": True, "passed": True, "robust": False, "score": deep["score"]}
            continue

        rob = _robustness(cand, symbol, folds, deep["score"])
        if not rob["robust"]:
            db.log_attempt(symbol, run_id, "confirmed", "robust", "rejected",
                           f"only {rob['pass_fraction']:.0%} of neighbours hold up (need {NEIGHBOR_PASS_FRACTION:.0%})",
                           strategy=cand["strategy"], params_hash=h, cand_score=cand["score"], inc_score=conf_now,
                           detail=rob)
            summary = {"attempted": True, "passed": True, "robust": False, "score": deep["score"]}
            continue

        if conf and conf_now is not None and deep["score"] < conf_now * (1 + CONFIRM_MARGIN):
            db.log_attempt(symbol, run_id, "confirmed", "margin", "kept",
                           f"deep score {deep['score']:.2f} does not beat confirmed {conf_now:.2f} by {CONFIRM_MARGIN:.0%}",
                           strategy=cand["strategy"], params_hash=h, cand_score=cand["score"], inc_score=conf_now)
            continue

        version = db.bump_champion_version()
        if conf:
            db.retire_slot(symbol, "confirmed", "superseded")
        slot = _slot_from_result(
            {"strategy": cand["strategy"], "params": cand["params"], "score": cand["score"],
             "window": "60d", "train_metrics": None, "test_metrics": None, "fold_scores": None},
            run_id, version,
        )
        slot.update(deep_score=deep["score"], deep_fold_scores=deep["fold_scores"], neighborhood=rob,
                    apex=apex_info, certification={**cert, "months": months})
        db.put_slot(symbol, "confirmed", slot)
        db.mark_strategy_confirmed(symbol, cand["strategy"])
        db.log_attempt(symbol, run_id, "confirmed", "robust", "promoted",
                       f"cleared {months}-month deep validation, Apex, stress battery and robustness",
                       strategy=cand["strategy"], params_hash=h, cand_score=cand["score"], inc_score=conf_now,
                       detail={"deep_score": deep["score"], "apex": apex_info, "robust": rob})
        return {"attempted": True, "passed": True, "robust": True, "score": deep["score"], "promoted": True}
    return summary


def run_promotion(symbol: str, run_id: int | None, results: list[dict[str, Any]], df,
                  extra_confirmed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Called once per training cycle with the merged leaderboard and the fast-screen dataframe.
    Returns {"champion_updated": bool, "deep_validation": dict | None} for the run-now API."""
    fast_folds = build_folds(df)
    changed = _leader_step(symbol, run_id, results, fast_folds)
    try:  # give every account the risk size its strategy's Apex replay recommends
        from app.accounts import risk_sync

        risk_sync.sync()
    except Exception:  # noqa: BLE001
        pass
    conf = _confirmed_step(symbol, run_id, results, extra_confirmed)
    return {
        "champion_updated": bool(changed or (conf and conf.get("promoted"))),
        "deep_validation": conf,
    }
