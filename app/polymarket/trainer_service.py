"""Polymarket momo trainer — three background loops and the shared status they publish.

  training loop  : continuous cycles of backtest search over the corpus; each cycle
                   re-scores the elites from earlier cycles first (so progress
                   accumulates), searches, checks the leaders on a HOLDOUT the search
                   never saw, promotes a champion, and re-points the auto paper accounts
                   at the results — the forward test.
  backfill loop  : keeps the corpus current ("recent") and walks it back in time
                   ("deep") in bounded batches, so there is always more data.
  digest loop    : an hourly Telegram digest of corpus / training / account equity.

Everything the UI shows about training comes from STATE (persisted to meta so it
survives restarts) plus the pm_optimizer_runs history — no guessing from the outside.
"""
from __future__ import annotations

import asyncio
import json
import os
import logging
import time
from typing import Any

from app import db
from app.polymarket import auto_accounts, backfill, backtest, ledger, notify, pm_folds, pm_promotion, pm_search, store
from app.polymarket.momo_strategy import default_params
from app.optimizer.search import params_hash

logger = logging.getLogger(__name__)

CYCLE_REST_SECONDS = 20        # pause between training cycles
SEARCH_SECONDS = 100           # wall-clock budget for one search
SEARCH_ITERATIONS = 400
MIN_SESSIONS_TO_TRAIN = 300    # below this a walk-forward fold is too thin to judge honestly
MAX_SESSIONS = 8_000           # sessions per cycle after thinning (bounds time and memory)
RECENT_FULL = 1_500            # the newest sessions are always kept at full density
HOLDOUT_FRAC = 0.15
K_FOLDS = 3
TEST_FRAC = 0.2
ELITE_KEEP = 40
HISTORY_KEEP = 400
DIGEST_EVERY_SECONDS = 3600

STATE: dict[str, Any] = {
    "phase": "starting", "cycle": 0, "iter_done": 0, "iter_total": 0, "cycle_best": None,
    "best_score_all": None, "total_evaluated": 0, "n_sessions": 0, "n_search": 0, "n_holdout": 0,
    "champion_score": None, "champion_version": None, "last_outcome": None, "last_reason": None,
    "last_cycle_at": None, "cycle_started_at": None, "cycle_seconds": None, "backfill": {}, "deep": {},
    "error": None, "auto": {},
}
_last_persist = 0.0


def _set(**kw: Any) -> None:
    global _last_persist
    STATE.update(kw)
    now = time.time()
    if now - _last_persist > 2.0 or "phase" in kw:
        _last_persist = now
        try:
            db.meta_set("pm_trainer_state", json.dumps(STATE, default=str))
        except Exception:  # noqa: BLE001
            pass


def live_state() -> dict[str, Any]:
    """The trainer's state for the UI: in-process STATE, or — when it runs in the background worker (app/worker.py) — the
    copy the worker persists to meta 'pm_trainer_state' every couple of seconds."""
    if os.environ.get("NOSTRO_TRAINER_IN_WORKER") == "1":
        try:
            return {**STATE, **json.loads(db.meta_get("pm_trainer_state") or "{}")}
        except ValueError:
            pass
    return dict(STATE)


def load_state() -> None:
    raw = db.meta_get("pm_trainer_state")
    if raw:
        try:
            saved = json.loads(raw)
            for k in ("cycle", "best_score_all", "total_evaluated", "champion_score", "champion_version", "last_outcome",
                      "last_reason", "last_cycle_at", "backfill", "deep", "auto"):
                if k in saved:
                    STATE[k] = saved[k]
        except ValueError:
            pass


def history(limit: int = HISTORY_KEEP) -> list[dict[str, Any]]:
    raw = db.meta_get("pm_trainer_history")
    try:
        return (json.loads(raw) if raw else [])[-limit:]
    except ValueError:
        return []


def _push_history(entry: dict[str, Any]) -> None:
    h = history(HISTORY_KEEP - 1) + [entry]
    db.meta_set("pm_trainer_history", json.dumps(h))


def _save_run(strategy: str, n_iterations: int, n_sessions: int, results: list[dict[str, Any]]) -> int:
    top = results[0] if results else None
    slim = [{k: v for k, v in r.items() if k not in ("fold_net", "fold_trades")} for r in results[:20]]
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pm_optimizer_runs (created_at, strategy, n_iterations, n_sessions, result_json, top_score) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
            (strategy, n_iterations, n_sessions, json.dumps(slim), top["score"] if top else None),
        )
        conn.execute("DELETE FROM pm_optimizer_runs WHERE id <= (SELECT MAX(id) FROM pm_optimizer_runs) - 300")
        return cur.lastrowid


def _elites() -> list[dict[str, Any]]:
    raw = db.meta_get("pm_elite")
    try:
        return json.loads(raw) if raw else []
    except ValueError:
        return []


def run_training_cycle() -> dict[str, Any]:
    """One full cycle. Safe to call directly (e.g. from a script)."""
    t_cycle = time.time()
    _set(phase="loading corpus", cycle_started_at=t_cycle, iter_done=0, iter_total=0, cycle_best=None, error=None)
    n_total = store.count_resolved_sessions()
    # A corpus bigger than one search can chew is thinned by an even stride over its whole
    # span (so the search sees weeks, not just the last few days), keeping the newest
    # sessions at full density — they are the most like what live trading faces.
    stride = max(1, -(-n_total // (MAX_SESSIONS - RECENT_FULL)))
    recent = store.list_resolved_sessions(limit=RECENT_FULL, latest=True)
    older = store.list_resolved_sessions(limit=MAX_SESSIONS, latest=True, stride=stride) if stride > 1 else []
    by_id = {s["id"]: s for s in older}
    by_id.update({s["id"]: s for s in recent})
    sessions = sorted(by_id.values(), key=lambda s: s["end_ts"]) if stride > 1 else store.list_resolved_sessions(limit=MAX_SESSIONS, latest=True)
    n = len(sessions)
    _set(n_sessions=n_total)
    if n < MIN_SESSIONS_TO_TRAIN:
        _set(phase="waiting for data", last_reason=f"need {MIN_SESSIONS_TO_TRAIN} sessions, have {n}")
        return {"status": "not_enough_sessions", "n_sessions": n, "need": MIN_SESSIONS_TO_TRAIN}

    cut = int(n * (1 - HOLDOUT_FRAC))
    search_sessions, holdout_sessions = sessions[:cut], sessions[cut:]
    backtest.retain({s["id"] for s in sessions})
    backtest.preload(sessions)
    folds = pm_folds.build_session_folds(search_sessions, k_folds=K_FOLDS, test_frac=TEST_FRAC)
    if not folds:
        _set(phase="waiting for data", last_reason="not enough sessions for walk-forward folds")
        return {"status": "no_folds", "n_sessions": n}
    _set(n_search=len(search_sessions), n_holdout=len(holdout_sessions))

    champion = pm_promotion.get_champion()
    seeds: list[dict[str, Any]] = [default_params()]
    if champion:
        seeds.append(champion["params"])
    seeds += [e["params"] for e in _elites()]

    _set(phase="searching")
    collector = pm_search.PmFieldCollector()

    def progress(done: int, total: int, best: float | None) -> None:
        _set(iter_done=done, iter_total=total, cycle_best=best)

    results = pm_search.pm_random_search(
        folds, "momo", n_iterations=SEARCH_ITERATIONS, top_n=ELITE_KEEP, sink=collector.add, seed_params=seeds,
        max_seconds=SEARCH_SECONDS, on_progress=progress,
    )
    evaluated = collector.counts["total"]
    run_id = _save_run("momo", SEARCH_ITERATIONS, n, results)
    collector.flush(run_id)

    _set(phase="validating on holdout")
    step = pm_promotion.leader_step(run_id, results, folds, holdout_sessions)
    champion = step["champion"]

    eligible = [r for r in results if pm_search.is_eligible(r) and r["score"] > 0]
    db.meta_set("pm_elite", json.dumps([{"params": r["params"], "score": r["score"]} for r in eligible[:ELITE_KEEP]]))

    _set(phase="updating paper accounts")
    try:
        champ_hash = champion["params_hash"] if champion else None
        cand_pool = [r for r in eligible if params_hash(r["params"]) != champ_hash]
        auto = auto_accounts.sync(champion, cand_pool)
    except Exception:  # noqa: BLE001
        logger.exception("auto account sync failed")
        auto = {"error": "sync failed"}

    best_cycle = eligible[0]["score"] if eligible else (results[0]["score"] if results else None)
    prev_best = STATE.get("best_score_all")
    best_all = max([x for x in (prev_best, best_cycle) if x is not None], default=None)
    secs = round(time.time() - t_cycle, 1)
    _set(
        phase="resting", cycle=STATE["cycle"] + 1, last_cycle_at=time.time(), cycle_seconds=secs,
        total_evaluated=STATE["total_evaluated"] + evaluated, best_score_all=best_all,
        champion_score=champion["score"] if champion else None,
        champion_version=champion["version"] if champion else None,
        last_outcome=step["outcome"], last_reason=step["reason"], auto=auto, cycle_best=best_cycle,
    )
    _push_history({
        "ts": time.time(), "cycle": STATE["cycle"], "best_score": best_cycle, "champion_score": STATE["champion_score"],
        "n_eval": evaluated, "n_elig": len(eligible), "n_sessions": n_total, "secs": secs, "outcome": step["outcome"],
        "top_trades": (eligible[0]["test_metrics"]["total_trades"] if eligible else None),
        "top_wr": (eligible[0]["test_metrics"]["win_rate_pct"] if eligible else None),
    })
    if step["outcome"] in ("promoted", "demoted") and champion:
        notify.notify_champion(champion, step["outcome"], step["reason"], n_total)
    return {"status": "ok", "run_id": run_id, "n_sessions": n_total, "evaluated": evaluated, "outcome": step["outcome"],
            "champion_score": STATE["champion_score"], "secs": secs}


# --------------------------------------------------------------------------- loops
async def scheduler_loop() -> None:
    """The training loop ('polymarket' NOSTRO_LOOPS token)."""
    load_state()
    try:
        await asyncio.to_thread(auto_accounts.ensure_baseline)
    except Exception:  # noqa: BLE001
        logger.exception("ensure_baseline failed")
    await asyncio.sleep(5)
    while True:
        try:
            status = await asyncio.to_thread(run_training_cycle)
            logger.info("polymarket trainer cycle: %s", status)
        except Exception as e:  # noqa: BLE001
            logger.exception("polymarket trainer cycle failed")
            _set(phase="error", error=f"{type(e).__name__}: {e}")
        await asyncio.sleep(CYCLE_REST_SECONDS if STATE.get("phase") != "waiting for data" else 60)


def corpus_stats() -> dict[str, Any]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT asset, COUNT(*) n, MIN(end_ts) mn, MAX(end_ts) mx FROM pm_sessions "
            "WHERE resolved_up IS NOT NULL AND COALESCE(n_ticks, 2) >= 2 GROUP BY asset"
        ).fetchall()
        live_ticks = conn.execute("SELECT COUNT(*) c FROM pm_session_prices").fetchone()["c"]
    per_asset = {r["asset"]: {"n": r["n"], "oldest": r["mn"], "newest": r["mx"]} for r in rows}
    total = sum(v["n"] for v in per_asset.values())
    return {"total": total, "per_asset": per_asset, "price_ticks": live_ticks,
            "oldest": min((v["oldest"] for v in per_asset.values()), default=None),
            "newest": max((v["newest"] for v in per_asset.values()), default=None)}


async def backfill_loop() -> None:
    """Recent + deep backfill, forever. Deep batches are bounded so the loop keeps
    cycling (and the recent pass keeps the corpus current) while history fills in."""
    await asyncio.sleep(3)
    while True:
        try:
            recent = await asyncio.to_thread(backfill.backfill_all, 3)
            STATE["backfill"] = {"at": time.time(), "stored": recent}
            deep = await asyncio.to_thread(backfill.deep_all, 120)
            STATE["deep"] = {"at": time.time(), "series": deep,
                             "exhausted": all(v.get("exhausted") for v in deep.values()) if deep else False}
            _set()
            all_done = STATE["deep"]["exhausted"]
        except Exception:  # noqa: BLE001
            logger.exception("polymarket backfill cycle failed")
            all_done = False
        await asyncio.sleep(240 if all_done else 15)


async def digest_loop() -> None:
    """Hourly Telegram digest (first one a few minutes after startup, once a cycle has run)."""
    await asyncio.sleep(180)
    while True:
        try:
            st = live_state()
            if st.get("cycle", 0) > 0 or st.get("total_evaluated", 0) > 0:
                await asyncio.to_thread(send_digest)
        except Exception:  # noqa: BLE001
            logger.exception("polymarket digest failed")
        await asyncio.sleep(DIGEST_EVERY_SECONDS)


def send_digest(manual: bool = False) -> None:
    accounts = ledger.accounts_with_stats()
    curves = {a["id"]: [(_epoch(t), e) for t, e in a["curve"]] for a in accounts}
    hist = history(150)
    notify.notify_digest(live_state(), accounts, curves, hist, force=manual)


def _epoch(ts: Any) -> float:
    import datetime as dt

    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        d = dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()
