"""Morning brief API: latest brief, history with realised returns, a manual build/send for testing."""
from __future__ import annotations

from fastapi import APIRouter

from app.morning import brief, earnings_report, loop

router = APIRouter(prefix="/api/morning")


@router.get("/latest")
def latest():
    return {"brief": brief.latest(), "loop": loop.status()}


@router.get("/history")
def history(limit: int = 30):
    return {"briefs": brief.history(max(1, min(120, limit))), "record": brief.record()}


@router.post("/build")
def build():
    """Rebuild today's brief without sending it."""
    b = brief.build()
    return {"day": b["day"], "hot": b["hot"], "nostro": b["nostro"], "catalysts": b["catalysts"], "record": b["record"], "text": b["text"]}


@router.get("/catalysts")
def catalysts_now(hours: float = 20.0):
    """Live catalyst scan (earnings, FDA, M&A, management, legal, macro) without touching the stored brief."""
    from app.morning import catalysts

    return catalysts.scan(hours=hours)


@router.post("/send-now")
def send_now():
    b = brief.build()
    return {"sent": brief.send(b), "day": b["day"], "text": b["text"]}


@router.post("/earnings-send-now")
def earnings_send_now(day: str | None = None):
    """Build and send today's earnings-slides album right now (ignores the already-sent flag)."""
    return earnings_report.send(day, force=True)


@router.get("/earnings-status")
def earnings_status():
    return earnings_report.status()


@router.post("/track")
def track():
    return brief.track()


@router.get("/pick")
def pick(symbol: str):
    """Was this symbol a morning pick recently? (the SECURITY page badge)"""
    sym = symbol.strip().upper()
    for b in brief.history(60):
        for p in b["picks"]:
            if p["symbol"] == sym:
                return {"day": b["day"], "pick": p}
    return {"day": None, "pick": None}
