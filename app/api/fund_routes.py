"""Fund management API: book (positions, trades, cash), portfolio overview with risk, limits, and the opportunity screen."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.fund import analytics, book

router = APIRouter(prefix="/api/fund")
SYMBOL = re.compile(r"[\w.\-=^]{1,20}")


class TradeIn(BaseModel):
    symbol: str
    side: str
    qty: float
    price: float
    fees: float = 0.0
    note: str = ""
    sleeve: str | None = None
    thesis: str | None = None


class CashIn(BaseModel):
    cash: float


@router.get("/overview")
def api_overview():
    return analytics.overview()


@router.post("/trade")
def api_trade(t: TradeIn):
    if not SYMBOL.fullmatch(t.symbol.strip()):
        raise HTTPException(status_code=400, detail="invalid symbol")
    try:
        return book.trade(t.symbol, t.side, t.qty, t.price, t.fees, t.note, t.sleeve, t.thesis)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/cash")
def api_cash(c: CashIn):
    if c.cash < 0:
        raise HTTPException(status_code=400, detail="cash cannot be negative")
    book.set_cash(c.cash)
    return {"cash": book.cash()}


@router.post("/limits")
def api_limits(body: dict[str, Any]):
    return book.set_limits(body)


@router.post("/screen/start")
def api_screen_start(universe: str = "mega", symbols: str = ""):
    """Score a universe with the WHY engine in the background. universe=mega (the ~50 largest US stocks), watch (pass symbols=A,B,C) or book (current holdings)."""
    if universe == "watch":
        syms = [s.strip().upper() for s in symbols.split(",") if SYMBOL.fullmatch(s.strip())][:80]
    elif universe == "book":
        syms = [p["symbol"] for p in book.positions()]
    else:
        from app.api import invest_routes as ir

        syms = [s for s, _n in ir.MONITORS["stocks"]]
    if not syms:
        raise HTTPException(status_code=400, detail="no symbols to scan")
    return analytics.start_screen(syms, universe)


@router.get("/screen")
def api_screen():
    return analytics.screen_status()
