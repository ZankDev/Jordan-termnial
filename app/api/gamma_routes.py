"""NQ GAMMA · VWAP API (prefix /api/gamma-vwap): live worker status, paper record and the backtest report. Paper only, places no orders."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter

router = APIRouter(prefix="/api/gamma-vwap", tags=["gamma-vwap"])


@router.get("/status")
def status():
    from app.invest import gamma_worker as gw

    return {"state": gw.STATE.get("last"), "error": gw.STATE.get("error"), "updated": gw.STATE.get("updated")}


@router.get("/tick")
def tick_now():
    """One worker tick on demand (also the way to see live numbers when the loop is not running on this server)."""
    from app.invest import gamma_worker as gw

    gw.ensure_tables()
    try:
        return {"state": gw.tick(), "error": None}
    except Exception as e:  # noqa: BLE001
        return {"state": None, "error": str(e)[:200]}


@router.get("/paper")
def paper():
    from app.invest import gamma_worker as gw

    gw.ensure_tables()
    return gw.paper_stats()


@router.get("/report")
def report():
    from app.invest import gamma_vwap as gv

    p = os.path.join(gv.CACHE, "report.json")
    return json.load(open(p)) if os.path.exists(p) else {"runs": [], "note": "run: python -m app.invest.gamma_vwap"}
