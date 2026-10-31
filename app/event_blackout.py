"""News / economic-event blackout: pause NEW entries around the releases that move NQ/ES the most (CPI, FOMC, NFP).

Reads the stored economic calendar (filled by app.api.market_routes.refresh_calendar), so the live engine never waits on a
network call. Only new entries are blocked; open positions keep being managed (stops, targets, flatten) as normal.
Settings live in the meta table and are edited from NOSTRO > EXECUTION.
"""
from __future__ import annotations

import time
from typing import Any

from app import db

DEFAULTS = {"blackout_enabled": "1", "blackout_before": "15", "blackout_after": "30", "blackout_keywords": "CPI,FOMC,NON-FARM,FEDERAL FUNDS,POWELL"}


def settings() -> dict[str, Any]:
    g = lambda k: db.meta_get(k, DEFAULTS[k]) or DEFAULTS[k]  # noqa: E731
    return {"enabled": g("blackout_enabled") == "1", "before": int(g("blackout_before")), "after": int(g("blackout_after")),
            "keywords": [k.strip().upper() for k in g("blackout_keywords").split(",") if k.strip()]}


def save(enabled: bool, before: int, after: int, keywords: str) -> None:
    db.meta_set("blackout_enabled", "1" if enabled else "0")
    db.meta_set("blackout_before", str(max(0, min(int(before), 240))))
    db.meta_set("blackout_after", str(max(0, min(int(after), 480))))
    db.meta_set("blackout_keywords", keywords)


def _events(lo: float, hi: float) -> list[dict[str, Any]]:
    try:
        with db.get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM econ_events WHERE country = 'USD' AND impact = 'High' AND ts BETWEEN ? AND ? ORDER BY ts", (int(lo), int(hi))).fetchall()]
    except Exception:  # noqa: BLE001 - table not created yet
        return []


def current(now: float | None = None) -> dict[str, Any] | None:
    """The event whose blackout window contains `now`, or None."""
    now = now or time.time()
    s = settings()
    if not s["enabled"]:
        return None
    for e in _events(now - s["after"] * 60, now + s["before"] * 60):
        if any(k in e["title"].upper() for k in s["keywords"]):
            return e
    return None


def upcoming(hours: float = 72) -> list[dict[str, Any]]:
    s = settings()
    now = time.time()
    return [e for e in _events(now - s["after"] * 60, now + hours * 3600) if any(k in e["title"].upper() for k in s["keywords"])]
