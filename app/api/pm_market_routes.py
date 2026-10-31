"""POLYMARKET › MARKETS — whole-market read-only data (see app/polymarket/market_data.py).
Separate prefix (/api/pmx) so nothing here can collide with the momo-bot routes under /api/polymarket."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.polymarket import market_data as md

router = APIRouter(prefix="/api/pmx")


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (LookupError, ValueError) as e:
        raise HTTPException(404 if isinstance(e, LookupError) else 400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Polymarket unavailable: {e}") from e


@router.get("/overview")
def get_overview(cat: str = "", sort: str = "vol24", q: str = "", limit: int = 80):
    return _wrap(md.overview, cat.upper(), sort, q, max(1, min(limit, 300)))


@router.get("/movers")
def get_movers(cat: str = "", n: int = 30):
    return {"items": _wrap(md.movers, cat.upper(), max(1, min(n, 100)))}


@router.get("/whales")
def get_whales(min_usd: float = 5000, limit: int = 60, market: str = ""):
    return {"items": _wrap(md.whales, max(500.0, min_usd), max(1, min(limit, 200)), market)}


@router.get("/event/{slug}")
def get_event(slug: str):
    e = _wrap(md.event_detail, slug)
    try:
        from app.api.capital_routes import _build_news, cached
        news = cached("news", 240, _build_news)
    except Exception:  # noqa: BLE001
        news = []
    return {**e, "news": md.linked_news(e["title"], news)}


@router.get("/history")
def get_history(token: str, interval: str = "1w"):
    return {"points": _wrap(md.history, token, interval)}
