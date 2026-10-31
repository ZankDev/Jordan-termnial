"""The Polymarket Field page API — mirrors app/api/field_routes.py's shape (same
cached stale-while-revalidate pattern, same 11-field point tuple) off
pm_candidate_log/pm_field_cycle_stats, so the EXISTING web/dashboard.js ScatterField
class needs zero changes (confirmed generic — see the plan) — just a second
instantiation pointed at this endpoint.

point = [id, wr_net, rr_net, exp_net, wr_gross, rr_gross, exp_gross, n, tier,
         strategy_idx, apex]
Gross columns mirror net (this backtest doesn't model commission, so gross == net
here — an honest simplification, not fabricated data) and strategy_idx is always 0
(momo is the only strategy so far; the field stays ready for a second one).
tier: 0 rejected by the trade-count gate, 1 scored but flagged, 2 survivor.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException

from app import db
from app.api.capital_routes import cached

router = APIRouter(prefix="/api/pm")

MAX_POINTS = 10_000


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


@router.get("/field")
def get_pm_field(hours: float = 24, after_id: int | None = None, limit: int = MAX_POINTS) -> dict[str, Any]:
    if after_id is None:
        return cached(f"pm_field:{hours}:{limit}", 8, lambda: _field(hours, None, limit))
    return _field(hours, after_id, limit)


def _field(hours: float, after_id: int | None, limit: int) -> dict[str, Any]:
    limit = max(100, min(limit, MAX_POINTS))
    cutoff = 0 if hours <= 0 else int(time.time() - max(0.1, min(hours, 24 * 180)) * 3600)
    cols = "id, wr_net, rr_net, exp_net, n, tier"

    with db.get_conn() as conn:
        if after_id is not None:
            rows = conn.execute(
                f"SELECT {cols} FROM pm_candidate_log WHERE ts >= ? AND id > ? ORDER BY id LIMIT ?",
                (cutoff, after_id, limit),
            ).fetchall()
        else:
            survivors = conn.execute(
                f"SELECT {cols} FROM pm_candidate_log WHERE ts >= ? AND tier = 2 ORDER BY id DESC LIMIT 3000", (cutoff,)
            ).fetchall()
            others = conn.execute(
                f"SELECT {cols} FROM pm_candidate_log WHERE ts >= ? AND tier < 2 ORDER BY id DESC LIMIT ?",
                (cutoff, max(0, limit - len(survivors))),
            ).fetchall()
            rows = sorted([*survivors, *others], key=lambda r: r["id"])
        last = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM pm_candidate_log").fetchone()["m"]
        cycles = conn.execute("SELECT * FROM pm_field_cycle_stats WHERE ts >= ? ORDER BY id", (cutoff,)).fetchall()

    def _r(v, d):
        return None if v is None else round(v, d)

    points = [
        [r["id"], _r(r["wr_net"], 2), _r(r["rr_net"], 3), _r(r["exp_net"], 4),
         _r(r["wr_net"], 2), _r(r["rr_net"], 3), _r(r["exp_net"], 4), r["n"], r["tier"], 0, None]
        for r in rows
    ]

    total = sum(c["n_total"] for c in cycles)
    surv = sum(c["n_survivors"] for c in cycles)
    return {
        "window_hours": hours,
        "generated_at": time.time(),
        "last_id": last,
        "points": points,
        "stats": {
            "field": total,
            "survivors": surv,
            "survivor_pct": _pct(surv, total),
            "cycles": len(cycles),
            "sampled_points": len(points),
        },
    }


@router.get("/field/point/{point_id}")
def get_pm_field_point(point_id: int) -> dict[str, Any]:
    import json

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM pm_candidate_log WHERE id = ?", (point_id,)).fetchone()
    if not row:
        raise HTTPException(404, "point not found (pruned or never logged)")
    d = dict(row)
    d["params"] = json.loads(d.pop("params_json")) if d.get("params_json") else None
    return d
