"""WEALTH background loop (every 5 minutes):
  * daily net-worth snapshot (after 23:00 Israel time, or first run of the day if missed)
  * daily database backup (security.backup_if_due)
  * deals refresh every 6 h + Telegram note for new listings that match the saved deal filter
  * risk watch: net worth falls more than daily_loss_alert_pct vs yesterday, or the real-money trading day loses more than
    real_daily_loss_usd -> Telegram alert (once per day each)
  * weekly report (Sunday 09:00 Israel) and monthly report (1st of the month 09:00) to Telegram
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import Any

from app import db
from app.wealth import core

logger = logging.getLogger(__name__)
_state: dict[str, Any] = {"last_error": None, "last_run": None}


def _once(key: str) -> bool:
    """True the first time `key` is seen (persisted), so each alert/report goes out once."""
    if db.meta_get(f"wealth_once_{key}"):
        return False
    db.meta_set(f"wealth_once_{key}", "1")
    return True


def _send(text: str) -> None:
    try:
        from app.notify import telegram_bot

        telegram_bot.send_text(text)
    except Exception:  # noqa: BLE001
        pass


def _fmt(usd: float, fx: float) -> str:
    return f"${usd:,.0f} (₪{usd * fx:,.0f})"


def report(kind: str = "weekly") -> str:
    nw = core.networth()
    fx = nw["usdils"]
    days = 7 if kind == "weekly" else 30
    ch = nw["change"].get("7d" if kind == "weekly" else "30d")
    L = [f"{'📅 WEEKLY' if kind == 'weekly' else '🗓 MONTHLY'} MONEY REPORT", "", f"Net worth: {_fmt(nw['total_usd'], fx)}"]
    if ch:
        L.append(f"Change {days}d: {'+' if ch['usd'] >= 0 else ''}{_fmt(ch['usd'], fx)} ({ch['pct']:+.2f}%)")
    L.append(f"Liquid: {_fmt(nw['liquid_usd'], fx)} · debts {_fmt(nw['debts_usd'], fx)}")
    L.append("Mix: " + ", ".join(f"{c['label']} {c['pct']:.0f}%" for c in nw["by_category"] if c["pct"] >= 3))
    top = [i for i in nw["items"] if i.get("pnl") is not None]
    if top:
        best = max(top, key=lambda i: i["pnl"]); worst = min(top, key=lambda i: i["pnl"])
        L.append(f"Best holding: {best['name']} {best['pnl']:+,.0f}$ · worst: {worst['name']} {worst['pnl']:+,.0f}$")
    try:
        rk = core.risk()
        bad = [w for w in rk["warnings"] if w["level"] in ("bad", "warn")]
        if bad:
            L.append("⚠ " + " | ".join(w["title"] for w in bad[:3]))
        if rk.get("market"):
            m = rk["market"]
            L.append(f"1-in-20 bad month for the portfolio: −${m['var95_month_usd']:,.0f} ({m['var95_month_pct']}%)")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.wealth import cashflow, shopify

        smtd = cashflow.stocks_mtd()
        L.append(f"Stocks this month: {'+' if smtd['pnl_usd'] >= 0 else ''}₪{smtd['pnl_ils']:,.0f}")
        mb = cashflow.main_balance()
        L.append(f"Main balance: ₪{mb['value']:,.0f}")
        sh = shopify.summary(30) if shopify.status()["connected"] else None
        if sh and sh.get("mtd"):
            L.append(f"Shopify this month: {sh['mtd']['net']:,.0f} {sh.get('currency') or ''} net · {sh['mtd']['orders']} orders")
    except Exception:  # noqa: BLE001
        pass
    if kind == "monthly":
        try:
            pf = core.performance()
            if pf.get("cost"):
                L.append(f"Portfolio vs S&P 500 (same dollars, same days): {pf['vs_spy']:+,.0f}$ · est. tax if sold: ${pf['tax']['estimated_usd']:,.0f}")
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.wealth import budget

            b = budget.summary(3)
            if b["count"]:
                L.append(f"Budget (6-mo avg): income ₪{b['avg_income']:,.0f} · spend ₪{b['avg_expense']:,.0f} · savings rate {b['savings_rate']}%")
        except Exception:  # noqa: BLE001
            pass
    try:
        rb = core.rebalance()
        drift = [r for r in rb["rows"] if r["alert"]]
        if drift:
            L.append("Rebalance: " + ", ".join(f"{r['label']} {r['drift']:+.0f}pts" for r in drift[:4]))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(L)


def _tick() -> None:
    from app.wealth import deals, security

    now = dt.datetime.now(core.IL)
    day = now.date().isoformat()
    security.backup_if_due()
    # recurring cash items (salary, card bills) and the monthly credit-card question
    try:
        from app.wealth import cashflow

        cashflow.seed_defaults()
        posted = cashflow.post_due()
        if posted:
            a = cashflow.main_balance()
            _send("🏦 Monthly items booked: " + ", ".join(posted) + f"\nMain balance: ₪{a['value']:,.0f}")
        cashflow.ask_cc_if_due()
    except Exception:  # noqa: BLE001
        logger.exception("cashflow tick failed")
    try:  # road to ₪1M: morning checklist + 21:00 reminder
        from app.wealth import plan

        plan.telegram_due(now)
    except Exception:  # noqa: BLE001
        logger.exception("plan telegram failed")
    hist = core.history(3)
    nw = None
    if not hist or hist[-1]["day"] != day:          # first snapshot of the day
        nw = core.snapshot()
    elif now.hour >= 23 and _once(f"close_{day}"):   # and the end-of-day value
        nw = core.snapshot()
    # risk watch
    st = core.settings()["risk"]
    try:
        nw = nw or core.networth()
        ch = nw["change"].get("1d")
        if ch and ch["pct"] is not None and ch["pct"] <= -abs(st["daily_loss_alert_pct"]) and _once(f"drop_{day}"):
            _send(f"⚠ Net worth down {ch['pct']:.1f}% today ({_fmt(ch['usd'], nw['usdils'])}). Now {_fmt(nw['total_usd'], nw['usdils'])}.")
        from app.polymarket import real_trading as rt

        t = (rt.status(check_balance=False) or {}).get("today") or {}
        if (t.get("pnl") or 0) <= -abs(st["real_daily_loss_usd"]) and _once(f"real_{day}"):
            _send(f"⚠ Real-money trading lost ${-t['pnl']:.2f} today (limit ${st['real_daily_loss_usd']:.0f}). Use the KILL SWITCH on WEALTH › RISK to stop everything.")
    except Exception:  # noqa: BLE001
        pass
    # deals
    try:
        r = deals.refresh()
        if not r.get("skipped") and r.get("new"):
            f = json.loads(db.meta_get("deals_filter") or "{}")
            if f.get("alert"):
                L = deals.listing(max_price=f.get("max_price"), region=f.get("region"), category=f.get("category"), q=f.get("q"))
                fresh = [d for d in L["deals"] if d["new"]][:8]
                if fresh:
                    _send("🏪 New businesses for sale matching your filter:\n" + "\n".join(
                        f"• {d['title'][:70]} — {'₪{:,.0f}'.format(d['price']) if d['price'] else 'price n/a'}{' · payback ' + str(d['payback_years']) + 'y' if d['payback_years'] else ''}\n  {d['url']}" for d in fresh))
    except Exception:  # noqa: BLE001
        logger.exception("deals refresh failed")
    # reports
    if now.weekday() == 6 and now.hour >= 9 and _once(f"weekly_{day}"):
        _send(report("weekly"))
    if now.day == 1 and now.hour >= 9 and _once(f"monthly_{day}"):
        _send(report("monthly"))


def status() -> dict[str, Any]:
    return dict(_state)


def _warm() -> None:
    try:
        from app.api import wealth_routes

        wealth_routes.warm()
    except Exception:  # noqa: BLE001
        logger.exception("wealth warm-up failed")


async def wealth_loop() -> None:
    await asyncio.sleep(20)
    await asyncio.to_thread(_warm)   # the WEALTH pages open instantly from here on
    await asyncio.sleep(70)
    while True:
        try:
            await asyncio.to_thread(_tick)
            await asyncio.to_thread(_warm)
            _state.update(last_error=None, last_run=dt.datetime.now().isoformat(timespec="seconds"))
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = str(e)
            logger.exception("wealth tick failed")
        await asyncio.sleep(300)
