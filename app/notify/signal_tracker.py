"""Tracks manual TEST signals sent to the broker, which the strategy engine knows nothing about.

A test signal (the TEST LONG / TEST SHORT button) sends a real entry with a stop and a target, but no trade exists in the
terminal, so nothing showed it open, closed or what it made. This keeps a row per test signal and follows the market: every minute the
1-minute bars since the signal are checked, and the row closes when the target or the stop trades (if one bar touches both, the stop is
assumed to have come first). It estimates the result from market prices; the broker's own fill is the final word.

Rows come from the test-signal endpoint (open_test) and, once, from any earlier entry already in traderspost_log that has a stop and a
target (backfill_from_log). Closing a row sends one Telegram message. tick() is called from ops_alerts.monitor_loop and never raises.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
TICKER = {"ES": "ES=F", "NQ": "NQ=F"}
SCHEMA = ("CREATE TABLE IF NOT EXISTS tracked_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, symbol TEXT, contract TEXT, side TEXT, qty REAL, "
          "entry REAL, stop REAL, target REAL, status TEXT NOT NULL DEFAULT 'open', exit_price REAL, exit_ts TEXT, pnl REAL, source TEXT, last_price REAL, last_at TEXT, log_id INTEGER)")


def _ensure(conn) -> None:
    conn.execute(SCHEMA)


def open_test(symbol: str, contract: str, side: str, qty: float, entry: float, stop: float, target: float) -> None:
    with db.get_conn() as conn:
        _ensure(conn)
        conn.execute("INSERT INTO tracked_signals (ts, symbol, contract, side, qty, entry, stop, target, source) VALUES (datetime('now'),?,?,?,?,?,?,?,'test')",
                     (symbol, contract, side, qty, entry, stop, target))


def backfill_from_log() -> int:
    """One-time: an entry already sent (traderspost_log) with a stop and a target, not yet tracked, becomes a tracked test signal."""
    from app.backtest.sizing import MICRO_ROOT

    root_to_symbol = {v: k for k, v in MICRO_ROOT.items()}
    n = 0
    with db.get_conn() as conn:
        _ensure(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS traderspost_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')), action TEXT, ticker TEXT, price REAL, "
                     "qty REAL, stop REAL, target REAL, http_status INTEGER, ok INTEGER, detail TEXT)")
        if conn.execute("SELECT 1 FROM tracked_signals LIMIT 1").fetchone():
            return 0
        for r in conn.execute("SELECT * FROM traderspost_log WHERE ok = 1 AND action IN ('buy','sell') AND stop IS NOT NULL AND target IS NOT NULL ORDER BY id").fetchall():
            ticker = str(r["ticker"] or "").upper()
            sym, contract = (root_to_symbol[ticker], "micro") if ticker in root_to_symbol else (ticker, "mini")
            if sym not in TICKER:
                continue
            conn.execute("INSERT INTO tracked_signals (ts, symbol, contract, side, qty, entry, stop, target, source, log_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (r["ts"], sym, contract, "long" if r["action"] == "buy" else "short", r["qty"] or 1, r["price"], r["stop"], r["target"], "log", r["id"]))
            n += 1
    return n


def _bars(symbol: str) -> list[tuple[int, float, float, float]]:
    """(epoch, high, low, close) of the 1-minute bars of the last day or two, futures included overnight."""
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(TICKER[symbol])}",
                     params={"range": "5d", "interval": "1m", "includePrePost": "true"},
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}, timeout=12)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    return [(t, h, l, c) for t, h, l, c in zip(res.get("timestamp") or [], q["high"], q["low"], q["close"]) if None not in (h, l, c)]


def _epoch(sql_ts: str) -> float:
    return dt.datetime.strptime(sql_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()


def tick() -> list[dict[str, Any]]:
    """Update every open tracked signal; returns the ones that closed this pass. Never raises."""
    from app.backtest.sizing import point_value

    closed: list[dict[str, Any]] = []
    try:
        backfill_from_log()
        with db.get_conn() as conn:
            _ensure(conn)
            rows = [dict(r) for r in conn.execute("SELECT * FROM tracked_signals WHERE status = 'open'").fetchall()]
        cache: dict[str, list] = {}
        for s in rows:
            try:
                bars = cache.get(s["symbol"]) or cache.setdefault(s["symbol"], _bars(s["symbol"]))
            except Exception:  # noqa: BLE001 - no price feed right now: try again next minute
                continue
            t0 = _epoch(s["ts"])
            after = [b for b in bars if b[0] >= t0]
            if not after:
                continue
            long_ = s["side"] == "long"
            exit_price, status, exit_t = None, None, None
            for t, h, l, c in after:
                hit_stop = l <= s["stop"] if long_ else h >= s["stop"]
                hit_tp = h >= s["target"] if long_ else l <= s["target"]
                if hit_stop:
                    exit_price, status, exit_t = s["stop"], "stop", t
                    break
                if hit_tp:
                    exit_price, status, exit_t = s["target"], "target", t
                    break
            last_t, _h, _l, last_c = after[-1]
            pv = point_value(s["symbol"], s["contract"] or "micro") * (s["qty"] or 1)
            with db.get_conn() as conn:
                if status:
                    pnl = round(((exit_price - s["entry"]) if long_ else (s["entry"] - exit_price)) * pv, 2)
                    conn.execute("UPDATE tracked_signals SET status=?, exit_price=?, exit_ts=?, pnl=?, last_price=?, last_at=datetime('now') WHERE id=?",
                                 (status, exit_price, dt.datetime.fromtimestamp(exit_t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), pnl, last_c, s["id"]))
                    closed.append({**s, "status": status, "exit_price": exit_price, "pnl": pnl})
                else:
                    conn.execute("UPDATE tracked_signals SET last_price=?, last_at=datetime('now') WHERE id=?", (last_c, s["id"]))
        for c in closed:
            try:
                from app.notify import ops_alerts

                ops_alerts.alert(f"test_closed_{c['id']}", f"TEST SIGNAL closed: {c['side'].upper()} {c['qty']:g} {c['symbol']} from {c['entry']} hit its "
                                 f"{'TARGET' if c['status'] == 'target' else 'STOP'} at {c['exit_price']} = {c['pnl']:+,.2f} USD (estimated from market prices; the broker's fill is final).", cooldown=0)
            except Exception:  # noqa: BLE001
                logger.exception("signal_tracker: telegram failed")
    except Exception:  # noqa: BLE001
        logger.exception("signal_tracker.tick failed")
    return closed


def close_open(symbol: str, price: float) -> int:
    """The user closed the real position by hand: every still-open tracked test signal on this symbol is closed at `price`."""
    from app.backtest.sizing import point_value

    n = 0
    with db.get_conn() as conn:
        _ensure(conn)
        for s in [dict(r) for r in conn.execute("SELECT * FROM tracked_signals WHERE status = 'open' AND symbol = ?", (symbol,)).fetchall()]:
            pv = point_value(s["symbol"], s["contract"] or "micro") * (s["qty"] or 1)
            pnl = round(((price - s["entry"]) if s["side"] == "long" else (s["entry"] - price)) * pv, 2)
            conn.execute("UPDATE tracked_signals SET status='manual', exit_price=?, exit_ts=datetime('now'), pnl=?, last_price=?, last_at=datetime('now') WHERE id=?", (price, pnl, price, s["id"]))
            n += 1
    return n


def list_tracked(limit: int = 8) -> list[dict[str, Any]]:
    """Newest first, each with an unrealised result for the ones still open."""
    from app.backtest.sizing import point_value

    try:
        with db.get_conn() as conn:
            _ensure(conn)
            rows = [dict(r) for r in conn.execute("SELECT * FROM tracked_signals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    except Exception:  # noqa: BLE001
        return []
    for s in rows:
        if s["status"] == "open" and s.get("last_price") is not None:
            pv = point_value(s["symbol"], s["contract"] or "micro") * (s["qty"] or 1)
            s["unrealised"] = round(((s["last_price"] - s["entry"]) if s["side"] == "long" else (s["entry"] - s["last_price"])) * pv, 2)
    return rows
