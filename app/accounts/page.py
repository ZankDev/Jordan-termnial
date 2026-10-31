"""Data for the NOSTRO ACCOUNTS page: every account with its label, today's profit/trades, eval progress,
withdrawable estimate, a small equity sparkline and headline stats — one query per table, not one per account."""
from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from app import db

NY = ZoneInfo("America/New_York")
SPARK_POINTS = 40


def _day_of(iso: str | None) -> dt.date | None:
    try:
        return dt.datetime.fromisoformat(iso).astimezone(NY).date() if iso else None
    except ValueError:
        return None


def _label(a: dict[str, Any]) -> dict[str, str]:
    """(code, text) the card shows as its badge."""
    st = a.get("eval_status")
    if a["account_type"] == "apex_eval":
        if st == "passed":
            return {"code": "passed", "text": "PASSED"}
        if st == "failed":
            return {"code": "failed", "text": "FAILED"}
        return {"code": "eval", "text": "IN EVALUATION" if st == "in_progress" else "EVAL · NOT STARTED"}
    return {"code": "live" if a["status"] == "live" else "idle", "text": "LIVE" if a["status"] == "live" else "IDLE"}


def _stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "avg_trade": None, "best": None, "worst": None}
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    loss = [t["pnl"] for t in trades if t["pnl"] < 0]
    gross_l = -sum(loss)
    return {
        "trades": n, "win_rate": round(100 * len(wins) / n, 1),
        "profit_factor": round(sum(wins) / gross_l, 2) if gross_l else None,
        "avg_trade": round(sum(t["pnl"] for t in trades) / n, 2),
        "best": round(max(t["pnl"] for t in trades), 2), "worst": round(min(t["pnl"] for t in trades), 2),
    }


def _last_prices(symbols: set[str]) -> dict[str, tuple[float, str]]:
    """Latest 5m close per symbol (the same cache the live engine refreshes every tick)."""
    from app.data import yfinance_source

    out: dict[str, tuple[float, str]] = {}
    for s in symbols:
        try:
            px = yfinance_source.last_close(s, max_age_seconds=90)  # cached; refreshes in the background, never blocks
            if px:
                out[s] = px
        except Exception:  # noqa: BLE001 - no price: the position just shows without open P&L
            pass
    return out


def _open_pnl(symbol: str, pos: dict[str, Any], price: float | None) -> float | None:
    """Net open P&L of a position at `price`: points × $/point − the round-turn commission it will pay."""
    if price is None or not pos or pos.get("entry_price") is None:
        return None
    from app.accounts import pnl
    from app.accounts.ledger import DEFAULT_COMMISSION_PER_TRADE

    direction = 1 if pos["side"] == "long" else -1
    gross = (price - float(pos["entry_price"])) * direction * pnl.position_point_value(symbol, pos)
    return round(gross - float(pos.get("commission", DEFAULT_COMMISSION_PER_TRADE)), 2)


def build(include_archived: bool = False) -> dict[str, Any]:
    accounts = db.list_accounts(include_archived=include_archived)
    ids = [a["id"] for a in accounts]
    by_acct: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    if ids:
        with db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT account_id, exit_time, pnl, equity_after, side, symbol, strategy, exit_reason, entry_price, exit_price, qty, source "
                f"FROM account_trades WHERE account_id IN ({','.join('?' * len(ids))}) ORDER BY id",
                ids,
            ).fetchall()
        for r in rows:
            by_acct[r["account_id"]].append(dict(r))

    today = dt.datetime.now(NY).date()
    try:
        from app.accounts import combo_eval

        combo_members = combo_eval.member_ids()
    except Exception:  # noqa: BLE001
        combo_members = set()
    prices = _last_prices({a["symbol"] for a in accounts if a.get("open_position") and a.get("symbol")})
    cards: list[dict[str, Any]] = []
    for a in accounts:
        trades = by_acct.get(a["id"], [])
        todays = [t for t in trades if _day_of(t["exit_time"]) == today]
        profit = round(a["balance"] - a["starting_balance"], 2)
        label = _label(a)
        target, floor = a.get("profit_target"), a.get("drawdown_floor")
        progress = None
        if a["account_type"] == "apex_eval" and target:
            progress = {
                "target": target, "profit": profit, "pct": round(max(0.0, min(100.0, 100 * profit / target)), 1),
                "drawdown_floor": floor, "buffer": round(a["balance"] - floor, 2) if floor is not None else None,
                "max_drawdown": a.get("max_drawdown"), "daily_loss_limit": a.get("daily_loss_limit"),
                "fail_reason": a.get("eval_fail_reason"),
            }
        curve = [a["starting_balance"]] + [t["equity_after"] for t in trades if t["equity_after"] is not None]
        step = max(1, len(curve) // SPARK_POINTS)
        spark = curve[::step][-SPARK_POINTS:] + ([curve[-1]] if (len(curve) - 1) % step else [])
        pos = a.get("open_position")
        px = prices.get(a.get("symbol")) if pos else None
        open_pnl = _open_pnl(a["symbol"], pos, px[0] if px else None) if pos else None
        equity = round(a["balance"] + (open_pnl or 0.0), 2)
        net = round(equity - a["starting_balance"], 2)
        today_realized = round(sum(t["pnl"] for t in todays), 2)
        cards.append({
            "id": a["id"], "name": a["name"], "symbol": a.get("symbol"), "strategy": a.get("strategy"),
            "type": a["account_type"], "status": a["status"], "label": label, "is_system": bool(a.get("is_system")),
            "real_linked": bool(a.get("traderspost_linked")), "tradovate_account_name": a.get("tradovate_account_name"),
            "payout_target": a.get("payout_target"), "awaiting_payout": bool(a.get("awaiting_payout")),
            "archived": bool(a.get("is_archived")), "starting_balance": a["starting_balance"], "balance": round(a["balance"], 2),
            "profit": profit, "profit_pct": round(100 * profit / a["starting_balance"], 2) if a["starting_balance"] else 0.0,
            # net = realized (balance) + the open position marked at the last price, after commission
            "equity": equity, "open_pnl": open_pnl, "net_pnl": net,
            "net_pct": round(100 * net / a["starting_balance"], 2) if a["starting_balance"] else 0.0,
            "today_pnl": today_realized, "today_net": round(today_realized + (open_pnl or 0.0), 2), "today_trades": len(todays),
            "today_wins": sum(1 for t in todays if t["pnl"] > 0),
            "stats": _stats(trades), "progress": progress, "spark": [round(v, 2) for v in spark] + ([equity] if open_pnl is not None else []),
            "open_position": {"side": pos["side"], "entry_price": pos["entry_price"], "qty": pos.get("qty", 1), "stop": pos.get("stop_price"),
                              "target": pos.get("target_price"), "entry_time": pos.get("entry_time"), "last": px[0] if px else None,
                              "last_time": px[1] if px else None, "pnl": open_pnl} if pos else None,
            # what could be paid out if this were a funded account: the profit above the starting balance.
            # Only offered for a PASSED evaluation, and flagged as an estimate — the firm's payout rules decide the real figure.
            "withdrawable": max(0.0, profit) if label["code"] == "passed" else None,
            "last_trade": trades[-1]["exit_time"] if trades else None,
            "sim_trades": sum(1 for t in trades if t.get("source") == "sim"),
            "combo_member": a["id"] in combo_members,
        })

    order = {"passed": 0, "eval": 1, "live": 2, "idle": 3, "failed": 4}
    cards.sort(key=lambda c: (order.get(c["label"]["code"], 5), -c["today_pnl"], -c["profit"]))
    passed = [c for c in cards if c["label"]["code"] == "passed"]
    return {
        "accounts": cards,
        "totals": {
            "accounts": len(cards), "live": sum(1 for c in cards if c["status"] == "live"), "passed": len(passed),
            "in_eval": sum(1 for c in cards if c["label"]["code"] == "eval"), "failed": sum(1 for c in cards if c["label"]["code"] == "failed"),
            "balance": round(sum(c["balance"] for c in cards), 2), "equity": round(sum(c["equity"] for c in cards), 2),
            "net_pnl": round(sum(c["net_pnl"] for c in cards), 2), "realized": round(sum(c["profit"] for c in cards), 2),
            "open_pnl": round(sum(c["open_pnl"] or 0 for c in cards), 2), "open_positions": sum(1 for c in cards if c["open_position"]),
            "today_net": round(sum(c["today_net"] for c in cards), 2),
            "today_pnl": round(sum(c["today_pnl"] for c in cards), 2),
            "today_trades": sum(c["today_trades"] for c in cards), "withdrawable": round(sum(c["withdrawable"] or 0 for c in passed), 2),
        },
        "date": today.isoformat(),
    }
