"""ALT DATA API (prefix /api/altdata): satellite sites, Wayback imagery releases, AIS chokepoint stacking, anomaly alerts. See app/world/altdata.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.capital_routes import cached
from app.world import altdata

router = APIRouter(prefix="/api/altdata", tags=["altdata"])


@router.get("/sites")
def api_sites():
    return altdata.sites()


@router.get("/wayback")
def api_wayback():
    try:
        rel = cached("alt:wayback", 86400, altdata.wayback)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Wayback catalogue unavailable: {e}") from e
    return {"releases": rel, "n": len(rel)}


@router.get("/ships")
def api_ships():
    altdata.start_sampler()
    from app.api import world_gx_routes as gx

    d = gx.api_ships()
    return {**{k: d[k] for k in ("configured", "connected", "error", "count", "hint")}, "ships": d["ships"], "chokepoints": altdata.stacking(),
            "sample_every_s": altdata.SAMPLE_EVERY}


@router.get("/alerts")
def api_alerts():
    altdata.start_sampler()
    return cached("alt:alerts", 90, altdata.alerts)
