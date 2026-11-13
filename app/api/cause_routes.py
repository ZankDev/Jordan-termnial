"""WHY? cause explainer API: /api/cause?symbol=&from=&to=  (from/to = unix seconds or ISO-8601). See app/world/cause.py."""
from __future__ import annotations

import datetime as dt
import re
import time

from fastapi import APIRouter, HTTPException, Query

from app.api.capital_routes import cached
from app.world import cause

router = APIRouter(prefix="/api/cause", tags=["cause"])


def _ts(v: str | None, default: float) -> float:
    if v is None or v == "":
        return default
    try:
        f = float(v)
        return f / 1000 if f > 1e11 else f
    except ValueError:
        pass
    try:
        d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad time: {v}") from e


def _args(symbol: str, frm: str | None, to: str | None) -> tuple[str, float, float]:
    sym = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.=^\-]{1,15}", sym):
        raise HTTPException(status_code=400, detail="bad symbol")
    t1 = _ts(to, time.time())
    t0 = _ts(frm, t1 - 86400)
    if t0 > t1:
        t0, t1 = t1, t0
    if t1 - t0 < 600:
        t0 -= 1800
        t1 += 1800
    # round so near-identical selections share a cache entry
    step = 300 if t1 - t0 < 3 * 86400 else 3600
    return sym, round(t0 / step) * step, round(t1 / step) * step


@router.get("")
def api_cause(symbol: str, frm: str | None = Query(None, alias="from"), to: str | None = None):
    sym, t0, t1 = _args(symbol, frm, to)
    try:
        return cached(f"cause:{sym}:{int(t0)}:{int(t1)}", 300, lambda: cause.explain(sym, t0, t1))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/summary")
def api_cause_summary(symbol: str, frm: str | None = Query(None, alias="from"), to: str | None = None):
    sym, t0, t1 = _args(symbol, frm, to)
    d = cached(f"cause:{sym}:{int(t0)}:{int(t1)}", 300, lambda: cause.explain(sym, t0, t1))
    try:
        return {"summary": cached(f"cause:ai:{sym}:{int(t0)}:{int(t1)}", 1800, lambda: cause.summarize(d)), "engine": "claude -p"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"AI summary unavailable: {e}") from e
