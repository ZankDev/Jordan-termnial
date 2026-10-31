"""CASH FLOW — recurring monthly items that move the user's main balance, the monthly credit-card question on Telegram,
and this month's stock earnings.

Recurring items (cashflow_recurring): e.g. salary +2,900 ₪ on the 1st, American Express billing -500 ₪ on the 1st. On/after
its day each month an item is posted ONCE: a budget transaction is written and the MAIN BALANCE asset (a manual 'bank'
asset in WEALTH › NET WORTH, created as "Main balance" if missing) is adjusted by the amount.

Credit card: on the 1st of each month at 09:00 (Israel) the bot asks on Telegram "how much did you spend on credit cards
last month?". The reply (a number, e.g. "3450" or "3,450 ₪") is booked as an expense for that month and subtracted from
the main balance. Until answered, the question is repeated once a day (max 3 times). Replies are only accepted from the
configured TELEGRAM_CHAT_ID.

Stocks this month: month-to-date change in value of the portfolio holdings (price now vs the last close of the previous
month, or vs cost for positions opened this month).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from app import db
from app.wealth import core

SCHEMA = """CREATE TABLE IF NOT EXISTS cashflow_recurring (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'ILS', day INTEGER NOT NULL DEFAULT 1, category TEXT, active INTEGER NOT NULL DEFAULT 1,
    last_posted TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))"""
MAIN_BALANCE = "Main balance"


def ensure() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)


def seed_defaults() -> None:
    """The user's items (2026-09-28): salary +2,900 ₪ and American Express -500 ₪, both on the 1st."""
    ensure()
    if db.meta_get("cashflow_seeded"):
        return
    month = dt.datetime.now(core.IL).strftime("%Y-%m")  # this month is already in the real balance: first booking next month
    with db.get_conn() as conn:
        conn.execute("INSERT INTO cashflow_recurring (name, amount, day, category, last_posted) VALUES ('Salary', 2900, 1, 'Income', ?)", (month,))
        conn.execute("INSERT INTO cashflow_recurring (name, amount, day, category, last_posted) VALUES ('American Express billing', -500, 1, 'Credit card', ?)", (month,))
    db.meta_set("cashflow_seeded", "1")


def main_balance() -> dict[str, Any]:
    core.ensure()
    aid = db.meta_get("main_balance_asset")
    if aid:
        with db.get_conn() as conn:
            r = conn.execute("SELECT * FROM wealth_assets WHERE id=?", (int(aid),)).fetchone()
        if r:
            return dict(r)
    a = core.upsert_asset({"name": MAIN_BALANCE, "category": "bank", "currency": "ILS", "value": 0,
                           "notes": "moved automatically by salary, card bills and the monthly credit-card answer"})
    db.meta_set("main_balance_asset", str(a["id"]))
    return a


def set_balance(value: float) -> dict[str, Any]:
    a = main_balance()
    with db.get_conn() as conn:
        conn.execute("UPDATE wealth_assets SET value=?, updated_at=datetime('now') WHERE id=?", (float(value), a["id"]))
    return main_balance()


def _move(amount: float, day: str, desc: str, category: str) -> None:
    """Book one cash movement: a budget transaction + the main balance adjusted by the same amount (ILS)."""
    from app.wealth import budget

    a = main_balance()
    with db.get_conn() as conn:
        conn.execute("UPDATE wealth_assets SET value = value + ?, updated_at=datetime('now') WHERE id=?", (amount, a["id"]))
    try:
        budget.add_tx(day, desc, amount, category, "auto")
    except Exception:  # noqa: BLE001 - the balance move stands even if the ledger row is a duplicate
        pass


def list_recurring() -> list[dict[str, Any]]:
    ensure()
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM cashflow_recurring ORDER BY day, id").fetchall()]


def upsert_recurring(r: dict[str, Any]) -> dict[str, Any]:
    ensure()
    with db.get_conn() as conn:
        if r.get("id"):
            conn.execute("UPDATE cashflow_recurring SET name=?, amount=?, day=?, category=?, active=? WHERE id=?",
                         (r["name"], float(r["amount"]), int(r.get("day") or 1), r.get("category"), int(r.get("active", 1)), int(r["id"])))
            rid = int(r["id"])
        else:
            today = dt.datetime.now(core.IL).date()
            day = int(r.get("day") or 1)
            # a new item whose day already passed this month starts next month (this month is in the real balance already)
            last = today.strftime("%Y-%m") if today.day >= day else None
            rid = conn.execute("INSERT INTO cashflow_recurring (name, amount, day, category, active, last_posted) VALUES (?,?,?,?,1,?)",
                               (r["name"], float(r["amount"]), day, r.get("category"), last)).lastrowid
        return dict(conn.execute("SELECT * FROM cashflow_recurring WHERE id=?", (rid,)).fetchone())


def delete_recurring(rid: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM cashflow_recurring WHERE id=?", (rid,))


def post_due(today: dt.date | None = None) -> list[str]:
    """Post every active item whose day has come this month and that was not posted this month yet."""
    ensure()
    today = today or dt.datetime.now(core.IL).date()
    month = today.strftime("%Y-%m")
    done = []
    for r in list_recurring():
        if not r["active"] or r.get("last_posted") == month or today.day < min(28, int(r["day"] or 1)):
            continue
        day = today.replace(day=min(28, int(r["day"] or 1))).isoformat()
        _move(float(r["amount"]), day, r["name"], r.get("category") or ("Income" if r["amount"] > 0 else "Other"))
        with db.get_conn() as conn:
            conn.execute("UPDATE cashflow_recurring SET last_posted=? WHERE id=?", (month, r["id"]))
        done.append(f"{r['name']} {r['amount']:+,.0f} ₪")
    return done


# ------------------------------------------------------------------ monthly credit-card question (Telegram)
def _prev_month(today: dt.date) -> str:
    return (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")


def ask_cc_if_due(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(core.IL)
    if now.day != 1 and not db.meta_get("cc_pending"):
        return False
    pend = json.loads(db.meta_get("cc_pending") or "null") or {}
    month = _prev_month(now.date()) if now.day == 1 else pend.get("month")
    if not month:
        return False
    if db.meta_get(f"cc_answered_{month}"):
        return False
    if now.hour < 9:
        return False
    asked = pend.get("asked", []) if pend.get("month") == month else []
    today = now.date().isoformat()
    if today in asked or len(asked) >= 3:
        return False
    from app.notify import telegram_bot

    telegram_bot.send_text(f"💳 How much did you spend on credit cards in {month}?\n"
                           f"Reply with just the number (e.g. 3450). I'll book it as an expense and take it off your main balance.")
    db.meta_set("cc_pending", json.dumps({"month": month, "asked": asked + [today]}))
    return True


def handle_telegram_text(text: str) -> str | None:
    """Called by the Telegram listener for every text message from the owner. Returns a reply, or None if not ours."""
    t = (text or "").strip()
    pend = json.loads(db.meta_get("cc_pending") or "null") or {}
    low = t.lower()
    if low in ("/balance", "balance", "יתרה"):
        a = main_balance()
        return f"🏦 Main balance: ₪{a['value']:,.0f}"
    if not pend.get("month"):
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", t.replace("₪", ""))
    if not m:
        return None
    amount = abs(float(m.group(0).replace(",", "")))
    if amount <= 0 or amount > 1_000_000:
        return "That doesn't look like an amount — reply with just the number, e.g. 3450."
    month = pend["month"]
    day = f"{month}-28"
    _move(-amount, day, f"Credit card spend {month}", "Credit card")
    db.meta_set(f"cc_answered_{month}", str(amount))
    db.meta_set("cc_pending", "")
    a = main_balance()
    return f"✅ Booked ₪{amount:,.0f} credit-card spend for {month}. Main balance now ₪{a['value']:,.0f}."


def cc_history() -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM meta WHERE key LIKE 'cc_answered_%' ORDER BY key DESC LIMIT 24").fetchall()
    return [{"month": r["key"].replace("cc_answered_", ""), "amount": float(r["value"])} for r in rows]


# ------------------------------------------------------------------ stocks this month
def stocks_mtd() -> dict[str, Any]:
    from app.api import invest_routes as inv
    from app.api.capital_routes import _yahoo_symbol

    today = dt.datetime.now(core.IL).date()
    m0 = today.replace(day=1)
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM capital_holdings").fetchall()]
    pf = core._portfolio()
    now_px = {h["symbol"]: h.get("price") for h in pf.get("holdings", [])}
    total, items = 0.0, []
    for r in rows:
        sym = r["symbol"].upper()
        px = now_px.get(sym)
        if not px:
            continue
        added = dt.date.fromisoformat(str(r["added_at"])[:10])
        if added >= m0:
            base = r["cost_basis"]
        else:
            h = inv._safe_history(_yahoo_symbol(r["kind"], sym), "3mo", "1d")
            closes = [(dt.datetime.fromtimestamp(c[0], dt.timezone.utc).date(), c[4]) for c in (h or {}).get("candles", [])]
            before = [c for d, c in closes if d < m0]
            base = before[-1] if before else r["cost_basis"]
        pnl = (px - base) * r["qty"]
        total += pnl
        items.append({"symbol": sym, "pnl_usd": round(pnl, 2), "pct": round(100 * (px / base - 1), 2) if base else None})
    fx = core.usdils()
    return {"month": today.strftime("%Y-%m"), "pnl_usd": round(total, 2), "pnl_ils": round(total * fx, 2), "items": sorted(items, key=lambda i: -abs(i["pnl_usd"]))}


def overview() -> dict[str, Any]:
    seed_defaults()
    a = main_balance()
    rec = list_recurring()
    monthly = sum(r["amount"] for r in rec if r["active"])
    pend = json.loads(db.meta_get("cc_pending") or "null") or {}
    return {"main_balance": a, "recurring": rec, "recurring_net_month": monthly, "cc_pending": pend or None, "cc_history": cc_history(),
            "stocks_mtd": stocks_mtd()}
