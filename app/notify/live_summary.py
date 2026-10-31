"""LIVE TODAY SUMMARY on Telegram — one message per day that edits itself every 20 seconds.

Shows: NOSTRO (today's net = closed + open P&L, equity, trades W/L, open trades, best / worst account, futures session),
the real Polymarket engine, the stock portfolio's day move, Shopify today and the ₪1M daily-plan progress.

A new message is sent the first time each day (Israel date) and then edited in place (edits don't buzz the phone). If the
user deletes it, the next tick sends a fresh one. Unchanged text is not re-sent. Settings in meta 'live_summary':
{"on": true, "every": 20}. Telegram: /summary sends a fresh message now, /summary off | on switches it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import json
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app import db
from app.notify import telegram_bot

logger = logging.getLogger(__name__)
IL, NY = ZoneInfo("Asia/Jerusalem"), ZoneInfo("America/New_York")
_state: dict[str, Any] = {"last_text": None, "last_error": None}


def settings() -> dict[str, Any]:
    try:
        s = json.loads(db.meta_get("live_summary") or "{}")
    except ValueError:
        s = {}
    return {"on": True, "every": 20, **s}


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    s = {**settings(), **{k: v for k, v in patch.items() if k in ("on", "every")}}
    s["every"] = max(15, min(300, int(s["every"])))
    db.meta_set("live_summary", json.dumps(s))
    return s


def _m(v: float | None, d: int = 0) -> str:
    if v is None:
        return "—"
    return f"{'+' if v > 0 else '−' if v < 0 else ''}${abs(v):,.{d}f}"


def _session(now_ny: dt.datetime) -> str:
    wd, h = now_ny.weekday(), now_ny.hour
    if wd == 5 or (wd == 6 and h < 18) or (wd == 4 and h >= 17):
        return "⚪ futures closed (weekend)"
    if h == 17:
        return "🟡 futures daily break (17–18 NY)"
    if 9 <= h < 16 and not (h == 9 and now_ny.minute < 30):
        return "🟢 NY cash session"
    return "🟢 futures open (overnight)"


def build_text() -> str:
    now_il, now_ny = dt.datetime.now(IL), dt.datetime.now(NY)
    L = [f"📊 <b>TODAY</b> · {now_il.strftime('%a %d %b')} · {now_il.strftime('%H:%M')} IL / {now_ny.strftime('%H:%M')} NY", "━━━━━━━━━━━━━━━"]
    try:
        from app.accounts import page

        d = page.build()
        t, cards = d["totals"], d["accounts"]
        wins = sum(c["today_wins"] for c in cards)
        live = [c for c in cards if c["status"] == "live"]
        L.append(f"🌾 <b>NOSTRO</b> · {len(live)} live accounts")
        L.append(f"Today net: <b>{_m(t['today_net'])}</b>  (closed {_m(t['today_pnl'])} · open {_m(t['open_pnl'])})")
        L.append(f"Equity {_m(t['equity']).lstrip('+')} · total net {_m(t['net_pnl'])}")
        L.append(f"Trades today: {t['today_trades']} ({wins}W/{t['today_trades'] - wins}L) · open trades {t['open_positions']}")
        ranked = sorted(cards, key=lambda c: c["today_net"])
        if ranked and (ranked[0]["today_net"] or ranked[-1]["today_net"]):
            short = lambda c: html.escape(c["name"].split(" (was")[0][:26])  # noqa: E731
            L.append(f"Best: {short(ranked[-1])} {_m(ranked[-1]['today_net'])} · Worst: {short(ranked[0])} {_m(ranked[0]['today_net'])}")
        if t.get("passed"):
            L.append(f"🏆 Passed: {t['passed']} · withdrawable {_m(t['withdrawable']).lstrip('+')}")
        L.append(_session(now_ny))
    except Exception as e:  # noqa: BLE001
        L.append(f"🌾 NOSTRO: unavailable ({html.escape(str(e)[:60])})")
    L.append("")
    try:
        from app.polymarket import real_trading as rt

        s = rt.status(check_balance=False) or {}
        td = s.get("today") or {}
        armed = (s.get("settings") or {}).get("armed")
        bal = s.get("balance")
        if bal is None:
            from app.wealth import core

            bal = core._polymarket()  # cached for 10 minutes while the engine is disarmed
        L.append(f"🎲 Polymarket real: {'🟢 ON' if armed else '⛔ OFF'} · bal {'$%.2f' % bal if bal is not None else '—'} · today {_m(td.get('pnl'), 2)} ({td.get('trades', 0)} trades)")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.wealth import core

        nw = core.networth(300)
        fx = nw["usdils"]
        stocks = sum(c["usd"] for c in nw["by_category"] if c["category"] in ("stocks", "crypto"))
        dp = nw.get("day_pnl_usd") or 0
        L.append(f"📈 Portfolio ₪{stocks * fx:,.0f} · today {'+' if dp >= 0 else '−'}₪{abs(dp) * fx:,.0f} · net worth ₪{nw['total_ils']:,.0f}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.wealth import shopify

        if shopify.status()["connected"]:
            sm = shopify.summary(30)
            if sm.get("today"):
                L.append(f"🛍️ Shopify today {sm['today']['net']:,.0f} {sm.get('currency') or ''} · {sm['today']['orders']} orders · month {sm['mtd']['net']:,.0f}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.wealth import plan

        ds = plan.day_stats(plan._today())
        L.append(f"🎯 Daily plan {ds['done']}/{ds['total']} tasks ({ds['pct']}%)")
    except Exception:  # noqa: BLE001
        pass
    L.append(f"\n⏱ updated {now_il.strftime('%H:%M:%S')} · every {settings()['every']}s")
    return "\n".join(L)


def _api(method: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    cfg = telegram_bot._config()
    if not cfg:
        return 0, {}
    token, chat_id = cfg
    r = requests.post(telegram_bot.API_URL.format(token=token, method=method), data={"chat_id": chat_id, **data}, timeout=15)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def tick(force_new: bool = False) -> None:
    if not telegram_bot.is_configured():
        return
    day = dt.datetime.now(IL).date().isoformat()
    key = f"live_summary_msg_{day}"
    text = build_text()
    mid = db.meta_get(key)
    if mid and not force_new:
        body = text.rsplit("\n⏱", 1)[0]
        if body == (_state["last_text"] or "").rsplit("\n⏱", 1)[0] and time.time() - _state.get("last_edit", 0) < 60:
            return  # numbers unchanged: skip the edit (the clock line alone refreshes once a minute)
        code, j = _api("editMessageText", {"message_id": int(mid), "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"})
        if code == 200 or "not modified" in str(j.get("description", "")):
            _state.update(last_text=text, last_edit=time.time(), last_error=None)
            return
        if "not found" not in str(j.get("description", "")) and "can't be edited" not in str(j.get("description", "")):
            _state["last_error"] = j.get("description")
            return
    code, j = _api("sendMessage", {"text": text, "parse_mode": "HTML", "disable_web_page_preview": "true", "disable_notification": "true"})
    if code == 200:
        db.meta_set(key, str(j["result"]["message_id"]))
        _state.update(last_text=text, last_edit=time.time(), last_error=None)
    else:
        _state["last_error"] = j.get("description")


def handle_command(text: str) -> str | None:
    t = (text or "").strip().lower()
    if not t.startswith("/summary"):
        return None
    arg = t.split(maxsplit=1)[1] if " " in t else ""
    if arg == "off":
        save_settings({"on": False})
        return "Live summary paused. Send /summary on to resume."
    if arg == "on":
        save_settings({"on": True})
    tick(force_new=True)
    return None


async def summary_loop() -> None:
    await asyncio.sleep(45)
    while True:
        s = settings()
        if s["on"]:
            try:
                await asyncio.to_thread(tick)
            except Exception as e:  # noqa: BLE001
                _state["last_error"] = str(e)
                logger.exception("live summary tick failed")
        await asyncio.sleep(s["every"])
