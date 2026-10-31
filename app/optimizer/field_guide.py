"""The FIELD as a feedback source for the search.

`candidate_log` (written by field_metrics.CandidateCollector every cycle) used to be telemetry only: the search never read it.
This module turns its survivors (tier 2: passed every gate, params stored) into extra SEEDS for the deep search, so the
search starts from regions that already produced survivors instead of re-rolling randomly.

Honest limit: only survivors keep their parameters (non-survivors are sampled and pruned without them), so the field can
point TOWARD good regions but never away from bad ones. A seed is only a starting point: it still has to pass the deep
search's folds, the sealed holdout and the long-history gate like any other candidate.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app import db

logger = logging.getLogger(__name__)

MIN_TRADES = 30
SCAN_ROWS = 400


def field_seeds(symbol: str, strategy: str, k: int = 6) -> list[dict[str, Any]]:
    """Up to k distinct parameter sets of recent survivors for this strategy, best expectancy first."""
    from app.optimizer.epoch import SCORING_EPOCH
    from app.optimizer.search import params_hash

    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT params_json, exp_net FROM candidate_log WHERE symbol = ? AND strategy = ? AND tier = 2 "
                "AND epoch = ? AND params_json IS NOT NULL AND n >= ? ORDER BY exp_net DESC LIMIT ?",
                (symbol, strategy, SCORING_EPOCH, MIN_TRADES, SCAN_ROWS),
            ).fetchall()
    except Exception:  # noqa: BLE001 - the field is an aid; never let it break the search
        logger.exception("field_guide: could not read candidate_log")
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        try:
            p = json.loads(r["params_json"])
        except (ValueError, TypeError):
            continue
        h = params_hash(p)
        if h in seen:
            continue
        seen.add(h)
        out.append(p)
        if len(out) >= k:
            break
    return out


def survivor_rates(symbol: str, limit_rows: int = 6000) -> dict[str, dict[str, float]]:
    """Per strategy: share of recent logged candidates that survived, for the daily digest and the budget."""
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT strategy, COUNT(*) AS n, SUM(CASE WHEN tier = 2 THEN 1 ELSE 0 END) AS surv FROM "
                "(SELECT strategy, tier FROM candidate_log WHERE symbol = ? ORDER BY id DESC LIMIT ?) GROUP BY strategy",
                (symbol, limit_rows),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {r["strategy"]: {"n": r["n"], "survivors": r["surv"] or 0, "rate": round((r["surv"] or 0) / r["n"], 4) if r["n"] else 0.0} for r in rows}
