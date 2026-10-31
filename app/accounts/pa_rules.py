"""Apex PA (funded account) rulebook, checked live against the terminal's own trade record for the linked real account.

What the terminal does for the real PA account, and how:
  * it never leaves a position open past the close: the engine flattens everything before 16:59 New York (23:59 Israel);
  * it stops opening new trades for the day once the realised daily profit reaches the daily target (account.daily_profit_limit, $300);
  * it tracks the trailing drawdown threshold (apex_rules) and ends the account's trading if the threshold is touched;
  * every protective order goes out with a stop and a target;
  * this module measures the payout rules from the account's closed trades: qualifying days, the 50% consistency rule, the safety net,
    the minimum payout and the payout count, so you can see at any time whether a withdrawal request would be accepted.

The numbers below come from the rules the owner pasted (weekly requests, 5 qualifying days, 50% consistency, safety net = start + drawdown + $100,
minimum $500, 6 payouts) plus values that were NOT in that text and are therefore ASSUMED (marked "assumed"): the 50K trailing drawdown and the
minimum profit for a qualifying day. They are conservative guesses; confirm them with Apex and edit RULES.
The terminal reports from its own records (and from the balance read off TradersPost); Apex's own dashboard is the final authority.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

from app import db

NY = ZoneInfo("America/New_York")
RULES: dict[str, Any] = {
    "start": 50_000.0,
    "trailing_drawdown": 2_500.0,      # assumed: Apex 50K PA trailing drawdown
    "safety_buffer": 100.0,            # from the owner's text
    "min_qualifying_day_profit": 150.0,  # assumed: not in the text (25K = $100, 100K = $250 are); 50K is between
    "qualifying_days": 5,              # from the owner's text
    "consistency_max_share": 0.5,      # from the owner's text: no single day may be 50% or more of the profit since the last payout
    "min_payout": 500.0,               # from the owner's text
    "max_payouts": 6,                  # from the owner's text
    "daily_target": 300.0,             # the owner's choice for this terminal
}
ASSUMED = ("trailing_drawdown", "min_qualifying_day_profit")


def _day(exit_time: str) -> dt.date | None:
    try:
        t = dt.datetime.fromisoformat(str(exit_time))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(NY).date()


def _since(account_id: int) -> dt.date | None:
    raw = db.meta_get(f"pa_last_payout_{account_id}")
    try:
        return dt.date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def record_payout(account_id: int, on: dt.date | None = None) -> None:
    """Call when a withdrawal has been paid: the 5-day count and the consistency window start again, and the payout count goes up."""
    db.meta_set(f"pa_last_payout_{account_id}", (on or dt.datetime.now(NY).date()).isoformat())
    n = int(db.meta_get(f"pa_payouts_{account_id}", "0") or 0)
    db.meta_set(f"pa_payouts_{account_id}", str(n + 1))


def status(account: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Live rule check for the linked real account (or the one given). None when no account is linked."""
    acct = account or next((a for a in db.list_accounts(include_archived=False) if a.get("traderspost_linked")), None)
    if not acct:
        return None
    R = RULES
    since = _since(acct["id"])
    trades = db.get_account_trades(acct["id"])
    days: dict[dt.date, float] = defaultdict(float)
    for t in trades:
        d = _day(t.get("exit_time"))
        if d and (since is None or d > since):
            days[d] += float(t.get("pnl") or 0.0)
    total = sum(days.values())
    best_day, best = (max(days.items(), key=lambda kv: kv[1]) if days else (None, 0.0))
    qual = sum(1 for v in days.values() if v >= R["min_qualifying_day_profit"])
    share = (best / total) if total > 0 else None
    bal = float(acct["balance"])
    safety = R["start"] + R["trailing_drawdown"] + R["safety_buffer"]
    above = bal - safety
    floor = acct.get("drawdown_floor")
    payouts = int(db.meta_get(f"pa_payouts_{acct['id']}", "0") or 0)
    today = dt.datetime.now(NY).date()
    today_pnl = days.get(today, 0.0)
    checks = [
        {"rule": "Flat before the close (16:59 New York)", "state": "enforced", "detail": "the engine closes every position before 23:59 Israel time"},
        {"rule": f"Daily profit target ${R['daily_target']:.0f}", "state": "enforced",
         "detail": f"today ${today_pnl:+,.2f}" + (" - target reached, no new trades today" if today_pnl >= R["daily_target"] else "")},
        {"rule": "Trailing drawdown", "state": "enforced" if floor is not None else "pending",
         "detail": f"threshold ${floor:,.2f}, buffer ${bal - floor:,.2f}" if floor is not None else "starts with the first trade"},
        {"rule": f"{R['qualifying_days']} qualifying days (>= ${R['min_qualifying_day_profit']:.0f}, assumed)", "state": "ok" if qual >= R["qualifying_days"] else "pending", "detail": f"{qual} of {R['qualifying_days']}"},
        {"rule": f"Consistency: best day under {R['consistency_max_share']:.0%} of profit", "state": ("pending" if share is None else "ok" if share < R["consistency_max_share"] else "violated"),
         "detail": ("no profit yet" if share is None else f"best day ${best:,.2f} ({best_day}) = {share:.0%} of ${total:,.2f}")},
        {"rule": f"Safety net: balance above ${safety:,.0f}", "state": "ok" if above > 0 else "pending", "detail": f"balance ${bal:,.2f}, {'above' if above > 0 else 'below'} by ${abs(above):,.2f}"},
        {"rule": f"Minimum payout ${R['min_payout']:.0f}", "state": "ok" if above >= R["min_payout"] else "pending", "detail": f"available above the safety net: ${max(0.0, above):,.2f}"},
        {"rule": f"Payouts used (max {R['max_payouts']})", "state": "ok" if payouts < R["max_payouts"] else "violated", "detail": f"{payouts} of {R['max_payouts']}"},
    ]
    eligible = qual >= R["qualifying_days"] and (share is not None and share < R["consistency_max_share"]) and above >= R["min_payout"] and payouts < R["max_payouts"]
    return {"account": {"id": acct["id"], "name": acct["name"], "balance": bal}, "checks": checks, "eligible_now": eligible,
            "assumed": {k: R[k] for k in ASSUMED}, "since": since.isoformat() if since else None,
            "days": {d.isoformat(): round(v, 2) for d, v in sorted(days.items())[-10:]}}


def status_text() -> str:
    s = status()
    if not s:
        return "No account is linked to TradersPost."
    icon = {"ok": "OK", "enforced": "ON", "pending": "..", "violated": "!!"}
    lines = [f"APEX PA RULES - {s['account']['name']}  balance ${s['account']['balance']:,.2f}"]
    lines += [f"[{icon[c['state']]}] {c['rule']}: {c['detail']}" for c in s["checks"]]
    lines.append("Payout request would be accepted now." if s["eligible_now"] else "Not yet eligible for a payout request.")
    lines.append("Assumed values (confirm with Apex): " + ", ".join(f"{k}={v:,.0f}" for k, v in s["assumed"].items()))
    return "\n".join(lines)
