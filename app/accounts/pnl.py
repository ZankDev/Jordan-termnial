"""Shared unrealized-P&L math, used by the Accounts-tab live tracker and the
Telegram notifier so there's one implementation of the formula, not two."""
from __future__ import annotations

from app.backtest.sizing import point_value


def position_point_value(symbol: str, position: dict | None) -> float:
    """Dollars per point for a whole open position. Legacy positions (no qty key) are
    1 mini, so accounts opened before position sizing existed keep their exact P&L."""
    position = position or {}
    return point_value(symbol, position.get("contract") or "mini") * float(position.get("qty") or 1)


def unrealized(
    symbol: str, side: str, entry_price: float, current_price: float, qty: float = 1.0, contract: str | None = None
) -> float:
    direction = 1 if side == "long" else -1
    return (current_price - entry_price) * direction * point_value(symbol, contract or "mini") * qty
