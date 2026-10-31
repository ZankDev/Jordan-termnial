"""Combo evaluations: 2-3 strategies trading ONE $50k Apex evaluation together.

The long-history combo test (portfolio.analyse_long) showed the pass rate of a single low-frequency strategy is small
(~11% in 60 days for the best one) while two uncorrelated profitable strategies sharing one account reach ~40%: their
losing days rarely line up, so the shared equity curve is smoother against the trailing drawdown.

How it runs, without a risky rewrite of the live engine: every member is its own paper account (strategy, symbol,
parameters, risk per trade) traded by the normal live engine. The combo owns the EVALUATION: its equity is the start
balance plus the sum of every member's closed-trade P&L (plus open-position P&L when a price is known), checked against
the Apex rules each tick (profit target, trailing drawdown that locks at the start balance). When the combo passes or
breaches, every member is stopped at once — exactly what one account holding all positions would do.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from app import db
from app.accounts import apex_rules

logger = logging.getLogger(__name__)
SCHEMA = """CREATE TABLE IF NOT EXISTS combo_evals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT NOT NULL, members_json TEXT NOT NULL,
    starting_balance REAL NOT NULL, profit_target REAL NOT NULL, max_drawdown REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress', peak_equity REAL, drawdown_floor REAL, equity REAL,
    fail_reason TEXT, ended_at TEXT, research_json TEXT)"""


_schema_ok = False


def ensure_schema() -> None:
    """Once per process: DDL goes through the write lock, and the pages read combos every few seconds."""
    global _schema_ok
    if _schema_ok:
        return
    with db.get_conn() as conn:
        conn.execute(SCHEMA)
    _schema_ok = True


def create(name: str, members: list[dict[str, Any]], research: dict[str, Any] | None = None) -> dict[str, Any]:
    """members: [{"strategy", "symbol", "params", "risk"}]. Creates one live paper account per member."""
    from app.accounts import ledger

    ensure_schema()
    ids = []
    for m in members:
        acct = db.create_account(f"combo·{name}·{m['strategy']} {m['symbol']}", 50_000.0, "standard", sizing_mode="risk_micro",
                                 risk_per_trade=float(m["risk"]))
        db.assign_strategy(acct["id"], m["strategy"], m["symbol"], m["params"])
        try:
            ledger.go_live(acct["id"])
        except Exception:  # noqa: BLE001 - no fresh data right now (weekend); the member can be put live later
            logger.exception("combo member go_live failed")
        ids.append(acct["id"])
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO combo_evals (name, created_at, members_json, starting_balance, profit_target, max_drawdown, peak_equity, drawdown_floor, equity, research_json) "
            "VALUES (?, datetime('now'), ?, 50000, 3000, 2000, 50000, 48000, 50000, ?)",
            (name, json.dumps(ids), json.dumps(research or {})))
        cid = cur.lastrowid
    return get(cid)


def _members(ids: list[int]) -> list[dict[str, Any]]:
    out = []
    for i in ids:
        a = db.get_account(i)
        if a:
            out.append(a)
    return out


def _combo_equity(c: dict[str, Any], accts: list[dict[str, Any]]) -> tuple[float, float]:
    """(closed-trade equity, equity including open positions where the member's live engine has a price)."""
    start = c["starting_balance"]
    closed = start + sum(a["balance"] - a["starting_balance"] for a in accts)
    open_pnl = 0.0
    try:
        from app.accounts import pnl
        from app.data import yfinance_source

        prices: dict[str, float] = {}
        for a in accts:
            pos = a.get("open_position")
            if not pos:
                continue
            sym = a["symbol"]
            if sym not in prices:
                px = yfinance_source.last_close(sym, max_age_seconds=90)  # cached, never waits on Yahoo
                if not px:
                    continue
                prices[sym] = px[0]
            u = pnl.unrealized(sym, pos["side"], pos["entry_price"], prices[sym], pos.get("qty", 1), pos.get("contract"))
            open_pnl += u or 0.0
    except Exception:  # noqa: BLE001 - open P&L is best-effort; closed-trade equity still decides
        pass
    return closed, closed + open_pnl


def tick() -> list[dict[str, Any]]:
    ensure_schema()
    with db.read_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM combo_evals WHERE status = 'in_progress'").fetchall()]
    events = []
    for c in rows:
        ids = json.loads(c["members_json"])
        accts = _members(ids)
        if not accts:
            continue
        closed, marked = _combo_equity(c, accts)
        acct_view = {"starting_balance": c["starting_balance"], "max_drawdown": c["max_drawdown"], "profit_target": c["profit_target"],
                     "peak_equity": c["peak_equity"]}
        v = apex_rules.evaluate_tick(acct_view, marked)
        status, reason = "in_progress", None
        if v["breached"]:
            status, reason = "failed", f"Drawdown floor ${v['drawdown_floor']:,.2f} breached at combined equity ${marked:,.2f}"
        elif closed >= c["starting_balance"] + c["profit_target"]:  # pass on realised profit only
            status = "passed"
        with db.get_conn() as conn:
            conn.execute("UPDATE combo_evals SET peak_equity = ?, drawdown_floor = ?, equity = ?, status = ?, fail_reason = ?, "
                         "ended_at = CASE WHEN ? != 'in_progress' THEN datetime('now') ELSE NULL END WHERE id = ?",
                         (v["peak_equity"], v["drawdown_floor"], round(marked, 2), status, reason, status, c["id"]))
        if status != "in_progress":
            from app.accounts import ledger

            for a in accts:
                try:
                    if a["status"] == "live":
                        ledger.stop_live(a["id"])  # flattens any open position at the last price
                except Exception:  # noqa: BLE001
                    logger.exception("combo stop member failed")
            events.append({"id": c["id"], "name": c["name"], "status": status, "equity": round(marked, 2)})
            try:
                from app.notify import telegram_bot

                telegram_bot.send_text(f"{'🏆 COMBO PASSED' if status == 'passed' else '❌ COMBO FAILED'}: {c['name']}\n"
                                       f"combined equity ${marked:,.0f}" + (f"\n{reason}" if reason else ""))
            except Exception:  # noqa: BLE001
                pass
    return events


def get(cid: int) -> dict[str, Any] | None:
    ensure_schema()
    with db.read_conn() as conn:
        r = conn.execute("SELECT * FROM combo_evals WHERE id = ?", (cid,)).fetchone()
    return _view(dict(r)) if r else None


def _view(c: dict[str, Any]) -> dict[str, Any]:
    ids = json.loads(c["members_json"])
    accts = _members(ids)
    with db.read_conn() as conn:
        trades = conn.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(pnl), 0) AS p FROM account_trades WHERE account_id IN ({','.join('?' * len(ids)) or 'NULL'})",
                              ids).fetchone() if ids else {"n": 0, "p": 0}
    profit = (c["equity"] or c["starting_balance"]) - c["starting_balance"]
    return {**{k: c[k] for k in ("id", "name", "created_at", "starting_balance", "profit_target", "max_drawdown", "status", "peak_equity",
                                   "drawdown_floor", "equity", "fail_reason", "ended_at")},
            "profit": round(profit, 2), "progress_pct": round(max(0.0, min(100.0, 100 * profit / c["profit_target"])), 1),
            "buffer": round((c["equity"] or c["starting_balance"]) - (c["drawdown_floor"] or c["starting_balance"] - c["max_drawdown"]), 2),
            "trades": trades["n"], "research": json.loads(c.get("research_json") or "{}"),
            "members": [{"id": a["id"], "strategy": a["strategy"], "symbol": a["symbol"], "risk": a.get("risk_per_trade"), "status": a["status"],
                         "pnl": round(a["balance"] - a["starting_balance"], 2), "open": bool(a.get("open_position"))} for a in accts]}


def list_all() -> list[dict[str, Any]]:
    ensure_schema()
    with db.read_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM combo_evals ORDER BY id DESC").fetchall()]
    return [_view(r) for r in rows]


def member_ids() -> set[int]:
    ensure_schema()
    with db.read_conn() as conn:
        return {i for r in conn.execute("SELECT members_json FROM combo_evals").fetchall() for i in json.loads(r["members_json"])}
