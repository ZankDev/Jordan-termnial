"""BUBBLE RADAR API: /api/bubble — money-flow graph, detected loops with 0-100 scores, per-company red flags. See app/world/bubble.py."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.capital_routes import cached
from app.world import bubble

router = APIRouter(prefix="/api/bubble", tags=["bubble"])


@router.get("")
def api_bubble():
    return cached("bubble:radar", 1800, bubble.build)


@router.get("/graph")
def api_bubble_graph():
    """The curated graph and loops only (no live data): instant."""
    gd = bubble.graph_data()
    return {**gd, "loops": bubble.loops(gd["edges"])}
