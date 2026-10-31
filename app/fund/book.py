"""The fund's book: positions, trades (average-cost accounting), cash, NAV snapshots and risk limits. Stored in the main SQLite database.

Accounting is simple and explicit: a buy raises the position and its average cost and reduces cash by qty x price + fees; a sell realises
(price - average cost) x qty - fees and returns cash. Nothing here places a real order: this is the fund's record and risk view.
"""
from __future__ import annotations

import json
import time
from typing import Any

from app import db

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS fund_positions (symbol TEXT PRIMARY KEY, qty REAL NOT NULL, avg_cost REAL NOT NULL, opened INTEGER, sleeve TEXT, thesis TEXT)",
    "CREATE TABLE IF NOT EXISTS fund_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, "
    "fees REAL DEFAULT 0, realized REAL DEFAULT 0, note TEXT)",
    "CREATE TABLE IF NOT EXISTS fund_nav (ts INTEGER PRIMARY KEY, nav REAL NOT NULL, cash REAL, invested REAL)",
]
LIMIT_DEFAULTS = {"max_position_pct": 20.0, "max_sector_pct": 40.0, "max_var95_pct": 2.5, "min_cash_pct": 5.0, "max_drawdown_pct": 15.0}


def ensure() -> None:
    with db.get_conn() as conn:
        for s in SCHEMA:
            conn.execute(s)


def cash() -> float:
    return float(db.meta_get("fund_cash", "0") or 0)


def set_cash(v: float) -> None:
    db.meta_set("fund_cash", str(float(v)))


def limits() -> dict[str, float]:
    try:
        return {**LIMIT_DEFAULTS, **{k: float(v) for k, v in json.loads(db.meta_get("fund_limits", "{}") or "{}").items() if k in LIMIT_DEFAULTS}}
    except ValueError:
        return dict(LIMIT_DEFAULTS)


def set_limits(new: dict[str, Any]) -> dict[str, float]:
    cur = limits()
    cur.update({k: float(v) for k, v in new.items() if k in LIMIT_DEFAULTS and float(v) > 0})
    db.meta_set("fund_limits", json.dumps(cur))
    return cur


def positions() -> list[dict[str, Any]]:
    ensure()
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM fund_positions WHERE qty > 0 ORDER BY symbol").fetchall()]


def trades(limit: int = 100) -> list[dict[str, Any]]:
    ensure()
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM fund_trades ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()]


def trade(symbol: str, side: str, qty: float, price: float, fees: float = 0.0, note: str = "", sleeve: str | None = None, thesis: str | None = None) -> dict[str, Any]:
    """Record a buy or sell. Raises ValueError on bad input (selling more than held, no cash for a buy)."""
    ensure()
    symbol, side = symbol.strip().upper(), side.lower()
    if side not in ("buy", "sell") or qty <= 0 or price <= 0 or fees < 0:
        raise ValueError("side must be buy/sell and qty, price must be positive")
    # db.get_conn() holds a process-wide, non-reentrant lock: cash is read before and written after the connection block, never inside it
    cash_now = cash()
    new_cash = cash_now
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM fund_positions WHERE symbol=?", (symbol,)).fetchone()
        held, avg = (row["qty"], row["avg_cost"]) if row else (0.0, 0.0)
        realized = 0.0
        if side == "buy":
            need = qty * price + fees
            if need > cash_now + 1e-6:
                raise ValueError(f"not enough cash: need {need:,.2f}, have {cash_now:,.2f} (set cash first)")
            new_qty = held + qty
            new_avg = (held * avg + qty * price + fees) / new_qty
            new_cash = cash_now - need
        else:
            if qty > held + 1e-9:
                raise ValueError(f"cannot sell {qty:g}: only {held:g} held")
            realized = (price - avg) * qty - fees
            new_qty, new_avg = held - qty, avg
            new_cash = cash_now + qty * price - fees
        conn.execute("INSERT INTO fund_positions (symbol, qty, avg_cost, opened, sleeve, thesis) VALUES (?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_cost=excluded.avg_cost, "
                     "sleeve=COALESCE(excluded.sleeve, fund_positions.sleeve), thesis=COALESCE(excluded.thesis, fund_positions.thesis)",
                     (symbol, new_qty, new_avg, int(time.time()) if not row else row["opened"], sleeve, thesis))
        conn.execute("INSERT INTO fund_trades (ts, symbol, side, qty, price, fees, realized, note) VALUES (?,?,?,?,?,?,?,?)", (int(time.time()), symbol, side, qty, price, fees, realized, note))
    set_cash(new_cash)
    return {"symbol": symbol, "side": side, "qty": qty, "price": price, "realized": round(realized, 2), "position": new_qty, "avg_cost": round(new_avg, 4), "cash": cash()}


def snapshot(nav: float, invested: float) -> None:
    """One NAV point per hour at most."""
    ensure()
    ts = int(time.time() // 3600 * 3600)
    with db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO fund_nav (ts, nav, cash, invested) VALUES (?,?,?,?)", (ts, nav, cash(), invested))


def nav_history(days: int = 365) -> list[dict[str, Any]]:
    ensure()
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT ts, nav, cash, invested FROM fund_nav WHERE ts > ? ORDER BY ts", (int(time.time() - days * 86400),)).fetchall()]


def realized_total() -> float:
    ensure()
    with db.get_conn() as conn:
        return float(conn.execute("SELECT COALESCE(SUM(realized), 0) FROM fund_trades").fetchone()[0])
