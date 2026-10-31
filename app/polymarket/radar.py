"""The 'momo radar' read-only view — every running 5-minute session's live gate status
under the champion's params (or the strategy defaults). Kept as a thin wrapper: the
live engine computes it once per tick and everything (UI, API) reads that one result.
"""
from __future__ import annotations

from typing import Any

from app.polymarket import live_engine


def live_sessions_view() -> list[dict[str, Any]]:
    return live_engine.live_view()["rows"]
