"""Leader-tier promotion for Polymarket strategies — a simpler cousin of
app/optimizer/promotion.py's _leader_step (no Apex fast-proxy / stress battery /
confirmed tier: there is no multi-year archive of 5-minute sessions to run that
machinery against). What it does keep:

  * the incumbent is re-scored every cycle on the SAME folds its challengers were
    just scored on, so scores are always comparable;
  * a challenger must outscore the incumbent by a margin, and the incumbent is
    protected for a short dwell time;
  * NEW: a candidate must also be profitable on a HOLDOUT — the most recent slice of
    sessions the search never saw. The search picks the best of hundreds of
    candidates on the same folds, which is optimistic by construction; the holdout is
    the check that the winner isn't just the luckiest of the batch.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from app import db as _db
from app.optimizer.search import params_hash
from app.polymarket import backtest
from app.polymarket.pm_search import evaluate_pm_candidate, is_eligible

LEADER_MARGIN_ABS = 0.25
LEADER_MARGIN_REL = 0.15
LEADER_MIN_DWELL_SECONDS = 1800
LEADER_FAIL_GRACE = 3
LEADER_TOP_K = 10
HOLDOUT_MIN_TRADES = 4


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _seconds_since(ts: str | None) -> float:
    if not ts:
        return 1e18
    try:
        then = dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - then).total_seconds()
    except ValueError:
        return 1e18


def holdout_check(params: dict[str, Any], holdout_sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Run a candidate on sessions the search never touched. ok = it made money
    there on enough trades to mean something (with too few trades the check is
    inconclusive, which counts as NOT ok — it must earn promotion, not be assumed)."""
    if not holdout_sessions:
        return {"ok": False, "reason": "no_holdout", "n": 0}
    res = backtest.run_pm_backtest("momo", params, holdout_sessions)
    m = res.metrics
    ok = m["total_trades"] >= HOLDOUT_MIN_TRADES and m["net_pnl"] > 0
    return {"ok": ok, "n": m["total_trades"], "net_pnl": m["net_pnl"], "win_rate_pct": m["win_rate_pct"],
            "profit_factor": m["profit_factor"], "edge_t_stat": m["edge_t_stat"], "n_sessions": len(holdout_sessions)}


def _slot_from_result(r: dict[str, Any], run_id: int | None, version: int) -> dict[str, Any]:
    return {
        "strategy": "momo", "params": r["params"], "params_hash": params_hash(r["params"]),
        "score": r["score"], "train_metrics": r["train_metrics"], "test_metrics": r["test_metrics"],
        "fold_scores": r["fold_scores"], "holdout": r.get("holdout"), "run_id": run_id, "version": version,
        "promoted_at": _utcnow(), "last_rescored_at": _utcnow(), "consecutive_fails": 0,
    }


def _flag_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "n_results": len(results),
        "n_eligible": sum(1 for r in results if is_eligible(r)),
        "n_diverged": sum(1 for r in results if r.get("diverged")),
        "n_too_smooth": sum(1 for r in results if r.get("too_smooth")),
        "n_burned": sum(1 for r in results if r.get("exceeds_burn_limit")),
    }


def leader_step(
    run_id: int | None, results: list[dict[str, Any]], folds: list[tuple[list, list]],
    holdout_sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Returns {"changed": bool, "outcome": str, "reason": str, "champion": slot|None}."""
    holdout_sessions = holdout_sessions or []
    counts = _flag_counts(results)
    inc = _get_slot("leader")

    pooled: dict[str, dict[str, Any]] = {}
    for r in results:
        if not (is_eligible(r) and r["score"] > 0):
            continue
        h = params_hash(r["params"])
        if h not in pooled or r["score"] > pooled[h]["score"]:
            pooled[h] = r
    candidates = sorted(pooled.values(), key=lambda r: r["score"], reverse=True)[:LEADER_TOP_K]
    best = None
    n_holdout_fail = 0
    for r in candidates:
        r["holdout"] = holdout_check(r["params"], holdout_sessions)
        if r["holdout"]["ok"]:
            best = r
            break
        n_holdout_fail += 1
    counts["n_holdout_rejected"] = n_holdout_fail

    inc_now: float | None = None
    inc_ok = False
    if inc:
        res = evaluate_pm_candidate(inc["params"], folds, "momo")
        inc_hold = holdout_check(inc["params"], holdout_sessions)
        inc_ok = res is not None and is_eligible(res) and res["score"] > 0
        inc_now = res["score"] if res is not None else None
        fails = 0 if inc_ok else (inc.get("consecutive_fails") or 0) + 1
        patch: dict[str, Any] = {"last_rescored_at": _utcnow(), "consecutive_fails": fails, "holdout": inc_hold}
        if res:
            patch.update(score=res["score"], train_metrics=res["train_metrics"], test_metrics=res["test_metrics"],
                         fold_scores=res["fold_scores"])
        _patch_slot("leader", **patch)

    def done(changed: bool, outcome: str, reason: str) -> dict[str, Any]:
        return {"changed": changed, "outcome": outcome, "reason": reason, "counts": counts, "champion": _get_slot("leader")}

    def promote(reason: str) -> dict[str, Any]:
        version = _bump_version()
        if inc:
            _retire_slot("leader", f"superseded:{reason}")
        _put_slot("leader", _slot_from_result(best, run_id, version))
        _log_attempt(run_id, "leader", "pool", "promoted", reason, strategy="momo",
                     params_hash=params_hash(best["params"]), cand_score=best["score"], inc_score=inc_now, detail=counts)
        return done(True, "promoted", reason)

    if not inc:
        if best:
            return promote("no_incumbent")
        why = (f"no candidate cleared the gates AND the holdout this cycle ({counts['n_eligible']} of "
               f"{counts['n_results']} scored candidates eligible, {n_holdout_fail} failed the holdout)")
        _log_attempt(run_id, "leader", "pool", "skipped", why, detail=counts)
        return done(False, "skipped", why)

    if not inc_ok:
        if best and params_hash(best["params"]) != inc["params_hash"]:
            return promote("incumbent_failed_revalidation")
        fails = (inc.get("consecutive_fails") or 0) + 1
        if fails >= LEADER_FAIL_GRACE:
            _retire_slot("leader", "decayed")
            _bump_version()
            why = f"incumbent failed re-validation {fails} cycles in a row with no replacement"
            _log_attempt(run_id, "leader", "rescore", "demoted", why, strategy="momo",
                         params_hash=inc["params_hash"], inc_score=inc_now, detail=counts)
            return done(True, "demoted", why)
        why = f"incumbent failed re-validation ({fails}/{LEADER_FAIL_GRACE}), no replacement yet"
        _log_attempt(run_id, "leader", "rescore", "held", why, strategy="momo", params_hash=inc["params_hash"],
                     inc_score=inc_now, detail=counts)
        return done(False, "held", why)

    if best and params_hash(best["params"]) != inc["params_hash"]:
        margin = max(LEADER_MARGIN_ABS, LEADER_MARGIN_REL * inc_now)
        if best["score"] >= inc_now + margin:
            if _seconds_since(inc.get("promoted_at")) >= LEADER_MIN_DWELL_SECONDS:
                return promote("outscored")
            why = "challenger clears the margin but the incumbent is inside its dwell window"
            _log_attempt(run_id, "leader", "dwell", "held", why, strategy="momo", params_hash=params_hash(best["params"]),
                         cand_score=best["score"], inc_score=inc_now, margin=margin, detail=counts)
            return done(False, "held", why)
        why = f"best challenger {best['score']:.2f} is under incumbent {inc_now:.2f} + margin {margin:.2f}"
        _log_attempt(run_id, "leader", "margin", "kept", why, strategy="momo", params_hash=params_hash(best["params"]),
                     cand_score=best["score"], inc_score=inc_now, margin=margin, detail=counts)
        return done(False, "kept", why)

    _log_attempt(run_id, "leader", "rescore", "kept", "incumbent still the best available", detail=counts)
    return done(False, "kept", "incumbent still the best available")


# ---------------------------------------------------------------------------
# Thin DB helpers over pm_champion_slots / pm_champion_history / pm_promotion_attempts
# ---------------------------------------------------------------------------
_JSON_FIELDS = {"params_json": "params", "train_metrics_json": "train_metrics", "test_metrics_json": "test_metrics",
                "fold_scores_json": "fold_scores", "holdout_json": "holdout"}


def _get_slot(tier: str) -> dict[str, Any] | None:
    with _db.get_conn() as conn:
        row = conn.execute("SELECT * FROM pm_champion_slots WHERE tier = ?", (tier,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for col, key in _JSON_FIELDS.items():
            raw = d.pop(col, None)
            d[key] = json.loads(raw) if raw else None
        return d


def _put_slot(tier: str, slot: dict[str, Any]) -> None:
    with _db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO pm_champion_slots (tier, strategy, params_json, params_hash, score, "
            "train_metrics_json, test_metrics_json, fold_scores_json, holdout_json, run_id, version, promoted_at, "
            "last_rescored_at, consecutive_fails) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tier, slot["strategy"], json.dumps(slot["params"]), slot["params_hash"], slot.get("score"),
             json.dumps(slot.get("train_metrics")), json.dumps(slot.get("test_metrics")),
             json.dumps(slot.get("fold_scores")), json.dumps(slot.get("holdout")), slot.get("run_id"),
             slot.get("version"), slot.get("promoted_at"), slot.get("last_rescored_at"), slot.get("consecutive_fails", 0)),
        )


def _patch_slot(tier: str, **fields: Any) -> None:
    if not fields:
        return
    json_cols = {v: k for k, v in _JSON_FIELDS.items()}
    sets, vals = [], []
    for key, value in fields.items():
        col = json_cols.get(key, key)
        sets.append(f"{col} = ?")
        vals.append(json.dumps(value) if key in json_cols else value)
    with _db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"UPDATE pm_champion_slots SET {', '.join(sets)} WHERE tier = ?", (*vals, tier))


def _retire_slot(tier: str, reason: str) -> None:
    with _db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM pm_champion_slots WHERE tier = ?", (tier,)).fetchone()
        if not row:
            return
        conn.execute(
            "INSERT INTO pm_champion_history (ts, tier, strategy, params_json, score, reason, version) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)",
            (tier, row["strategy"], row["params_json"], row["score"], reason, row["version"]),
        )
        conn.execute("DELETE FROM pm_champion_slots WHERE tier = ?", (tier,))


def _bump_version() -> int:
    with _db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM meta WHERE key = 'pm_champion_version'").fetchone()
        nxt = (int(row["value"]) if row else 0) + 1
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('pm_champion_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(nxt),),
        )
        return nxt


def _log_attempt(run_id: int | None, tier: str, stage: str, outcome: str, reason: str, strategy: str | None = None,
                 params_hash: str | None = None, cand_score: float | None = None, inc_score: float | None = None,
                 margin: float | None = None, detail: dict[str, Any] | None = None) -> None:
    with _db.get_conn() as conn:
        conn.execute(
            "INSERT INTO pm_promotion_attempts (ts, run_id, tier, stage, outcome, reason, strategy, params_hash, "
            "cand_score, inc_score, margin, detail_json) VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, tier, stage, outcome, reason, strategy, params_hash, cand_score, inc_score, margin,
             json.dumps(detail) if detail else None),
        )
        conn.execute("DELETE FROM pm_promotion_attempts WHERE id <= (SELECT MAX(id) FROM pm_promotion_attempts) - 2000")


def get_champion() -> dict[str, Any] | None:
    return _get_slot("leader")


def list_attempts(limit: int = 25) -> list[dict[str, Any]]:
    with _db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM pm_promotion_attempts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        raw = d.pop("detail_json", None)
        d["detail"] = json.loads(raw) if raw else None
        out.append(d)
    return out


def list_history(limit: int = 20) -> list[dict[str, Any]]:
    with _db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM pm_champion_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
