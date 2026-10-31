"""Critical-event detection. Reads the merged news feeds, scores each headline, groups the same story across outlets, and pushes the important ones to Telegram.

How an alert is decided (all rules are visible below; nothing is a black box):
  * every headline is matched against keyword families (WAR / MACRO / MARKET stress / ENERGY), Hebrew and English;
  * a story is the same headline seen in several outlets (words overlap); `sources` = how many different outlets carry it;
  * severity 3 (pushed to Telegram) = a "hard" trigger word AND at least 2 outlets within 3 hours, or a market trigger (VIX / index shock);
  * severity 2 = a family match from one outlet, or two soft matches: shown in the app, not pushed;
  * the same story is pushed once (hash kept in the database), and pushes are rate-limited per hour so a news storm cannot flood the chat.
Market triggers read live prices: VIX above 30 or +20% on the day, S&P 500 futures moving 2% or more.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any

from app import db
from app.intel import feeds

logger = logging.getLogger(__name__)

HARD = {
    "war": ("declares war", "declared war", "invasion", "invades", "missile strike", "ballistic missile", "airstrikes", "air strikes", "nuclear", "evacuation order", "state of emergency",
            "טילים", "מתקפת טילים", "הכרזת מלחמה", "מתקפה", "פלישה", "אזעקות", "פיגוע", "חיסול", "תקיפה באיראן", "תקיפת איראן"),
    "macro": ("emergency rate", "surprise rate", "cuts interest rates", "raises interest rates", "cuts rates", "raises rates", "hikes rates", "cpi", "inflation report", "nonfarm", "jobs report", "fomc decision", "בנק ישראל מעלה", "בנק ישראל מוריד", "מדד המחירים"),
    "market": ("circuit breaker", "trading halted", "flash crash", "bank run", "market crash", "stocks plunge", "default", "bankruptcy", "בורסה קורסת", "קריסה", "עצירת מסחר"),
    "energy": ("strait of hormuz", "opec cut", "pipeline attack", "oil surges", "oil plunges", "הורמוז"),
}
SOFT = {
    "war": ("ceasefire", "hostage", "troops", "strike on", "attack on", "houthi", "hezbollah", "iran", "gaza", "ukraine", "russia", "taiwan", "הפסקת אש", "חטופים", "חיזבאללה", "חות'ים", "איראן", "עזה"),
    "macro": ("fed", "rate hike", "rate cut", "powell", "ריבית", "inflation", "gdp", "unemployment", "tariff", "sanctions", "ecb", "אינפלציה", "מכסים", "סנקציות", "אבטלה"),
    "market": ("selloff", "sell-off", "plunge", "tumble", "rally", "record high", "downgrade", "ירידות חדות", "זינוק", "צניחה"),
    "energy": ("oil", "opec", "natural gas", "נפט", "גז"),
}
STOP = {"the", "a", "of", "to", "in", "on", "and", "for", "as", "at", "by", "with", "is", "are", "after", "over", "את", "של", "על", "עם", "לא", "גם", "הוא", "היא"}
PUSH_PER_HOUR = 5
NOISE = ("false claim", "fake", "hoax", "mocked", "mocks", "debunk", "opinion:", "week ahead", "what to know", "explainer", "years later", "looks back", "anniversary", "how to", "quiz", "podcast")
SCHEMA = ("CREATE TABLE IF NOT EXISTS intel_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT, severity INTEGER, title TEXT, link TEXT, "
          "source TEXT, sources INTEGER, hash TEXT UNIQUE, pushed INTEGER DEFAULT 0, lang TEXT, extra TEXT)")


def ensure() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[\w']+", title.lower()) if len(w) > 2 and w not in STOP}


def _hit(title: str, table: dict[str, tuple[str, ...]]) -> list[str]:
    t = title.lower()
    return [fam for fam, keys in table.items() if any(k in t for k in keys)]


def classify(items: list[dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
    """Group the same story across outlets and return scored stories, newest first."""
    now = now or time.time()
    recent = [i for i in items if now - i["ts"] <= 3 * 3600]
    stories: list[dict[str, Any]] = []
    for it in recent:
        if any(n in it["title"].lower() for n in NOISE):
            continue  # commentary, hoax-debunks and anniversaries are not events
        hard, soft = _hit(it["title"], HARD), _hit(it["title"], SOFT)
        if hard == ["macro"] and not re.search(r"\d", it["title"]):
            hard, soft = [], soft + ["macro"]  # no figure in the headline: commentary, not a release
        if not hard and not soft:
            continue
        w = _words(it["title"])
        home = next((s for s in stories if len(w & s["words"]) >= max(3, int(0.5 * min(len(w), len(s["words"]))))), None)
        if home:
            home["outlets"].add(it["source"])
            home["hard"] |= set(hard)
            home["soft"] |= set(soft)
            home["items"].append(it)
            continue
        stories.append({"words": w, "outlets": {it["source"]}, "hard": set(hard), "soft": set(soft), "items": [it], "head": it})
    out = []
    for s in stories:
        n = len(s["outlets"])
        need = 3 if s["hard"] == {"market"} else 2  # a bare "market crash" headline needs more outlets: the price triggers below cover the real thing
        sev = 3 if (s["hard"] and n >= need) else 2 if (s["hard"] or (s["soft"] and n >= 2)) else 1
        fam = sorted(s["hard"] or s["soft"])
        head = max(s["items"], key=lambda i: i["ts"])
        out.append({"kind": fam[0] if fam else "other", "families": fam, "severity": sev, "title": head["title"], "link": head["link"], "source": head["source"], "sources": n,
                    "ts": int(head["ts"]), "lang": head["lang"], "outlets": sorted(s["outlets"])[:6],
                    "hash": hashlib.sha1(("".join(sorted(s["words"]))[:80]).encode()).hexdigest()[:16]})
    return sorted(out, key=lambda e: (-e["severity"], -e["ts"]))


def _market_triggers() -> list[dict[str, Any]]:
    """VIX / index shocks from live prices. Returned in the same shape as news stories."""
    from app.api import invest_routes as ir

    out = []
    for sym, name in (("^VIX", "VIX"), ("ES=F", "S&P 500 futures"), ("NQ=F", "Nasdaq futures")):
        h = ir._safe_history(sym, "5d", "1d")
        if not h or len(h["candles"]) < 2:
            continue
        last, prev = h["candles"][-1][4], h["candles"][-2][4]
        if not last or not prev:
            continue
        pct = (last / prev - 1) * 100
        shock = (sym == "^VIX" and (last >= 30 or pct >= 20)) or (sym != "^VIX" and abs(pct) >= 2.0)
        if shock:
            day = time.strftime("%Y-%m-%d")
            out.append({"kind": "market", "families": ["market"], "severity": 3, "title": f"{name} {pct:+.1f}% on the day ({last:.2f})", "link": "", "source": "live prices", "sources": 1,
                        "ts": int(time.time()), "lang": "en", "outlets": ["live prices"], "hash": hashlib.sha1(f"mkt{sym}{day}{int(pct // 1)}".encode()).hexdigest()[:16]})
    return out


def _store_and_push(stories: list[dict[str, Any]], push: bool = True) -> list[dict[str, Any]]:
    ensure()
    pushed_now: list[dict[str, Any]] = []
    with db.get_conn() as conn:
        sent_last_hour = conn.execute("SELECT COUNT(*) FROM intel_events WHERE pushed=1 AND ts > ?", (int(time.time()) - 3600,)).fetchone()[0]
        for s in stories:
            if s["severity"] < 2:
                continue
            row = conn.execute("SELECT id, pushed, severity FROM intel_events WHERE hash=?", (s["hash"],)).fetchone()
            if row is None:
                conn.execute("INSERT INTO intel_events (ts, kind, severity, title, link, source, sources, hash, lang, extra) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (s["ts"], s["kind"], s["severity"], s["title"], s["link"], s["source"], s["sources"], s["hash"], s["lang"], json.dumps({"outlets": s["outlets"]})))
                row = conn.execute("SELECT id, pushed, severity FROM intel_events WHERE hash=?", (s["hash"],)).fetchone()
            elif s["severity"] > row["severity"]:
                conn.execute("UPDATE intel_events SET severity=?, sources=? WHERE id=?", (s["severity"], s["sources"], row["id"]))
            if push and s["severity"] >= 3 and not row["pushed"] and sent_last_hour + len(pushed_now) < PUSH_PER_HOUR:
                pushed_now.append(s)
                conn.execute("UPDATE intel_events SET pushed=1 WHERE id=?", (row["id"],))
    return pushed_now


WHY_IT_MATTERS = {
    "war": "Geopolitical escalation moves oil, gold, the VIX and defence stocks first; Israeli assets and the shekel react fast.",
    "macro": "Rate and inflation surprises hit yields and the dollar first, then growth and tech stocks.",
    "market": "A stress signal in prices or credit: check exposure, stops and liquidity before the next open.",
    "energy": "Energy shocks feed straight into inflation expectations, airlines, transport and the energy sector.",
}
KIND_ICON = {"war": "🚨", "macro": "📊", "market": "📉", "energy": "🛢"}


def _market_line() -> str:
    """One compact line of live prices for the alert card (cached history, never raises)."""
    from app.api import invest_routes as ir

    parts = []
    for sym, label in (("ES=F", "S&P fut"), ("NQ=F", "NQ fut"), ("^VIX", "VIX"), ("CL=F", "Oil"), ("GC=F", "Gold"), ("ILS=X", "USD/ILS")):
        try:
            h = ir._safe_history(sym, "5d", "1d")
            last, prev = h["candles"][-1][4], h["candles"][-2][4]
            parts.append(f"{label:8s}{last:>9,.2f}  {(last / prev - 1) * 100:+5.1f}%")
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(parts)


def _card(s: dict[str, Any]) -> tuple[str, list[list[dict]]]:
    from html import escape

    when = time.strftime("%H:%M", time.localtime(s["ts"]))
    others = max(0, s["sources"] - 1)
    src = escape(s["source"]) + (f" +{others} more" if others else "")
    kind = s["kind"] if s["kind"] in WHY_IT_MATTERS else "market"
    mkt = _market_line()
    lines = [f"{KIND_ICON.get(kind, '⚠️')} <b>CRITICAL · {kind.upper()}</b>   <code>▰▰▰ 3/3</code>", "", f"<b>{escape(s['title'])}</b>", f"<i>{src} · {when}</i>", "",
             f"<b>Why it matters</b>\n{WHY_IT_MATTERS[kind]}"]
    try:
        from app.intel import impact

        info = impact.understand(s["title"], s.get("kind"))
        ex = impact.exposure(info, impact.playbook(info["playbook"]))
        if ex and ex["rows"]:
            names = ", ".join(r["symbol"] for r in ex["rows"][:4])
            hist = f" · history {'+' if ex['hist_usd'] >= 0 else '-'}${abs(ex['hist_usd']):,} day 1" if ex["hist_usd"] else ""
            lines += ["", f"💼 <b>Your fund</b>: ${ex['exposed']:,} of ${ex['total']:,} exposed ({escape(names)}){hist}"]
    except Exception:  # noqa: BLE001 - the alert must go out even if the fund lookup fails
        logger.exception("fund exposure line failed")
    if mkt:
        lines += ["", "<b>Markets right now</b>", f"<pre>{escape(mkt)}</pre>"]
    lines += ["", "<i>NOSTRO Intel · confirmed by " + str(s["sources"]) + (" outlet" if s["sources"] == 1 else " outlets") + "</i>"]
    buttons = [[{"text": "📰 Read the story", "url": s["link"]}]] if str(s.get("link", "")).startswith("http") else []
    return "\n".join(lines), buttons


def _telegram(s: dict[str, Any]) -> None:
    from app.notify import telegram_bot

    try:
        from app.intel import newsimpact

        if newsimpact.covered(s["title"]):
            return  # the news engine already alerted this story
    except Exception:  # noqa: BLE001
        logger.exception("news engine dedupe check failed")
    if s.get("kind") != "market":  # price triggers are facts; keyword stories must also pass the AI reader when one is configured
        try:
            from app.intel import ai_news

            if ai_news.enabled():
                tri = ai_news.triage([{"id": 0, "title": s["title"], "kinds": json.dumps([s.get("kind", "")])}]).get(0)
                if tri and (not tri["mm"] or tri["score"] < 50):
                    logger.info("critical event dropped by AI reader (%s/100): %s", tri["score"], s["title"][:80])
                    return
        except Exception:  # noqa: BLE001
            logger.exception("AI check of critical event failed")
    html, buttons = _card(s)
    try:  # watch the event's themes for small caps that break out because of it (app/intel/movers.py)
        from app.intel import movers

        movers.watch(f"ev:{s.get('hash') or s['title'][:60]}", int(s["ts"]), s["title"], s.get("kind"))
    except Exception:  # noqa: BLE001
        logger.exception("movers watch failed")
    if telegram_bot.send_rich(html, buttons) is None:  # HTML rejected or network problem: fall back to plain text so the alert is never lost
        telegram_bot.send_text(f"CRITICAL · {s['kind'].upper()}\n{s['title']}\n{s['source']} · {s['sources']} outlets" + (f"\n{s['link']}" if s.get("link") else ""))


def scan(push: bool = True) -> dict[str, Any]:
    items = feeds.fetch_all(max_age=60)
    stories = classify(items) + _market_triggers()
    pushed = _store_and_push(stories, push=push)
    for s in pushed:
        try:
            _telegram(s)
        except Exception:  # noqa: BLE001
            logger.exception("critical-event telegram push failed")
    return {"items": len(items), "stories": len(stories), "pushed": len(pushed)}


def recent(hours: float = 48, min_sev: int = 2) -> list[dict[str, Any]]:
    ensure()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM intel_events WHERE ts > ? AND severity >= ? ORDER BY ts DESC LIMIT 200", (int(time.time() - hours * 3600), min_sev)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["outlets"] = json.loads(d.pop("extra") or "{}").get("outlets", [])
        except ValueError:
            d["outlets"] = []
        out.append(d)
    return out


def impact_followups() -> int:
    """30-90 min after a pushed alert, send the movers / Polymarket / history card once."""
    from app.intel import impact
    from app.notify import telegram_bot

    sent = 0
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, ts, kind, title, link FROM intel_events WHERE pushed=1 AND ts BETWEEN ? AND ?", (int(time.time() - 5400), int(time.time() - 1800))).fetchall()]
    for r in rows:
        key = f"impact_sent:{r['id']}"
        if db.meta_get(key):
            continue
        db.meta_set(key, "1")
        res = impact.analyse(r["ts"], r["title"], r["kind"])
        if impact.worth_sending(res):
            html, buttons = impact.card(res, r.get("link"))
            telegram_bot.send_rich(html, buttons)
            sent += 1
    return sent


def _daily_ideas() -> None:
    import json as _json

    from app.api import market_routes as mr
    from app.intel import insiders, setups

    try:
        res = setups.scan(list(mr.UNIVERSE))
        db.meta_set("setup_scan_latest", _json.dumps(res))
    except Exception:  # noqa: BLE001
        logger.exception("setup scan failed")
    try:
        insiders.refresh(list(mr.UNIVERSE), days=21)
    except Exception:  # noqa: BLE001
        logger.exception("insider refresh failed")


async def intel_loop() -> None:
    from app.intel import calendar, impact, pm_watch

    await asyncio.sleep(45)
    n = 0
    while True:
        from app.intel import pm_edge

        steps = [("scan", lambda: scan(True)), ("impact follow-ups", impact_followups), ("economic calendar", calendar.tick), ("polymarket shocks", pm_watch.tick)]
        from app.world import exchanges as world_exchanges

        steps.append(("world exchanges", world_exchanges.world_tick))  # unusual index moves on any exchange
        if n % 3 == 0:  # every 15 min
            steps.append(("polymarket opportunities", pm_edge.tick))
        if n % 24 == 5:  # about every 2 hours, in the background (GDELT is rate-limited, so this takes a few minutes)
            from app.intel import pm_resolve

            asyncio.create_task(asyncio.to_thread(pm_resolve.scan))
        if n % 288 == 3:  # once a day (and ~15 min after start-up): chart setups + insider buying, in the background so the loop is not held up
            asyncio.create_task(asyncio.to_thread(_daily_ideas))
        if n % 36 == 0:  # at start-up, then about every 3 hours
            steps.append(("playbook learning", impact.learn))
        for name, fn in steps:  # each step on its own: one failing source never stops the others
            try:
                await asyncio.to_thread(fn)
            except Exception:  # noqa: BLE001
                logger.exception("intel step failed: %s", name)
        n += 1
        await asyncio.sleep(300)


async def news_loop() -> None:
    """Every 60 s: read the fast sources, group stories, classify, measure reactions, send the two-step alerts. RSS + follow-ups every 5 min."""
    from app.intel import newsimpact, sources

    await asyncio.sleep(60)
    n = 0
    while True:
        steps = [("fast sources", sources.poll_fast), ("news impact", newsimpact.process), ("news alerts", newsimpact.alerts)]
        if n % 2 == 0:
            from app.intel import movers

            steps.append(("event movers", movers.tick))  # small-cap breakouts on watched events + follow-ups on earlier picks
        if n % 5 == 0:
            steps.insert(1, ("rss into stories", sources.poll_slow))
            steps.append(("follow-up", newsimpact.followup))
        for name, fn in steps:
            try:
                await asyncio.to_thread(fn)
            except Exception:  # noqa: BLE001
                logger.exception("news step failed: %s", name)
        n += 1
        await asyncio.sleep(60)
