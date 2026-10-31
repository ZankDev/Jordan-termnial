"""ALRT: price, % move, volume spike, news keyword and economic-release alerts, delivered to Telegram (and shown in the app).

The evaluator runs once a minute (see alerts_loop, started from app.main). Each alert fires once by default; repeating
alerts respect a cooldown. Every firing is written to alert_log so the app can toast it and show the history.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app import db
from app.notify import telegram_bot

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- price | pct_move | volume | news | econ
    symbol TEXT,
    op TEXT,                       -- above | below | up | down | any
    value REAL,
    extra TEXT,                    -- json: keyword, minutes_before, ...
    note TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    once INTEGER NOT NULL DEFAULT 1,
    cooldown INTEGER NOT NULL DEFAULT 3600,
    created_at REAL NOT NULL,
    last_fired REAL,
    fired_count INTEGER NOT NULL DEFAULT 0,
    state TEXT                     -- json scratch (seen headlines, fired event ids)
);
CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER,
    ts REAL NOT NULL,
    message TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
"""


def ensure() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


def _quote(symbol: str) -> dict[str, Any] | None:
    from app.api.capital_routes import _chart, cached

    try:
        return cached(f"aq:{symbol}", 40, lambda: _chart(symbol))
    except Exception:  # noqa: BLE001
        return None


def _fmt(n: float) -> str:
    return f"{n:,.2f}" if abs(n) >= 1 else f"{n:.4f}"


def _check(a: dict[str, Any], now: float) -> str | None:
    """Return the message to send if this alert should fire now."""
    kind, sym = a["kind"], (a["symbol"] or "").upper()
    extra = json.loads(a["extra"] or "{}")
    state = json.loads(a["state"] or "{}")
    if kind in ("price", "pct_move", "volume"):
        q = _quote(sym)
        if not q:
            return None
        price, prev = q["price"], q["prev"]
        pct = (price / prev - 1) * 100 if prev else 0.0
        if kind == "price":
            if (a["op"] == "above" and price >= a["value"]) or (a["op"] == "below" and price <= a["value"]):
                return f"ALERT {sym}: price {_fmt(price)} is {a['op']} {_fmt(a['value'])} ({pct:+.2f}% today)"
        elif kind == "pct_move":
            if (a["op"] in ("any", None) and abs(pct) >= a["value"]) or (a["op"] == "up" and pct >= a["value"]) or (a["op"] == "down" and pct <= -a["value"]):
                return f"ALERT {sym}: moved {pct:+.2f}% today (trigger {a['op'] or 'any'} {a['value']:g}%) at {_fmt(price)}"
        elif kind == "volume":
            avg = q.get("vol_avg") or 0
            if avg and q.get("vol", 0) / avg >= a["value"]:
                return f"ALERT {sym}: volume {q['vol'] / avg:.1f}x its 20-day average (trigger {a['value']:g}x), price {_fmt(price)} ({pct:+.2f}%)"
        return None
    if kind == "news":
        from app.api import invest_routes as inv

        kw = (extra.get("keyword") or "").lower()
        try:
            items = inv._news(extra.get("query") or sym, bool(sym) and not extra.get("query"))
        except Exception:  # noqa: BLE001
            return None
        seen = set(state.get("seen", []))
        hits = [i for i in items if kw in (i["title"] or "").lower() and i["link"] not in seen]
        first_run = "seen" not in state
        state["seen"] = list({*seen, *[i["link"] for i in items]})[-400:]
        a["state"] = json.dumps(state)
        if first_run or not hits:
            return None  # the first pass only records what already exists
        return f"NEWS ALERT ({extra.get('query') or sym} · '{kw}'): {hits[0]['title']} — {hits[0]['source']}\n{hits[0]['link']}"
    if kind == "econ":
        kw = (extra.get("keyword") or "").lower()
        mins = float(extra.get("minutes_before") or 30)
        with db.get_conn() as conn:
            rows = conn.execute("SELECT * FROM econ_events WHERE ts BETWEEN ? AND ? ORDER BY ts", (int(now), int(now + mins * 60))).fetchall()
        fired = set(state.get("fired", []))
        for r in rows:
            if kw in r["title"].lower() and (not a["symbol"] or r["country"] == a["symbol"].upper()) and r["ts"] not in fired:
                fired.add(r["ts"])
                state["fired"] = sorted(fired)[-50:]
                a["state"] = json.dumps(state)
                mins_left = max(0, int((r["ts"] - now) / 60))
                return (f"ECONOMIC RELEASE in {mins_left} min: {r['country']} {r['title']} ({r['impact']} impact)"
                        f"\nforecast {r['forecast'] or '-'} · previous {r['previous'] or '-'}")
        return None
    return None


def evaluate() -> int:
    ensure()
    now = time.time()
    fired = 0
    with db.get_conn() as conn:
        alerts = [dict(r) for r in conn.execute("SELECT * FROM alerts WHERE active = 1").fetchall()]
    for a in alerts:
        try:
            if a["last_fired"] and not a["once"] and now - a["last_fired"] < a["cooldown"]:
                continue
            msg = _check(a, now)
            with db.get_conn() as conn:
                conn.execute("UPDATE alerts SET state = ? WHERE id = ?", (a.get("state"), a["id"]))
            if not msg:
                continue
            if a.get("note"):
                msg += f"\nNote: {a['note']}"
            telegram_bot.send_text(msg)
            with db.get_conn() as conn:
                conn.execute("INSERT INTO alert_log (alert_id, ts, message, delivered) VALUES (?, ?, ?, 1)", (a["id"], now, msg))
                conn.execute("UPDATE alerts SET last_fired = ?, fired_count = fired_count + 1, active = CASE WHEN once = 1 THEN 0 ELSE active END WHERE id = ?", (now, a["id"]))
            fired += 1
        except Exception:  # noqa: BLE001 - one bad alert must never stop the others
            logger.exception("alert %s failed", a.get("id"))
    return fired


async def alerts_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(evaluate)
        except Exception:  # noqa: BLE001
            logger.exception("alerts loop")
        await asyncio.sleep(60)
