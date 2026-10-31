"""NT LAB API: the strategy search with NinjaTrader 8 as the backtest provider (see app/ntlab)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.ntlab import mcp_server as tools
from app.ntlab import spec as sp
from app.ntlab import store

router = APIRouter(prefix="/api/ntlab")


@router.get("/status")
def get_status():
    return tools.t_status({}) | {"search": tools.t_search_status({})}


@router.get("/runs")
def get_runs(n: int = 20):
    return {"runs": store.runs(n)}


@router.get("/leaderboard")
def get_leaderboard(run_id: int | None = None, n: int = 40, champions_only: bool = False):
    rows = store.leaderboard(run_id, min(n, 200), champions_only)
    for r in rows:
        r["rules"] = sp.describe(r["spec"])
    return {"rows": rows}


@router.get("/candidate")
def get_candidate(run_id: int, spec_id: str):
    c = tools.t_candidate({"run_id": run_id, "spec_id": spec_id})
    if c.get("error"):
        raise HTTPException(404, c["error"])
    return c


@router.post("/start")
def post_start(body: dict[str, Any]):
    out = tools.t_search_start(body)
    if not out.get("ok"):
        raise HTTPException(409, out.get("error"))
    return out


@router.post("/install")
def post_install():
    return tools.t_install({})
