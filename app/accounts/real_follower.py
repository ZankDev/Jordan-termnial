"""Automatic follower for the REAL-money account (the one linked to TradersPost).

The trainer keeps hunting for strategies; this moves the real account onto a clearly better one without anyone clicking. It is
deliberately stricter than the AUTO paper account (auto_account.py) because real orders follow it.

A candidate (confirmed slots + the deep search's remembered elites) must:
  * clear the long-history gate (long_gate.check: 150+ trades, PF 1.3+, 80% of years profitable, drawdown, newest 6 months > 0);
  * pass the Apex 50K Monte Carlo over that long history at the base risk: pass >= 75%, breach <= 10%;
  * beat the strategy the account runs now by >= 25% on (pass - breach), or the current one no longer clears the gate.
A switch happens only when the account is flat, outside regular trading hours (Mon-Fri 09:20-16:10 New York), its evaluation is still
in progress, drawdown is under 60% of the limit, the day is not near the daily loss limit, and the last switch was >= 7 days ago.
Risk per trade and the TradersPost link are NEVER touched. The old strategy's trades stay in the account's history.

Control (meta key `real_auto_follow`): "1" on, "0" off, "dry" log what it would do, unset = dry-run for the first 24 hours, then on.
Telegram: /realauto on|off|dry|status.   Every decision is stored in real_follow_log and sent to Telegram when something changes.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo

from app import db

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

CHECK_EVERY_SECONDS = 6 * 3600
MIN_DAYS_BETWEEN_SWITCHES = 7
MIN_PASS, MAX_BREACH = 0.75, 0.10
BEAT_BY = 1.25
MAX_DD_FRACTION = 0.60
DRY_RUN_HOURS = 24
SCHEMA = ("CREATE TABLE IF NOT EXISTS real_follow_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')), "
          "account_id INTEGER, action TEXT, old_strategy TEXT, new_strategy TEXT, symbol TEXT, detail TEXT)")


def _log(account_id: int | None, action: str, old: str | None = None, new: str | None = None, symbol: str | None = None, detail: Any = None) -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)
        conn.execute("INSERT INTO real_follow_log (account_id, action, old_strategy, new_strategy, symbol, detail) VALUES (?,?,?,?,?,?)",
                     (account_id, action, old, new, symbol, json.dumps(detail, default=str)[:2000] if detail is not None else None))


def recent_log(limit: int = 10) -> list[dict[str, Any]]:
    try:
        with db.get_conn() as conn:
            conn.execute(SCHEMA)
            return [dict(r) for r in conn.execute("SELECT * FROM real_follow_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    except Exception:  # noqa: BLE001
        return []


def mode() -> str:
    """'on', 'off' or 'dry' (the effective mode right now)."""
    v = db.meta_get("real_auto_follow")
    if v in ("1", "on"):
        return "on"
    if v in ("0", "off"):
        return "off"
    if v == "dry":
        return "dry"
    since = db.meta_get("real_follow_since")
    if not since:
        db.meta_set("real_follow_since", str(time.time()))
        return "dry"
    return "on" if time.time() - float(since) >= DRY_RUN_HOURS * 3600 else "dry"


def set_mode(m: str) -> str:
    m = {"on": "1", "1": "1", "off": "0", "0": "0", "dry": "dry"}[m.lower()]
    db.meta_set("real_auto_follow", m)
    return mode()


def _linked() -> dict[str, Any] | None:
    return next((a for a in db.list_accounts(include_archived=False) if a.get("traderspost_linked")), None)


def _edge(symbol: str, strategy: str, params: dict[str, Any]) -> dict[str, Any]:
    """Long-history gate + Apex Monte Carlo at the base risk. {'ok', 'edge', 'why', ...}."""
    import numpy as np

    from app.accounts import portfolio
    from app.backtest.engine import run_backtest
    from app.backtest.sizing import default_config
    from app.optimizer import long_gate, promotion
    from app.strategies import registry

    lg = long_gate.check(symbol, strategy, params)
    if not lg["judged"] or not lg["passes"]:
        return {"ok": False, "edge": -1.0, "why": lg["why"]}
    months = lg["record"]["months"]
    df = promotion._deep_df(symbol, months)
    trades = run_backtest(df, registry.get(strategy), params, symbol, 50_000.0, sizing=default_config(50_000.0)).trades
    days = portfolio._daily(trades)
    arr = np.array([days[d] for d in sorted(days)]) if days else np.array([])
    if len(arr) < 60:
        return {"ok": False, "edge": -1.0, "why": f"only {len(arr)} trading days"}
    res = portfolio._sim(arr, np.random.default_rng(11))
    p, b, med = res[1.0] if 1.0 in res else res[max(res)]
    ok = p >= MIN_PASS and b <= MAX_BREACH
    return {"ok": ok, "edge": round(p - b, 3), "pass": round(p, 3), "breach": round(b, 3), "median_days": med, "long": lg["record"],
            "why": f"Apex 50K Monte Carlo pass {p:.0%}, breach {b:.0%}" + ("" if ok else f" (need pass >= {MIN_PASS:.0%}, breach <= {MAX_BREACH:.0%})")}


def _candidates() -> list[dict[str, Any]]:
    from app.optimizer.search import params_hash

    out, seen = [], set()

    def add(sym: str, strat: str, params: dict[str, Any], src: str) -> None:
        h = (sym, strat, params_hash(params))
        if h not in seen:
            seen.add(h)
            out.append({"symbol": sym, "strategy": strat, "params": params, "src": src})

    for sym in ("ES", "NQ"):
        slot = db.get_slot(sym, "confirmed")
        if slot:
            add(sym, slot["strategy"], slot["params"], "confirmed")
        try:
            el = json.loads(db.meta_get(f"deep_elite_{sym}") or "{}")
        except ValueError:
            el = {}
        for strat, lst in el.items():
            for e in lst[:3]:
                add(sym, strat, e["params"], "deep")
    return out


def _guards(acct: dict[str, Any]) -> str | None:
    """None when a switch is allowed right now, else the reason it is not."""
    if acct.get("open_position") is not None:
        return "position open"
    if acct.get("eval_status") not in (None, "in_progress"):
        return f"evaluation is {acct.get('eval_status')}"
    now = dt.datetime.now(NY)
    if now.weekday() < 5 and dt.time(9, 20) <= now.time() <= dt.time(16, 10):
        return "regular trading hours"
    dd_limit = acct.get("max_drawdown") or 0
    if dd_limit and acct.get("peak_equity") is not None and (acct["peak_equity"] - acct["balance"]) > MAX_DD_FRACTION * dd_limit:
        return "drawdown above 60% of the limit"
    dll = acct.get("daily_loss_limit") or 0
    if dll and (acct.get("daily_realized_pnl") or 0) <= -0.5 * dll:
        return "daily loss limit nearly used"
    with db.get_conn() as conn:
        conn.execute(SCHEMA)
        last = conn.execute("SELECT ts FROM real_follow_log WHERE account_id = ? AND action = 'switch' ORDER BY id DESC LIMIT 1", (acct["id"],)).fetchone()
    if last:
        t = dt.datetime.strptime(last["ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        if (dt.datetime.now(dt.timezone.utc) - t).days < MIN_DAYS_BETWEEN_SWITCHES:
            return f"last switch less than {MIN_DAYS_BETWEEN_SWITCHES} days ago"
    return None


def evaluate() -> dict[str, Any]:
    """One full decision pass (also used by /realauto status and the dry-run). Never changes anything."""
    acct = _linked()
    if not acct:
        return {"action": "none", "switch": False, "why": "no account is linked to TradersPost"}
    cur_params = json.loads(acct["strategy_params_json"]) if acct.get("strategy_params_json") else {}
    cur = _edge(acct["symbol"], acct["strategy"], cur_params) if acct.get("strategy") and acct.get("symbol") else None
    best = None
    for c in _candidates():
        if c["symbol"] == acct.get("symbol") and c["strategy"] == acct.get("strategy") and json.dumps(c["params"], sort_keys=True) == json.dumps(cur_params, sort_keys=True):
            continue
        e = _edge(c["symbol"], c["strategy"], c["params"])
        if e["ok"] and (best is None or e["edge"] > best["edge"]):
            best = {**c, **e}
    cur_ok = bool(cur and cur["ok"])
    better = best is not None and (not cur_ok or best["edge"] >= (cur["edge"] if cur else 0) * BEAT_BY)
    return {"account": acct, "current": cur, "best": best, "switch": bool(better), "action": "switch" if better else "keep",
            "why": (f"{best['strategy']} {best['symbol']}: {best['why']}" if better else
                    (f"current strategy still the best ({cur['why']})" if cur_ok else "no candidate clears every gate"))}


def _tell(text: str, kind: str) -> None:
    try:
        from app.notify import ops_alerts

        ops_alerts.alert(f"real_follow_{kind}", text, cooldown=0)
    except Exception:  # noqa: BLE001
        logger.exception("real_follower: telegram failed")


def tick() -> None:
    """Called from the live loop; does real work at most every CHECK_EVERY_SECONDS, and only when a switch would be allowed."""
    m = mode()
    if m == "off":
        return
    if time.time() - float(db.meta_get("real_follow_checked_at", "0") or 0) < CHECK_EVERY_SECONDS:
        return
    acct = _linked()
    if not acct or _guards(acct):
        return
    db.meta_set("real_follow_checked_at", str(time.time()))
    d = evaluate()
    old = acct.get("strategy")
    if d["action"] != "switch":
        _log(acct["id"], "keep", old, old, acct.get("symbol"), d["why"])
        return
    best = d["best"]
    if m == "dry":
        _log(acct["id"], "would_switch", old, best["strategy"], best["symbol"], d["why"])
        _tell(f"REAL FOLLOWER (dry run): would move the real account from {old} {acct.get('symbol')} to {best['strategy']} {best['symbol']}. "
              f"{best['why']}. Send /realauto on to let it switch by itself.", "dry")
        return
    from app.accounts import ledger

    _tell(f"REAL FOLLOWER: moving the real account from {old} {acct.get('symbol')} to {best['strategy']} {best['symbol']} now "
          f"(flat, outside trading hours). {best['why']}.", "pre")
    ledger.assign_strategy(acct["id"], best["strategy"], best["symbol"], best["params"])
    _log(acct["id"], "switch", old, best["strategy"], best["symbol"], d["why"])
    _tell(f"REAL FOLLOWER: done. The real account now runs {best['strategy']} on {best['symbol']}; risk per trade and the TradersPost link "
          "are unchanged. Check the first order at the broker.", "post")


def status_text() -> str:
    acct = _linked()
    lines = [f"Real follower: {mode().upper()} (control with /realauto on|off|dry)"]
    if acct:
        lines.append(f"Account: {acct['name']} runs {acct.get('strategy')} {acct.get('symbol')}")
    for r in recent_log(4):
        lines.append(f"{r['ts'][:16]} {r['action']} {r.get('new_strategy') or ''}: {(r.get('detail') or '')[:90]}")
    return "\n".join(lines)
