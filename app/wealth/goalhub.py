"""GOALS & MISSIONS — one goal system for the whole terminal: the NOSTRO farm, stocks, wealth, the business and anything
personal. A goal either tracks a live metric of the system automatically (passed Apex evaluations, portfolio value, net
worth, revenue…) or a value the user updates by hand. Every goal carries the WORK: a checklist of tasks the user ticks off.

Progress: direction 'up' → (value − start) / (target − start); 'down' (e.g. "largest holding below 40%") → (start − value) /
(start − target). Milestones at 25 / 50 / 75 / 100%. A daily value snapshot (hub_log) gives each goal a history line, a
30-day pace and an ETA. XP: 10 per finished task, 100 per reached goal; level = 1 + floor(sqrt(xp / 60)).
"""
from __future__ import annotations

import datetime as dt
import math
import time
from typing import Any

from app import db
from app.wealth import core

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS hub_goals (id INTEGER PRIMARY KEY AUTOINCREMENT, area TEXT NOT NULL, title TEXT NOT NULL, emoji TEXT,
       metric TEXT, direction TEXT NOT NULL DEFAULT 'up', start_value REAL NOT NULL DEFAULT 0, target REAL NOT NULL, unit TEXT,
       deadline TEXT, manual_value REAL, why TEXT, archived INTEGER NOT NULL DEFAULT 0, reached_at TEXT, sort INTEGER NOT NULL DEFAULT 0,
       created_at TEXT NOT NULL DEFAULT (date('now')))""",
    """CREATE TABLE IF NOT EXISTS hub_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, title TEXT NOT NULL,
       done_at TEXT, sort INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS hub_log (goal_id INTEGER NOT NULL, day TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY (goal_id, day))""",
]
AREAS = {"nostro": ("NOSTRO FARM", "#ffa028"), "stocks": ("STOCKS", "#2be07e"), "wealth": ("WEALTH", "#5aa9ff"),
         "business": ("BUSINESS", "#c792ff"), "personal": ("PERSONAL", "#e8d44d")}
# metric key -> (area, label, unit)
METRICS = {
    "nostro.passed": ("nostro", "Apex evaluations passed (all time)", "count"),
    "nostro.best_eval_pct": ("nostro", "Closest account to passing (% of its profit target)", "%"),
    "nostro.withdrawable": ("nostro", "Withdrawable from passed accounts (est.)", "$"),
    "nostro.net_pnl": ("nostro", "NOSTRO net P&L of the live accounts (realized)", "$"),
    "nostro.win_rate_30d": ("nostro", "NOSTRO win rate, last 30 days", "%"),
    "stocks.value_ils": ("stocks", "Stock & crypto portfolio value", "₪"),
    "stocks.pnl_ils": ("stocks", "Portfolio profit (unrealized)", "₪"),
    "stocks.top_weight": ("stocks", "Largest single holding (% of portfolio)", "%"),
    "stocks.holdings": ("stocks", "Number of holdings", "count"),
    "wealth.networth_ils": ("wealth", "Net worth", "₪"),
    "wealth.runway": ("wealth", "Emergency runway (months of spending)", "months"),
    "wealth.savings_ils": ("wealth", "Saved in savings goals", "₪"),
    "business.revenue": ("business", "Revenue since the ₪1M plan started", "₪"),
    "business.streak": ("business", "Daily-plan streak", "days"),
}
DEFAULT_GOALS = [
    ("nostro", "Pass the first Apex evaluation", "🏆", "nostro.best_eval_pct", "up", 0, 100, "+3 months",
     "A pass proves the farm works before real money goes in.",
     ["Keep only strategies that were profitable over 3+ years", "Let risk sync size every account (no manual risk)",
      "Review every losing account each Sunday", "Never touch an account mid-trade", "Check the morning brief every day"]),
    ("nostro", "5 funded accounts", "🌾", "nostro.passed", "up", 0, 5, "+9 months",
     "Five funded accounts = a real monthly income stream.",
     ["Buy the first real Apex evaluation once a paper account passes twice", "Connect Tradovate (read-only) to track the real account",
      "Write a rule: max 2 real evaluations open at once", "Scale to 5 evaluations with the best 2 strategies", "Set up a combo evaluation for every new account"]),
    ("nostro", "First $1,000 payout", "💵", "nostro.withdrawable", "up", 0, 1000, "+6 months",
     "The first withdrawal turns the system into income.",
     ["Read the Apex payout rules (minimum days, consistency, buffer)", "Plan the payout day and where the money goes", "Keep a safety buffer above the drawdown floor"]),
    ("nostro", "NOSTRO live accounts +$10,000", "📈", "nostro.net_pnl", "up", 0, 10000, "+6 months",
     "Net profit across the paper farm before scaling up.",
     ["Remove any strategy that loses 3 weeks in a row", "Keep correlation low: not every account in the same trade", "Weekly: compare live results with the backtest"]),
    ("stocks", "Portfolio ₪100,000", "🚀", "stocks.value_ils", "up", None, 100000, "+2 years",
     "Build the long-term portfolio step by step.",
     ["Set a fixed monthly investment (DCA) and automate it", "Add a broad S&P 500 index ETF as the core", "Write a one-line thesis for every holding",
      "Monthly review: portfolio vs the S&P 500", "Invest every bonus / extra income the same week"]),
    ("stocks", "Diversify: largest holding under 40%", "⚖️", "stocks.top_weight", "down", None, 40, "+6 months",
     "One coin is ~97% of the portfolio — one bad month decides everything.",
     ["Decide the target mix (e.g. 50% index, 20% crypto, 30% stocks)", "Put new money only into what is under its target",
      "Rebalance when a holding drifts 10 points", "Check the RISK section every month"]),
    ("wealth", "Emergency fund: 6 months", "🛡️", "wealth.runway", "up", 0, 6, "+1 year",
     "Six months of spending in the bank means no forced selling.",
     ["Set the real main balance on MONEY › CASH FLOW", "Import the bank / card CSV to know the real monthly spend",
      "Move a fixed amount to savings on salary day", "Cut 1 subscription you don't use"]),
    ("wealth", "Net worth ₪250,000", "💎", "wealth.networth_ils", "up", None, 250000, "+3 years",
     "Everything you own minus what you owe.",
     ["Add pension / provident funds on MONEY › NET WORTH", "Review net worth every month-end", "Keep the savings rate above 20%"]),
    ("business", "Shopify: first ₪10,000 revenue", "🛍️", "business.revenue", "up", 0, 10000, "+4 months",
     "Phase 1 of the road to ₪1M.",
     ["Finish the FIRST SALES milestones on BUSINESS", "Post a short video every day for 30 days", "Get the first 5 reviews"]),
]


def _today() -> dt.date:
    return dt.datetime.now(core.IL).date()


_ready = False


def ensure() -> None:
    global _ready
    if _ready:
        return
    _ready = True
    with db.get_conn() as conn:
        for s in SCHEMA:
            conn.execute(s)
    if not db.meta_get("hub_seeded"):
        vals = metric_values()
        today = _today()
        with db.get_conn() as conn:
            for i, (area, title, emoji, metric, direction, start, target, dl, why, tasks) in enumerate(DEFAULT_GOALS):
                n, unit = dl[1:].split(" ")
                days = int(n) * (30 if unit.startswith("month") else 365)
                cur = (vals.get(metric) or {}).get("value")
                sv = float(cur if start is None and cur is not None else (start or 0))
                gid = conn.execute("INSERT INTO hub_goals (area, title, emoji, metric, direction, start_value, target, unit, deadline, why, sort) "
                                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (area, title, emoji, metric, direction, sv, target, METRICS[metric][2],
                                                                      (today + dt.timedelta(days=days)).isoformat(), why, i)).lastrowid
                for j, t in enumerate(tasks):
                    conn.execute("INSERT INTO hub_tasks (goal_id, title, sort) VALUES (?,?,?)", (gid, t, j))
        db.meta_set("hub_seeded", "1")


# ------------------------------------------------------------------ live metrics
_mcache: dict[str, Any] = {}


def metric_values(max_age: float = 420) -> dict[str, dict[str, Any]]:
    hit = _mcache.get("v")
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    out: dict[str, dict[str, Any]] = {}

    def put(k: str, v: Any) -> None:
        if v is not None:
            out[k] = {"value": round(float(v), 2), "label": METRICS[k][1], "unit": METRICS[k][2], "area": METRICS[k][0]}

    try:
        with db.get_conn() as conn:
            live = [dict(r) for r in conn.execute("SELECT balance, starting_balance, profit_target, eval_status, account_type FROM accounts WHERE COALESCE(is_archived,0)=0").fetchall()]
            passed = conn.execute("SELECT (SELECT COUNT(*) FROM accounts WHERE eval_status='passed') + (SELECT COUNT(*) FROM accounts_archive WHERE eval_status='passed')").fetchone()[0]
            since = (dt.date.today() - dt.timedelta(days=30)).isoformat()
            wr = conn.execute("SELECT COUNT(*), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) FROM (SELECT pnl, exit_time FROM account_trades UNION ALL "
                              "SELECT pnl, exit_time FROM account_trades_archive) WHERE substr(exit_time,1,10) >= ?", (since,)).fetchone()
        put("nostro.passed", passed)
        evals = [a for a in live if a["eval_status"] == "in_progress" and a["profit_target"]]
        put("nostro.best_eval_pct", max([max(0.0, 100 * (a["balance"] - a["starting_balance"]) / a["profit_target"]) for a in evals] or [0]))
        put("nostro.withdrawable", sum(max(0.0, a["balance"] - a["starting_balance"]) for a in live if a["eval_status"] == "passed"))
        put("nostro.net_pnl", sum(a["balance"] - a["starting_balance"] for a in live))
        if wr and wr[0]:
            put("nostro.win_rate_30d", 100 * (wr[1] or 0) / wr[0])
    except Exception:  # noqa: BLE001
        pass
    try:
        fx = core.usdils()
        pf = core._portfolio()
        hold = [h for h in pf.get("holdings", []) if h.get("value")]
        tot = sum(h["value"] for h in hold)
        put("stocks.value_ils", tot * fx)
        put("stocks.pnl_ils", sum(h.get("pnl") or 0 for h in hold) * fx)
        put("stocks.top_weight", 100 * max(h["value"] for h in hold) / tot if tot else None)
        put("stocks.holdings", len(hold))
    except Exception:  # noqa: BLE001
        pass
    try:
        nw = core.networth(600)
        put("wealth.networth_ils", nw["total_ils"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.api import wealth_routes
        from app.wealth import insights

        o = wealth_routes._s("outlook", 120, insights.outlook)
        put("wealth.runway", o.get("runway_months") or 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.wealth import plan

        put("wealth.savings_ils", plan.goals()["total_saved"])
        put("business.revenue", plan.revenue()["total"])
        put("business.streak", plan.overview()["streak"])
    except Exception:  # noqa: BLE001
        pass
    _mcache["v"] = (time.time(), out)
    return out


# ------------------------------------------------------------------ overview
def _progress(g: dict[str, Any], value: float | None) -> float:
    if value is None:
        return 0.0
    s, t = float(g["start_value"]), float(g["target"])
    if g["direction"] == "down":
        span = s - t
        return 100.0 if value <= t else (0.0 if span <= 0 else max(0.0, min(100.0, 100 * (s - value) / span)))
    span = t - s
    return 100.0 if value >= t else (0.0 if span <= 0 else max(0.0, min(100.0, 100 * (value - s) / span)))


def overview(area: str | None = None) -> dict[str, Any]:
    ensure()
    vals = metric_values()
    today = _today()
    with db.read_conn() as conn:  # reads never queue behind the trainer's writes
        goals = [dict(r) for r in conn.execute("SELECT * FROM hub_goals WHERE archived=0 ORDER BY sort, id").fetchall()]
        tasks = [dict(r) for r in conn.execute("SELECT * FROM hub_tasks ORDER BY goal_id, sort, id").fetchall()]
        logs = [dict(r) for r in conn.execute("SELECT * FROM hub_log WHERE day >= ? ORDER BY day", ((today - dt.timedelta(days=180)).isoformat(),)).fetchall()]
        done_goals = conn.execute("SELECT COUNT(*) FROM hub_goals WHERE reached_at IS NOT NULL").fetchone()[0]
    # one value snapshot per goal per day (history line + pace) — written only when today's value is new or changed
    logged = {(l["goal_id"], l["day"]): l["value"] for l in logs}
    writes = []
    for g in goals:
        v = (vals.get(g["metric"]) or {}).get("value") if g["metric"] else g["manual_value"]
        if v is not None:
            g["_v"] = v
            if logged.get((g["id"], today.isoformat())) != v:
                writes.append((g["id"], today.isoformat(), v))
    if writes:
        with db.get_conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO hub_log (goal_id, day, value) VALUES (?,?,?)", writes)
    out = []
    for g in goals:
        v = g.get("_v")
        pct = _progress(g, v)
        if pct >= 100 and not g["reached_at"]:
            with db.get_conn() as conn:
                conn.execute("UPDATE hub_goals SET reached_at=? WHERE id=?", (today.isoformat(), g["id"]))
            g["reached_at"] = today.isoformat()
        gl = [l for l in logs if l["goal_id"] == g["id"]]
        if v is not None and (not gl or gl[-1]["day"] != today.isoformat()):
            gl.append({"day": today.isoformat(), "value": v})
        pace, eta = None, None
        recent = [l for l in gl if l["day"] >= (today - dt.timedelta(days=30)).isoformat()]
        if len(recent) >= 2:
            d0 = dt.date.fromisoformat(recent[0]["day"])
            days = max(1, (today - d0).days)
            pace = (recent[-1]["value"] - recent[0]["value"]) / days
            rem = (g["target"] - v) if g["direction"] == "up" else (v - g["target"])
            if v is not None and rem > 0 and ((pace > 0 and g["direction"] == "up") or (pace < 0 and g["direction"] == "down")):
                eta = (today + dt.timedelta(days=int(rem / abs(pace)))).isoformat()
        created = dt.date.fromisoformat(str(g["created_at"])[:10])
        dl = dt.date.fromisoformat(g["deadline"]) if g.get("deadline") else None
        expected = round(100 * min(1.0, max(0.0, (today - created).days / max(1, (dl - created).days))), 1) if dl and dl > created else None
        gt = [t for t in tasks if t["goal_id"] == g["id"]]
        status = "reached" if pct >= 100 else ("no data" if v is None else "behind" if expected is not None and pct + 5 < expected else "on track")
        out.append({**{k: g[k] for k in ("id", "area", "title", "emoji", "metric", "direction", "start_value", "target", "unit", "deadline", "why", "reached_at", "manual_value")},
                    "value": v, "pct": round(pct, 1), "expected_pct": expected, "status": status, "pace_per_day": pace, "eta": eta,
                    "days_left": (dl - today).days if dl else None, "metric_label": METRICS.get(g["metric"] or "", (None, "set by hand", None))[1],
                    "milestones": [m for m in (25, 50, 75, 100) if pct >= m], "tasks": gt, "tasks_done": sum(1 for t in gt if t["done_at"]),
                    "next_task": next((t for t in gt if not t["done_at"]), None), "history": [{"day": l["day"], "value": l["value"]} for l in gl][-90:],
                    "color": AREAS.get(g["area"], ("", "#ffa028"))[1]})
    tasks_done = sum(1 for t in tasks if t["done_at"])
    week = (today - dt.timedelta(days=today.weekday())).isoformat()
    xp = 10 * tasks_done + 100 * done_goals
    level = 1 + int(math.sqrt(xp / 60))
    lo, hi = 60 * (level - 1) ** 2, 60 * level ** 2
    shown = [g for g in out if not area or g["area"] == area]
    focus = min((g for g in out if g["status"] != "reached" and g["next_task"]), key=lambda g: (g["status"] != "behind", -(g["expected_pct"] or 0) + g["pct"]), default=None)
    return {"goals": shown, "areas": {k: {"label": v[0], "color": v[1], "n": sum(1 for g in out if g["area"] == k)} for k, v in AREAS.items()},
            "metrics": vals, "metric_catalog": {k: {"area": v[0], "label": v[1], "unit": v[2], "value": (vals.get(k) or {}).get("value")} for k, v in METRICS.items()},
            "player": {"xp": xp, "level": level, "level_from": lo, "level_to": hi, "tasks_done": tasks_done, "goals_reached": done_goals,
                       "tasks_this_week": sum(1 for t in tasks if t["done_at"] and t["done_at"] >= week), "active": sum(1 for g in out if g["status"] != "reached")},
            "focus": {"goal_id": focus["id"], "title": focus["title"], "task": focus["next_task"]} if focus else None}


# ------------------------------------------------------------------ edits
def save_goal(g: dict[str, Any]) -> dict[str, Any]:
    ensure()
    metric = g.get("metric") or None
    if metric and metric not in METRICS:
        raise ValueError("unknown metric")
    unit = METRICS[metric][2] if metric else (g.get("unit") or "")
    with db.get_conn() as conn:
        if g.get("id"):
            conn.execute("UPDATE hub_goals SET area=?, title=?, emoji=?, target=?, deadline=?, why=?, direction=? WHERE id=?",
                         (g["area"], g["title"], g.get("emoji"), float(g["target"]), g.get("deadline"), g.get("why"), g.get("direction") or "up", int(g["id"])))
            gid = int(g["id"])
        else:
            start = g.get("start_value")
            if start is None:
                start = (metric_values(0).get(metric) or {}).get("value", 0.0) if metric else (g.get("manual_value") or 0.0)
            gid = conn.execute("INSERT INTO hub_goals (area, title, emoji, metric, direction, start_value, target, unit, deadline, why, manual_value, sort) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?,?,999)", (g["area"], g["title"], g.get("emoji"), metric, g.get("direction") or "up", float(start or 0),
                                                                      float(g["target"]), unit, g.get("deadline"), g.get("why"), g.get("manual_value"))).lastrowid
            for j, t in enumerate(g.get("tasks") or []):
                if str(t).strip():
                    conn.execute("INSERT INTO hub_tasks (goal_id, title, sort) VALUES (?,?,?)", (gid, str(t).strip()[:140], j))
    return {"id": gid}


def delete_goal(gid: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE hub_goals SET archived=1 WHERE id=?", (gid,))


def set_value(gid: int, value: float) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE hub_goals SET manual_value=? WHERE id=? AND metric IS NULL", (float(value), gid))


def add_task(gid: int, title: str) -> dict[str, Any]:
    ensure()
    with db.get_conn() as conn:
        tid = conn.execute("INSERT INTO hub_tasks (goal_id, title, sort) VALUES (?,?,999)", (gid, title.strip()[:140])).lastrowid
    return {"id": tid}


def toggle_task(tid: int) -> dict[str, Any]:
    with db.get_conn() as conn:
        r = conn.execute("SELECT done_at FROM hub_tasks WHERE id=?", (tid,)).fetchone()
        if not r:
            raise ValueError("no such task")
        done = None if r["done_at"] else dt.datetime.now(core.IL).date().isoformat()
        conn.execute("UPDATE hub_tasks SET done_at=? WHERE id=?", (done, tid))
    return {"id": tid, "done": bool(done)}


def delete_task(tid: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM hub_tasks WHERE id=?", (tid,))
