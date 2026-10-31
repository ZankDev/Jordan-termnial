"""The Field page API: every evaluated candidate as a point (win rate vs reward-to-risk),
plus exact headline statistics."""
from __future__ import annotations

import json
import statistics
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from app import db
from app.api.capital_routes import cached
from app.optimizer.epoch import SCORING_EPOCH

router = APIRouter(prefix="/api")

MAX_POINTS = 10_000
FLAG_NAMES = {1: "diverged", 2: "too smooth", 4: "burn limit", 8: "regime", 16: "score <= 0"}


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


@router.get("/field")
def get_field(symbol: str = "NQ", hours: float = 24, after_id: int | None = None, strategy: str | None = None,
              limit: int = MAX_POINTS):
    # the full scatter is the heavy one (thousands of rows); serve it stale-while-revalidate so a request
    # never waits on it while the trainer is busy. Incremental polls (after_id) are tiny and stay live.
    if after_id is None:
        key = f"field:{symbol.upper()}:{hours}:{strategy}:{limit}"
        return cached(key, 8, lambda: _field(symbol, hours, None, strategy, limit))
    return _field(symbol, hours, after_id, strategy, limit)


def _field(symbol: str, hours: float, after_id: int | None, strategy: str | None, limit: int):
    """Points come from the (sampled) candidate log; the stats come from the exact per-cycle
    counts, so the headline numbers stay honest however the scatter is sampled.

    point = [id, wr_net, rr_net, exp_net, wr_gross, rr_gross, exp_gross, n, tier, strategy_idx, apex]
    tier: 0 rejected by the trade-count gate, 1 scored but flagged, 2 survivor (passes every gate)."""
    symbol = symbol.upper()
    limit = max(100, min(limit, MAX_POINTS))
    # hours <= 0 means ALL TIME: everything still retained (survivors + cycle stats are kept 180 days,
    # the sampled non-survivors 48 h)
    cutoff = 0 if hours <= 0 else int(time.time() - max(0.1, min(hours, 24 * 180)) * 3600)
    where = ["symbol = ?", "epoch = ?", "ts >= ?"]
    args: list[Any] = [symbol, SCORING_EPOCH, cutoff]
    if strategy:
        where.append("strategy = ?")
        args.append(strategy)
    clause = " AND ".join(where)
    cols = "id, wr_net, rr_net, exp_net, wr_gross, rr_gross, exp_gross, n, tier, strategy, apex"

    with db.get_conn() as conn:
        if after_id is not None:
            rows = conn.execute(f"SELECT {cols} FROM candidate_log WHERE {clause} AND id > ? ORDER BY id LIMIT ?",
                                (*args, after_id, limit)).fetchall()
        else:
            # survivors always in; the sampled majority fills the rest, newest first
            survivors = conn.execute(f"SELECT {cols} FROM candidate_log WHERE {clause} AND tier = 2 ORDER BY id DESC LIMIT 3000",
                                     args).fetchall()
            others = conn.execute(f"SELECT {cols} FROM candidate_log WHERE {clause} AND tier < 2 ORDER BY id DESC LIMIT ?",
                                  (*args, max(0, limit - len(survivors)))).fetchall()
            rows = sorted([*survivors, *others], key=lambda r: r["id"])
        last = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM candidate_log WHERE symbol = ? AND epoch = ?",
                            (symbol, SCORING_EPOCH)).fetchone()["m"]
        names = [r["strategy"] for r in conn.execute(
            "SELECT DISTINCT strategy FROM candidate_log WHERE symbol = ? AND epoch = ? ORDER BY strategy",
            (symbol, SCORING_EPOCH)).fetchall()]
        cycles = conn.execute(
            "SELECT * FROM field_cycle_stats WHERE symbol = ? AND epoch = ? AND ts >= ? ORDER BY id",
            (symbol, SCORING_EPOCH, cutoff)).fetchall()

    index = {n: i for i, n in enumerate(names)}
    points = [
        [r["id"], round(r["wr_net"], 2), None if r["rr_net"] is None else round(r["rr_net"], 3), round(r["exp_net"], 4),
         round(r["wr_gross"], 2), None if r["rr_gross"] is None else round(r["rr_gross"], 3), round(r["exp_gross"], 4),
         r["n"], r["tier"], index.get(r["strategy"], -1), r["apex"]]
        for r in rows
    ]

    total = sum(c["n_total"] for c in cycles)
    surv = sum(c["n_survivors"] for c in cycles)
    gp, npos, gpnn = (sum(c[k] for c in cycles) for k in ("n_gp", "n_np", "n_gp_nn"))
    costs = [c["median_cost_r"] for c in cycles if c["median_cost_r"] is not None]
    return {
        "symbol": symbol,
        "epoch": SCORING_EPOCH,
        "window_hours": hours,
        "generated_at": time.time(),
        "last_id": last,
        "strategies": names,
        "points": points,
        "stats": {
            "field": total,
            "survivors": surv,
            "gross_pos": {"n": gp, "pct": _pct(gp, total)},
            "net_pos": {"n": npos, "pct": _pct(npos, total)},
            "gp_nn": {"n": gpnn, "pct": _pct(gpnn, total)},
            "median_cost_r": round(statistics.median(costs), 4) if costs else 0.0,
            "cycles": len(cycles),
            "sampled_points": len(points),
        },
    }


@router.get("/field/point/{point_id}")
def get_field_point(point_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM candidate_log WHERE id = ?", (point_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="point not found (pruned or never logged)")
    d = dict(row)
    d["params"] = json.loads(d.pop("params_json")) if d.get("params_json") else None
    d["flag_names"] = [name for bit, name in FLAG_NAMES.items() if d["flags"] & bit]
    return d
