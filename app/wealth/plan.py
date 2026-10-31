"""SAVINGS GOALS + ROAD TO ₪1M (daily plan you tick off).

Savings goals (INVESTMENTS page): named pots with a target and a deadline ("Car ₪60k by 2027-12"). Deposits / withdrawals are
logged (savings_moves); each goal shows progress, what is needed per month to make the deadline and whether it is on track
(saved vs a straight line from the start to the deadline). Earmarks only — no money is moved anywhere.

Road to ₪1M (BUSINESS page): revenue since the plan start (Shopify net sales, read-only, + revenue entered by hand) against a
target (default ₪1,000,000), split into six phases at 1% / 5% / 10% / 25% / 50% / 100% of the target. Each phase has one-time
milestone tasks; on top of that there are daily and weekly habit tasks. Ticks are stored per task per day (weekly tasks per
ISO week, keyed by its Monday; one-time tasks with day 'once'). Streak = consecutive days with ≥ 80% of the daily tasks done.

Telegram: every morning (plan setting tg_hour, Israel time) the bot sends today's checklist with a button per task; tapping
a button ticks / unticks it and the message updates. At 21:00 a reminder goes out if less than 80% is done.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

from app import db
from app.wealth import core

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS plan_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, freq TEXT NOT NULL DEFAULT 'daily',
       phase INTEGER, category TEXT, minutes INTEGER, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0,
       created_at TEXT NOT NULL DEFAULT (datetime('now')))""",
    """CREATE TABLE IF NOT EXISTS plan_checks (task_id INTEGER NOT NULL, day TEXT NOT NULL, done_at REAL NOT NULL, PRIMARY KEY (task_id, day))""",
    """CREATE TABLE IF NOT EXISTS revenue_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, amount REAL NOT NULL, source TEXT, note TEXT)""",
    """CREATE TABLE IF NOT EXISTS savings_goals (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, emoji TEXT, target REAL NOT NULL,
       deadline TEXT, color TEXT, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT (date('now')))""",
    """CREATE TABLE IF NOT EXISTS savings_moves (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, day TEXT NOT NULL, amount REAL NOT NULL, note TEXT)""",
]
STREAK_PCT = 80
PHASES = [  # (share of target, name, focus)
    (0.01, "FIRST SALES", "Prove people buy: one hero product, a page that converts, content every day, the first 10 sales and reviews."),
    (0.05, "REPEATABLE", "Turn luck into a system: capture emails, test small paid ads, find creatives that sell, raise order value."),
    (0.10, "SCALE ADS", "Pour money on what works: track cost per purchase, scale winners 20% at a time, influencers, a second product."),
    (0.25, "SYSTEMS", "Stop doing everything yourself: outsource, write procedures, open a second sales channel, monthly P&L."),
    (0.50, "BRAND", "Customers come back and tell friends: loyalty, a helper, new audiences."),
    (1.00, "₪1M", "Reinvest in the best channel, better supplier prices — and plan the next million."),
]
DEFAULT_TASKS = [
    # daily habits
    ("Check yesterday's sales, orders and ad spend", "daily", None, "numbers", 10),
    ("Post 1 short video (TikTok / Reels / Story)", "daily", None, "content", 30),
    ("Answer every customer message and review", "daily", None, "customers", 15),
    ("Reach out to 5 new people: customers, influencers, groups", "daily", None, "outreach", 20),
    ("1 focused hour improving the store (page, offer, product)", "daily", None, "store", 60),
    ("Learn 20 minutes: ads, marketing or a competitor", "daily", None, "learn", 20),
    # weekly
    ("Test 1 new ad creative or offer", "weekly", None, "ads", 60),
    ("Weekly numbers review: revenue, order value, conversion, ad cost", "weekly", None, "numbers", 30),
    ("Send 1 email / WhatsApp campaign to customers", "weekly", None, "customers", 30),
    ("Add or improve 1 product", "weekly", None, "store", 60),
    # one-time milestones per phase
    ("Pick 1 hero product and write down why people buy it", "once", 0, "store", None),
    ("Product page that converts: photos, reviews, clear price and shipping", "once", 0, "store", None),
    ("Post 30 short videos", "once", 0, "content", None),
    ("First 10 sales (friends, family, groups)", "once", 0, "outreach", None),
    ("Collect the first 5 reviews", "once", 0, "customers", None),
    ("Email / WhatsApp capture popup (10% off first order)", "once", 1, "customers", None),
    ("First paid ad test: ₪50/day for 7 days", "once", 1, "ads", None),
    ("Find 3 creatives that sell and keep them running", "once", 1, "ads", None),
    ("Add an upsell or bundle to raise order value", "once", 1, "store", None),
    ("Abandoned-cart messages switched on", "once", 1, "customers", None),
    ("Track cost per purchase and ROAS every week", "once", 2, "numbers", None),
    ("Scale ads with ROAS above 2 by 20%", "once", 2, "ads", None),
    ("Work with 5 micro-influencers", "once", 2, "outreach", None),
    ("Launch a 2nd product for existing customers", "once", 2, "store", None),
    ("Outsource customer service or fulfilment", "once", 3, "team", None),
    ("Write step-by-step procedures for the daily work", "once", 3, "team", None),
    ("Open a second sales channel (marketplace / wholesale)", "once", 3, "store", None),
    ("Monthly P&L: revenue, ad cost, product cost, profit", "once", 3, "numbers", None),
    ("Hire a first part-time helper", "once", 4, "team", None),
    ("Loyalty / repeat-purchase program", "once", 4, "customers", None),
    ("Expand to a new audience or country", "once", 4, "ads", None),
    ("Reinvest profits in the best channel", "once", 5, "numbers", None),
    ("Negotiate better supplier prices", "once", 5, "store", None),
    ("Plan the next ₪1M", "once", 5, "numbers", None),
]


def _today() -> dt.date:
    return dt.datetime.now(core.IL).date()


def _monday(d: dt.date) -> str:
    return (d - dt.timedelta(days=d.weekday())).isoformat()


_ready = False


def ensure() -> None:
    """Create / seed once per process (DDL waits on the database lock while the trainer writes)."""
    global _ready
    if _ready:
        return
    _ready = True
    with db.get_conn() as conn:
        for s in SCHEMA:
            conn.execute(s)
    if not db.meta_get("plan_seeded"):
        with db.get_conn() as conn:
            for i, (t, f, ph, cat, mins) in enumerate(DEFAULT_TASKS):
                conn.execute("INSERT INTO plan_tasks (title, freq, phase, category, minutes, sort) VALUES (?,?,?,?,?,?)", (t, f, ph, cat, mins, i))
        db.meta_set("plan_seeded", "1")
    if not db.meta_get("plan_tg_started"):  # the Telegram checklist starts the morning after the plan is created
        day = _today().isoformat()
        db.meta_set(f"plan_msg_{day}", "skip")
        db.meta_set(f"plan_eve_{day}", "1")
        db.meta_set("plan_tg_started", "1")


# ------------------------------------------------------------------ settings
def settings() -> dict[str, Any]:
    t = _today()
    base = {"target": 1_000_000.0, "currency": "ILS", "start": t.isoformat(), "deadline": t.replace(year=t.year + 3).isoformat(),
            "shopify": True, "telegram": True, "tg_hour": 8}
    try:
        saved = json.loads(db.meta_get("revenue_plan") or "{}")
    except ValueError:
        saved = {}
    if not saved:
        db.meta_set("revenue_plan", json.dumps(base))  # freeze the start date on first use
    return {**base, **saved}


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    cur = settings()
    for k in ("target", "start", "deadline", "shopify", "telegram", "tg_hour"):
        if k in patch and patch[k] is not None:
            cur[k] = patch[k]
    cur["target"] = max(1000.0, float(cur["target"]))
    cur["tg_hour"] = max(5, min(12, int(cur["tg_hour"])))
    db.meta_set("revenue_plan", json.dumps(cur))
    return cur


# ------------------------------------------------------------------ revenue
def revenue() -> dict[str, Any]:
    ensure()
    st = settings()
    start = dt.date.fromisoformat(st["start"])
    today = _today()
    by_day: dict[str, float] = {}
    shop_total, shop_err, capped = 0.0, None, False
    if st.get("shopify"):
        try:
            from app.wealth import shopify

            if shopify.status()["connected"]:
                days = (today - start).days + 1
                capped = days > 365
                n = max(7, min(365, days))
                s = shopify.summary(n)
                if s.get("error"):
                    shop_err = s["error"]
                for x in s.get("series", []):
                    if x["day"] >= st["start"]:
                        by_day[x["day"]] = by_day.get(x["day"], 0.0) + x["net"]
                        shop_total += x["net"]
        except Exception as e:  # noqa: BLE001
            shop_err = str(e)
    with db.read_conn() as conn:
        entries = [dict(r) for r in conn.execute("SELECT * FROM revenue_entries WHERE day >= ? ORDER BY day DESC", (st["start"],)).fetchall()]
    manual = 0.0
    for e in entries:
        by_day[e["day"]] = by_day.get(e["day"], 0.0) + e["amount"]
        manual += e["amount"]
    total = shop_total + manual
    series, cum = [], 0.0
    d = start
    while d <= today:
        cum += by_day.get(d.isoformat(), 0.0)
        series.append({"day": d.isoformat(), "revenue": round(cum, 2), "daily": round(by_day.get(d.isoformat(), 0.0), 2)})
        d += dt.timedelta(days=1)
    last30 = sum(v for k, v in by_day.items() if k >= (today - dt.timedelta(days=29)).isoformat())
    return {"total": round(total, 2), "shopify": round(shop_total, 2), "manual": round(manual, 2), "entries": entries[:50],
            "series": series, "last30": round(last30, 2), "shopify_error": shop_err, "capped_365": capped}


def add_revenue(day: str, amount: float, source: str | None, note: str | None) -> dict[str, Any]:
    ensure()
    with db.get_conn() as conn:
        rid = conn.execute("INSERT INTO revenue_entries (day, amount, source, note) VALUES (?,?,?,?)", (day, float(amount), source, note)).lastrowid
    return {"id": rid}


def delete_revenue(rid: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM revenue_entries WHERE id=?", (rid,))


# ------------------------------------------------------------------ tasks + ticks
def _tasks(active_only: bool = True) -> list[dict[str, Any]]:
    ensure()
    with db.read_conn() as conn:
        q = "SELECT * FROM plan_tasks" + (" WHERE active=1" if active_only else "") + " ORDER BY freq, phase, sort, id"
        return [dict(r) for r in conn.execute(q).fetchall()]


def _key(task: dict[str, Any], day: dt.date) -> str:
    return "once" if task["freq"] == "once" else _monday(day) if task["freq"] == "weekly" else day.isoformat()


def _done_set(keys: list[str]) -> set[tuple[int, str]]:
    if not keys:
        return set()
    with db.read_conn() as conn:
        rows = conn.execute(f"SELECT task_id, day FROM plan_checks WHERE day IN ({','.join('?' * len(keys))})", keys).fetchall()
    return {(r["task_id"], r["day"]) for r in rows}


def toggle(task_id: int, day: str | None = None, done: bool | None = None) -> dict[str, Any]:
    ensure()
    d = dt.date.fromisoformat(day) if day else _today()
    with db.get_conn() as conn:
        t = conn.execute("SELECT * FROM plan_tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            raise ValueError("no such task")
        k = _key(dict(t), d)
        has = conn.execute("SELECT 1 FROM plan_checks WHERE task_id=? AND day=?", (task_id, k)).fetchone() is not None
        want = (not has) if done is None else bool(done)
        if want and not has:
            conn.execute("INSERT INTO plan_checks (task_id, day, done_at) VALUES (?,?,?)", (task_id, k, time.time()))
        elif not want and has:
            conn.execute("DELETE FROM plan_checks WHERE task_id=? AND day=?", (task_id, k))
    return {"task_id": task_id, "day": k, "done": want, "today": day_stats(d)}


def day_stats(d: dt.date, tasks: list[dict[str, Any]] | None = None, done: set | None = None) -> dict[str, Any]:
    daily = [t for t in (tasks or _tasks()) if t["freq"] == "daily"]
    done = done if done is not None else _done_set([d.isoformat()])
    n = sum(1 for t in daily if (t["id"], d.isoformat()) in done)
    return {"day": d.isoformat(), "done": n, "total": len(daily), "pct": round(100 * n / len(daily)) if daily else 0}


def _history(days: int = 120, tasks: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """day -> % of the daily tasks done."""
    daily_ids = {t["id"] for t in (tasks or _tasks()) if t["freq"] == "daily"}
    if not daily_ids:
        return {}
    since = (_today() - dt.timedelta(days=days)).isoformat()
    with db.read_conn() as conn:
        rows = conn.execute("SELECT day, task_id FROM plan_checks WHERE day >= ? AND day != 'once'", (since,)).fetchall()
    cnt: dict[str, int] = {}
    for r in rows:
        if r["task_id"] in daily_ids:
            cnt[r["day"]] = cnt.get(r["day"], 0) + 1
    return {k: round(100 * v / len(daily_ids)) for k, v in cnt.items()}


def add_task(title: str, freq: str, phase: int | None = None, category: str | None = None, minutes: int | None = None) -> dict[str, Any]:
    ensure()
    if freq not in ("daily", "weekly", "once"):
        raise ValueError("freq must be daily, weekly or once")
    with db.get_conn() as conn:
        tid = conn.execute("INSERT INTO plan_tasks (title, freq, phase, category, minutes, sort) VALUES (?,?,?,?,?,999)",
                           (title.strip()[:120], freq, phase, category, minutes)).lastrowid
    return {"id": tid}


def update_task(task_id: int, title: str | None = None, active: bool | None = None) -> None:
    with db.get_conn() as conn:
        if title is not None:
            conn.execute("UPDATE plan_tasks SET title=? WHERE id=?", (title.strip()[:120], task_id))
        if active is not None:
            conn.execute("UPDATE plan_tasks SET active=? WHERE id=?", (1 if active else 0, task_id))


# ------------------------------------------------------------------ the whole plan for the page
def overview(day: str | None = None) -> dict[str, Any]:
    ensure()
    st = settings()
    d = dt.date.fromisoformat(day) if day else _today()
    today = _today()
    rev = revenue()
    target = float(st["target"])
    total = rev["total"]
    tasks = _tasks()
    done = _done_set([d.isoformat(), _monday(d), "once"])
    for t in tasks:
        t["done"] = (t["id"], _key(t, d)) in done
    phases = []
    cur_phase = len(PHASES) - 1
    prev = 0.0
    for i, (share, name, focus) in enumerate(PHASES):
        goal = target * share
        state = "done" if total >= goal else ("current" if total >= prev else "next")
        if state == "current":
            cur_phase = i
        ph_tasks = [t for t in tasks if t["freq"] == "once" and t["phase"] == i]
        phases.append({"i": i, "name": name, "focus": focus, "from": prev, "to": goal, "state": state,
                       "pct": round(max(0.0, min(100.0, 100 * (total - prev) / (goal - prev))), 1) if goal > prev else 100.0,
                       "tasks": ph_tasks, "tasks_done": sum(1 for t in ph_tasks if t["done"])})
        prev = goal
    hist = _history(120, tasks)
    streak, k = 0, today if hist.get(today.isoformat(), 0) >= STREAK_PCT else today - dt.timedelta(days=1)
    while hist.get(k.isoformat(), 0) >= STREAK_PCT:
        streak += 1
        k -= dt.timedelta(days=1)
    best, run = 0, 0
    for i in range(120, -1, -1):
        if hist.get((today - dt.timedelta(days=i)).isoformat(), 0) >= STREAK_PCT:
            run += 1; best = max(best, run)
        else:
            run = 0
    deadline = dt.date.fromisoformat(st["deadline"])
    days_left = max(1, (deadline - today).days)
    remaining = max(0.0, target - total)
    per_day_30 = rev["last30"] / 30
    eta = (today + dt.timedelta(days=int(remaining / per_day_30))).isoformat() if per_day_30 > 0 and remaining > 0 else None
    heat = [{"day": (today - dt.timedelta(days=i)).isoformat(), "pct": hist.get((today - dt.timedelta(days=i)).isoformat(), 0)} for i in range(111, -1, -1)]
    return {"settings": st, "day": d.isoformat(), "today": today.isoformat(), "revenue": rev, "target": target, "total": total,
            "pct": round(100 * total / target, 2), "remaining": round(remaining, 2), "days_left": days_left,
            "need_per_day": round(remaining / days_left, 2), "need_per_month": round(remaining / days_left * 30.4, 2),
            "pace_per_day": round(per_day_30, 2), "eta": eta, "phase": cur_phase, "phases": phases,
            "daily": [t for t in tasks if t["freq"] == "daily"], "weekly": [t for t in tasks if t["freq"] == "weekly"],
            "week_of": _monday(d), "day_stats": day_stats(d, tasks, done), "streak": streak, "best_streak": best, "heat": heat,
            "streak_rule": f"a day counts when at least {STREAK_PCT}% of the daily tasks are done"}


# ------------------------------------------------------------------ Telegram checklist
def _tg_payload(d: dt.date) -> tuple[str, list[list[dict]]]:
    o = overview(d.isoformat())
    ds = o["day_stats"]
    ph = o["phases"][o["phase"]]
    lines = [f"🎯 ROAD TO ₪{o['target']:,.0f} — {d.strftime('%a %d %b')}",
             f"Revenue ₪{o['total']:,.0f} ({o['pct']:.1f}%) · phase {ph['name']}",
             f"Today {ds['done']}/{ds['total']} done · 🔥 streak {o['streak']} days",
             "", "Tap a task when it's done:"]
    kb = [[{"text": f"{'✅' if t['done'] else '⬜'} {t['title'][:48]}", "callback_data": f"plan:{t['id']}:{d.isoformat()}"}] for t in o["daily"]]
    open_weekly = [t for t in o["weekly"] if not t["done"]]
    kb += [[{"text": f"⬜ (this week) {t['title'][:38]}", "callback_data": f"plan:{t['id']}:{d.isoformat()}"}] for t in open_weekly]
    nxt = next((t for t in ph["tasks"] if not t["done"]), None)
    if nxt:
        kb.append([{"text": f"🏁 {nxt['title'][:46]}", "callback_data": f"plan:{nxt['id']}:{d.isoformat()}"}])
    if ds["total"] and ds["done"] == ds["total"]:
        lines.insert(3, "💪 All daily tasks done!")
    return "\n".join(lines), kb


def telegram_due(now: dt.datetime | None = None) -> None:
    st = settings()
    if not st.get("telegram"):
        return
    now = now or dt.datetime.now(core.IL)
    day = now.date().isoformat()
    from app.notify import telegram_bot

    if now.hour >= int(st["tg_hour"]) and not db.meta_get(f"plan_msg_{day}"):
        text, kb = _tg_payload(now.date())
        mid = telegram_bot.send_text_buttons(text, kb)
        db.meta_set(f"plan_msg_{day}", str(mid or 0))
    if now.hour >= 21 and not db.meta_get(f"plan_eve_{day}"):
        db.meta_set(f"plan_eve_{day}", "1")
        ds = day_stats(now.date())
        if ds["total"] and ds["pct"] < STREAK_PCT:
            left = [t["title"] for t in _tasks() if t["freq"] == "daily" and (t["id"], day) not in _done_set([day])]
            text, kb = _tg_payload(now.date())
            telegram_bot.send_text_buttons(f"⏰ {len(left)} tasks left today — finish {max(0, -(-STREAK_PCT * ds['total'] // 100) - ds['done'])} more to keep the streak.\n\n" + text, kb)


def handle_callback(data: str, message_id: int, cq_id: str) -> None:
    from app.notify import telegram_bot

    _, tid, day = data.split(":", 2)
    r = toggle(int(tid), day)
    telegram_bot.answer_callback_query(cq_id, "✅ Done" if r["done"] else "Unchecked")
    text, kb = _tg_payload(dt.date.fromisoformat(day))
    telegram_bot.edit_text_buttons(message_id, text, kb)


def send_now() -> None:
    text, kb = _tg_payload(_today())
    from app.notify import telegram_bot

    mid = telegram_bot.send_text_buttons(text, kb)
    db.meta_set(f"plan_msg_{_today().isoformat()}", str(mid or 0))


# ------------------------------------------------------------------ savings goals
def goals() -> dict[str, Any]:
    ensure()
    today = _today()
    with db.read_conn() as conn:
        gs = [dict(r) for r in conn.execute("SELECT * FROM savings_goals WHERE archived=0 ORDER BY id").fetchall()]
        moves = [dict(r) for r in conn.execute("SELECT * FROM savings_moves ORDER BY day, id").fetchall()]
    out = []
    for g in gs:
        mv = [m for m in moves if m["goal_id"] == g["id"]]
        saved = sum(m["amount"] for m in mv)
        target = float(g["target"])
        remaining = max(0.0, target - saved)
        start = dt.date.fromisoformat(str(g["created_at"])[:10])
        dl = dt.date.fromisoformat(g["deadline"]) if g.get("deadline") else None
        months_left = max(0.0, (dl - today).days / 30.44) if dl else None
        expected = None
        if dl and dl > start:
            expected = round(100 * min(1.0, max(0.0, (today - start).days / (dl - start).days)), 1)
        pct = round(100 * saved / target, 1) if target else 0.0
        last90 = sum(m["amount"] for m in mv if m["day"] >= (today - dt.timedelta(days=90)).isoformat() and m.get("note") != "starting amount")
        rate = last90 / 3  # per month, last 3 months
        eta = (today + dt.timedelta(days=int(30.44 * remaining / rate))).isoformat() if rate > 0 and remaining > 0 else None
        cum, curve = 0.0, []
        for m in mv:
            cum += m["amount"]
            curve.append({"day": m["day"], "saved": round(cum, 2)})
        status = "done" if saved >= target else ("ahead" if expected is not None and pct >= expected + 2 else
                                                  "behind" if expected is not None and pct < expected - 5 else "on track" if expected is not None else "no deadline")
        out.append({**g, "saved": round(saved, 2), "remaining": round(remaining, 2), "pct": pct, "expected_pct": expected,
                    "months_left": round(months_left, 1) if months_left is not None else None,
                    "monthly_needed": round(remaining / months_left, 0) if months_left and months_left > 0.3 else (remaining if dl else None),
                    "rate_month": round(rate, 0), "eta": eta, "status": status, "moves": mv[-12:][::-1], "curve": curve})
    tot_t = sum(g["target"] for g in out)
    tot_s = sum(g["saved"] for g in out)
    return {"goals": out, "total_target": tot_t, "total_saved": tot_s, "total_monthly_needed": sum(g["monthly_needed"] or 0 for g in out if g["status"] != "done")}


def upsert_goal(g: dict[str, Any]) -> dict[str, Any]:
    ensure()
    with db.get_conn() as conn:
        if g.get("id"):
            conn.execute("UPDATE savings_goals SET name=?, emoji=?, target=?, deadline=?, color=? WHERE id=?",
                         (g["name"], g.get("emoji"), float(g["target"]), g.get("deadline") or None, g.get("color"), int(g["id"])))
            gid = int(g["id"])
        else:
            gid = conn.execute("INSERT INTO savings_goals (name, emoji, target, deadline, color) VALUES (?,?,?,?,?)",
                               (g["name"], g.get("emoji"), float(g["target"]), g.get("deadline") or None, g.get("color"))).lastrowid
            if g.get("saved"):
                conn.execute("INSERT INTO savings_moves (goal_id, day, amount, note) VALUES (?,?,?,?)", (gid, _today().isoformat(), float(g["saved"]), "starting amount"))
    return {"id": gid}


def delete_goal(gid: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE savings_goals SET archived=1 WHERE id=?", (gid,))


def move(gid: int, amount: float, note: str | None = None) -> dict[str, Any]:
    ensure()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO savings_moves (goal_id, day, amount, note) VALUES (?,?,?,?)", (gid, _today().isoformat(), float(amount), note))
    return next((g for g in goals()["goals"] if g["id"] == gid), {})
