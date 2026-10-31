"""BUDGET — income, expenses, recurring payments and savings rate, from bank / credit-card CSV exports.

Import: any CSV (or the Excel file saved as CSV) from an Israeli bank or card issuer. Columns are detected by their Hebrew or
English header: date (תאריך / תאריך עסקה / תאריך ערך / date), description (תיאור / שם בית העסק / פרטים / description / merchant),
amount (סכום / סכום חיוב / סכום עסקה / amount) or separate debit / credit columns (חובה / זכות, debit / credit).
Dates dd/mm/yyyy, dd.mm.yy or ISO. Expenses are stored negative, income positive. Duplicate rows (same date, description,
amount) are skipped, so the same file can be imported twice safely.
Categorisation: keyword rules on the merchant name (Hebrew + English), editable per transaction; a manual category always wins.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import statistics
from collections import defaultdict
from typing import Any

from app import db

SCHEMA = """CREATE TABLE IF NOT EXISTS budget_tx (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, description TEXT NOT NULL,
    amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'ILS', category TEXT, manual INTEGER NOT NULL DEFAULT 0, account TEXT,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE(day, description, amount, account))"""

RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Income", ("משכורת", "salary", "העברה מ", "זיכוי", "bit ממ", "החזר", "refund", "דיבידנד", "ריבית זכות")),
    ("Groceries", ("שופרסל", "רמי לוי", "יוחננוף", "ויקטורי", "מגה", "טיב טעם", "אושר עד", "חצי חינם", "סופר", "מכולת", "am:pm", "יינות ביתן", "carrefour", "קרפור")),
    ("Restaurants & cafes", ("מסעדה", "קפה", "cafe", "coffee", "ארומה", "קופי", "בורגר", "פיצה", "wolt", "וולט", "תן ביס", "10bis", "סושי", "מקדונלד", "mcdonald")),
    ("Fuel & transport", ("דלק", "פז", "סונול", "דור אלון", "ten ", "yellow", "רב קו", "rav kav", "gett", "גט טקסי", "רכבת", "חניון", "pango", "פנגו", "cellopark", "סלופארק", "כביש 6")),
    ("Housing & bills", ("שכר דירה", "ארנונה", "חברת החשמל", "חשמל", "מים", "תאגיד", "גז", "ועד בית", "משכנתא", "mortgage")),
    ("Phone & internet", ("סלקום", "פרטנר", "פלאפון", "הוט", "hot ", "yes ", "בזק", "גולן טלקום", "019", "012", "wecom", "we4g")),
    ("Subscriptions", ("netflix", "spotify", "apple", "google", "youtube", "disney", "amazon prime", "chatgpt", "openai", "anthropic", "microsoft", "icloud", "adobe")),
    ("Health", ("סופר-פארם", "סופר פארם", "super-pharm", "be ", "מכבי", "כללית", "מאוחדת", "לאומית", "רופא", "מרפאה", "בית מרקחת", "אופטיקה")),
    ("Insurance", ("ביטוח", "הראל", "מגדל", "כלל", "הפניקס", "מנורה", "איילון", "insurance")),
    ("Shopping", ("זארה", "zara", "h&m", "קסטרו", "fox", "פוקס", "אמזון", "amazon", "aliexpress", "עלי אקספרס", "shein", "איקאה", "ikea", "ace", "הום סנטר", "kSP", "באג", "ivory", "איביי", "ebay")),
    ("Leisure & travel", ("סינמה", "cinema", "יס פלאנט", "booking", "airbnb", "אל על", "el al", "ישראייר", "ארקיע", "wizz", "ryanair", "מלון", "hotel", "כושר", "gym", "הולמס")),
    ("Investing & savings", ("interactive brokers", "ibkr", "אקסלנס", "מיטב", "פסגות", "ברוקר", "bits of gold", "ביטס אוף גולד", "binance", "coinbase", "קופת גמל", "קרן השתלמות", "הפקדה לחיסכון")),
    ("Cash & transfers", ("משיכת מזומן", "כספומט", "atm", "bit", "ביט", "paybox", "פייבוקס", "העברה ל")),
    ("Taxes & fees", ("מס הכנסה", "ביטוח לאומי", "עמלה", "דמי כרטיס", "ריבית חובה", "רשות המסים")),
]
HEAD = {
    "day": ("תאריך עסקה", "תאריך רכישה", "תאריך", "תאריך ערך", "date", "transaction date"),
    "desc": ("שם בית העסק", "שם בית עסק", "תיאור", "פרטים", "תיאור התנועה", "description", "merchant", "details", "הפעולה"),
    "amount": ("סכום חיוב", "סכום עסקה", "סכום", "amount", "charge", "סכום בש\"ח", "סכום בשקלים"),
    "debit": ("חובה", "debit", "withdrawal"),
    "credit": ("זכות", "credit", "deposit"),
}


def ensure() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)


def categorize(desc: str, amount: float) -> str:
    d = f" {desc.lower()} "
    for cat, words in RULES:
        if any(w.lower() in d for w in words):
            if cat == "Income" and amount < 0:
                continue
            return cat
    return "Income" if amount > 0 else "Other"


def _num(s: str) -> float | None:
    if s is None:
        return None
    t = str(s).strip().replace("₪", "").replace(",", "").replace("‏", "").replace("‎", "").replace(" ", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if t.endswith("-"):
        t, neg = t[:-1], True
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _find(headers: list[str], keys: tuple[str, ...]) -> int | None:
    norm = [h.strip().strip('"').lower() for h in headers]
    for k in keys:
        for i, h in enumerate(norm):
            if h == k.lower():
                return i
    for k in keys:
        for i, h in enumerate(norm):
            if k.lower() in h:
                return i
    return None


def import_csv(text: str, account: str = "", expense_positive: bool | None = None) -> dict[str, Any]:
    """Parse and store a bank/card CSV. Card exports list charges as positive numbers: expense_positive=None guesses it
    (most rows positive and no credit column -> they are charges)."""
    ensure()
    text = text.lstrip("﻿")
    sample = text[:4000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    # header = the first row that has a date-like and an amount-like column name
    hi = None
    for i, r in enumerate(rows[:40]):
        if _find(r, HEAD["day"]) is not None and (_find(r, HEAD["amount"]) is not None or _find(r, HEAD["debit"]) is not None):
            hi = i
            break
    if hi is None:
        raise ValueError("Could not find the header row (need a date column and an amount / debit column).")
    h = rows[hi]
    ci = {k: _find(h, v) for k, v in HEAD.items()}
    if ci["desc"] is None:
        ci["desc"] = next((i for i in range(len(h)) if i not in (ci["day"], ci["amount"], ci["debit"], ci["credit"])), None)
    parsed = []
    for r in rows[hi + 1:]:
        if not r or len(r) <= (ci["day"] or 0):
            continue
        day = _date(r[ci["day"]])
        if not day:
            continue
        desc = (r[ci["desc"]] if ci["desc"] is not None and ci["desc"] < len(r) else "").strip()
        if ci["amount"] is not None and ci["amount"] < len(r) and _num(r[ci["amount"]]) is not None:
            amt = _num(r[ci["amount"]])
        else:
            deb = _num(r[ci["debit"]]) if ci["debit"] is not None and ci["debit"] < len(r) else None
            cre = _num(r[ci["credit"]]) if ci["credit"] is not None and ci["credit"] < len(r) else None
            amt = (cre or 0.0) - (deb or 0.0)
        if amt is None or amt == 0:
            continue
        parsed.append([day, desc or "—", amt])
    if expense_positive is None:
        pos = sum(1 for p in parsed if p[2] > 0)
        expense_positive = ci["credit"] is None and ci["debit"] is None and pos > 0.7 * max(1, len(parsed))
    added = skipped = 0
    with db.get_conn() as conn:
        for day, desc, amt in parsed:
            a = -amt if expense_positive else amt
            try:
                conn.execute("INSERT INTO budget_tx (day, description, amount, category, account) VALUES (?,?,?,?,?)",
                             (day, desc, round(a, 2), categorize(desc, a), account or None))
                added += 1
            except Exception:  # noqa: BLE001 - UNIQUE: already imported
                skipped += 1
    return {"rows": len(parsed), "added": added, "skipped_duplicates": skipped, "treated_charges_as_expenses": bool(expense_positive),
            "columns": {k: (h[v] if v is not None else None) for k, v in ci.items()}}


def add_tx(day: str, description: str, amount: float, category: str | None = None, account: str | None = None) -> dict[str, Any]:
    ensure()
    with db.get_conn() as conn:
        cid = conn.execute("INSERT INTO budget_tx (day, description, amount, category, manual, account) VALUES (?,?,?,?,?,?)",
                           (day, description, amount, category or categorize(description, amount), 1 if category else 0, account)).lastrowid
    return {"id": cid}


def set_category(tx_id: int, category: str, apply_to_same: bool = True) -> int:
    ensure()
    with db.get_conn() as conn:
        row = conn.execute("SELECT description FROM budget_tx WHERE id=?", (tx_id,)).fetchone()
        if not row:
            return 0
        if apply_to_same:
            return conn.execute("UPDATE budget_tx SET category=?, manual=1 WHERE description=?", (category, row["description"])).rowcount
        return conn.execute("UPDATE budget_tx SET category=?, manual=1 WHERE id=?", (category, tx_id)).rowcount


def delete_tx(tx_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM budget_tx WHERE id=?", (tx_id,))


def _merchant(desc: str) -> str:
    return re.sub(r"[\d#*]+", "", desc.lower()).strip()[:28]


def summary(months: int = 12) -> dict[str, Any]:
    ensure()
    since = (dt.date.today().replace(day=1) - dt.timedelta(days=31 * (months - 1))).replace(day=1).isoformat()
    with db.get_conn() as conn:
        tx = [dict(r) for r in conn.execute("SELECT * FROM budget_tx WHERE day >= ? ORDER BY day DESC, id DESC", (since,)).fetchall()]
    by_month: dict[str, dict[str, float]] = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    by_cat_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in tx:
        m = t["day"][:7]
        if t["category"] in ("Investing & savings",):
            by_cat_month[t["category"]][m] += -t["amount"]
            continue
        if t["amount"] > 0:
            by_month[m]["income"] += t["amount"]
        else:
            by_month[m]["expense"] += -t["amount"]
            by_cat_month[t["category"] or "Other"][m] += -t["amount"]
    months_sorted = sorted(by_month)
    series = [{"month": m, "income": round(by_month[m]["income"], 2), "expense": round(by_month[m]["expense"], 2),
               "net": round(by_month[m]["income"] - by_month[m]["expense"], 2),
               "savings_rate": round(100 * (by_month[m]["income"] - by_month[m]["expense"]) / by_month[m]["income"], 1) if by_month[m]["income"] else None}
              for m in months_sorted]
    full = [s for s in series if s["month"] < dt.date.today().strftime("%Y-%m")] or series
    avg = lambda k: round(statistics.mean(s[k] for s in full[-6:]), 2) if full else 0.0  # noqa: E731
    cats = []
    for c, mm in by_cat_month.items():
        vals = [mm.get(s["month"], 0.0) for s in full[-6:]]
        cats.append({"category": c, "avg_month": round(statistics.mean(vals), 2) if vals else 0.0, "last_month": round(mm.get(full[-1]["month"], 0.0), 2) if full else 0.0,
                     "total": round(sum(mm.values()), 2)})
    cats.sort(key=lambda c: -c["avg_month"])
    # recurring: same merchant in >= 3 of the last 6 months with a similar amount
    rec: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for t in tx:
        if t["amount"] < 0:
            rec[_merchant(t["description"])].append((t["day"][:7], -t["amount"]))
    recurring = []
    for mname, items in rec.items():
        ms = {m for m, _ in items}
        if len(ms) >= 3:
            amts = [a for _, a in items]
            med = statistics.median(amts)
            if statistics.pstdev(amts) <= 0.35 * med + 5:
                recurring.append({"merchant": mname, "months": len(ms), "typical": round(med, 2), "yearly": round(med * 12, 2)})
    recurring.sort(key=lambda r: -r["yearly"])
    return {"months": series, "avg_income": avg("income"), "avg_expense": avg("expense"), "avg_net": avg("net"),
            "savings_rate": round(100 * avg("net") / avg("income"), 1) if avg("income") else None, "categories": cats,
            "recurring": recurring[:25], "recurring_yearly": round(sum(r["yearly"] for r in recurring), 2), "transactions": tx[:400],
            "count": len(tx), "category_names": [c for c, _ in RULES] + ["Other"]}
