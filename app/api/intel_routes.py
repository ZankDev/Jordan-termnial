"""Market intelligence API: merged news feeds, critical events, Polymarket crowd probabilities, and the status of every connector."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException

from app.intel import crowd, events, feeds

router = APIRouter(prefix="/api/intel")


@router.get("/feed")
def api_feed(group: str = "all", limit: int = 60):
    items = feeds.fetch_all()
    if group in ("il", "world"):
        items = [i for i in items if i["group"] == group]
    stories = {e["title"]: e for e in events.classify(items)}
    rows = []
    for it in items[: max(1, min(limit, 200))]:
        ev = stories.get(it["title"])
        rows.append({**it, "severity": ev["severity"] if ev else 0, "kind": ev["kind"] if ev else None})
    return {"items": rows, "n": len(items), "at": time.time()}


@router.get("/events")
def api_events(hours: float = 48, min_sev: int = 2):
    return {"events": events.recent(hours, min_sev)}


@router.post("/scan")
def api_scan(push: bool = False):
    """Run one detection pass now (push=false just refreshes the stored events; pushes only go out from the background loop or push=true)."""
    return events.scan(push=push)


@router.get("/polymarket")
def api_polymarket():
    try:
        return crowd.snapshot()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Polymarket unavailable: {type(e).__name__}")


@router.get("/ledger")
def api_ledger(hours: float = 48, all: bool = False):
    """Every market-relevant story with its type, expected impact, measured verdict and explanation (news impact engine)."""
    from app.intel import newsimpact

    return {"stories": newsimpact.ledger(hours, only_relevant=not all), "stats": newsimpact.stats(min_n=5)}


@router.get("/story/{sid}")
def api_story(sid: int):
    from app.intel import newsimpact

    s = newsimpact.story(sid)
    if not s:
        raise HTTPException(status_code=404, detail="no such story")
    return s


@router.get("/channels")
def api_channels():
    from app.intel import sources

    return {"channels": sources.channels()}


@router.post("/channels")
def api_set_channels(body: dict[str, Any]):
    """Replace the Telegram channel list: {"channels": {"WalterBloomberg": "squawk", ...}} (public channels only)."""
    from app.intel import sources

    ch = body.get("channels")
    if not isinstance(ch, dict) or not ch:
        raise HTTPException(status_code=400, detail="channels must be a non-empty object")
    return {"channels": sources.set_channels(ch)}


@router.get("/pm-opps")
def api_pm_opps():
    """Model-vs-price opportunities on Polymarket price-threshold markets (live scan) and the paper ledger with its hit rate."""
    from app.intel import pm_edge

    try:
        live = pm_edge.scan()[:25]
    except Exception:  # noqa: BLE001
        live = []
    return {"live": live, **pm_edge.scorecard()}


@router.get("/ideas")
def api_ideas():
    """Where to invest: insider purchase clusters (SEC Form 4), chart setups with measured base rates, and the Polymarket paper ledger."""
    import json as _json

    from app import db as _db
    from app.intel import insiders, pm_edge

    setups_raw = _db.meta_get("setup_scan_latest")
    return {"insiders": insiders.clusters(14)[:20], "setups": _json.loads(setups_raw) if setups_raw else None, "polymarket": pm_edge.scorecard()}


@router.get("/pm-resolve")
def api_pm_resolve():
    """Latest resolution-lag check: date-specific Polymarket markets whose day is over, the evidence found, the verdict and any price gap."""
    import json as _json

    from app import db as _db

    raw = _db.meta_get("pm_resolve_latest")
    return _json.loads(raw) if raw else {"at": None, "results": []}


@router.get("/llm")
def api_llm_status():
    from app.intel import llm

    return llm.status()


@router.post("/llm")
def api_llm_set(body: dict[str, Any]):
    """Configure the free language model: {"provider": "groq" | "gemini" | "openrouter" | "ollama", "key": "...", "model": ""}. The key is stored locally only."""
    from app.intel import llm

    try:
        return llm.set_config(str(body.get("provider", "")), str(body.get("key", "")), str(body.get("model", "")))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/calendar")
def api_calendar():
    """This week's US releases that move markets: time, consensus forecast, previous, and (once out) actual / surprise / direction."""
    from app.intel import calendar

    return calendar.view()


@router.get("/pm-shocks")
def api_pm_shocks(hours: float = 24):
    """Recent sharp moves in market-relevant Polymarket probabilities (see app/intel/pm_watch.py)."""
    from app.intel import pm_watch

    return {"shocks": pm_watch.recent(hours)}


_impact_cache: dict[int, tuple[float, Any]] = {}


@router.get("/impact")
def api_impact(event_id: int | None = None):
    """For one stored alert (default: the latest critical one): stocks that moved since, matching Polymarket markets, and the learned playbook for that kind of event."""
    from app.intel import impact

    evs = events.recent(hours=24 * 14, min_sev=2)
    pick = next((e for e in evs if e["id"] == event_id), None) if event_id else next((e for e in evs if e["severity"] >= 3), evs[0] if evs else None)
    if pick is None:
        return {"event": None, "note": "no stored events yet"}
    hit = _impact_cache.get(pick["id"])
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    try:
        res = {"event": {k: pick[k] for k in ("id", "ts", "kind", "severity", "title", "link", "source")}, **impact.analyse(pick["ts"], pick["title"], pick["kind"])}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"impact unavailable: {type(e).__name__}") from e
    _impact_cache[pick["id"]] = (time.time(), res)
    return res


@router.get("/study")
def api_study(trigger: str = "vix_spike", threshold: float | None = None):
    """What the S&P 500 did after a kind of event, with sample size and a significance check (see app/intel/study.py)."""
    from app.intel import study

    try:
        return study.run(trigger, threshold)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"price history unavailable: {type(e).__name__}") from e


def _connector_state() -> list[dict[str, Any]]:
    import os

    from app import db

    def have(*keys: str) -> bool:
        return all(os.environ.get(k) or db.meta_get(k) for k in keys)

    return [
        {"name": "Israeli news (RSS)", "state": "connected", "detail": f"{sum(1 for f in feeds.status()['feeds'] if f['group'] == 'il' and f.get('ok'))} outlets answering"},
        {"name": "Global news (Google News queries)", "state": "connected", "detail": "war, Fed/inflation, market stress, oil, Hebrew security + Bank of Israel"},
        {"name": "Polymarket (crowd probabilities)", "state": "connected", "detail": "public Gamma API"},
        {"name": "Telegram groups (read your channels)", "state": "ready" if have("TG_API_ID", "TG_API_HASH") else "needs credentials",
         "detail": "needs api_id + api_hash from my.telegram.org and a one-time login code (see TELEGRAM READER)"},
        {"name": "X / Twitter", "state": "ready" if have("X_BEARER_TOKEN") else "needs credentials", "detail": "needs an X API bearer token (paid tier) - see X CONNECTOR"},
    ]


@router.get("/sources")
def api_sources():
    return {"connectors": _connector_state(), **feeds.status()}


@router.get("/movers")
def get_movers(days: int = 14):
    """EVENT MOVERS: small caps that broke out because of a critical event, their targets and outcomes, plus the events being watched now."""
    from app.intel import movers

    return movers.board(days)


@router.get("/history")
def api_history(type: str | None = None):
    """Library of historical market-moving events with the measured reaction of each asset (first session, 2 days, 1 week, 1 month)."""
    from app.intel import history

    evs = history.events()
    if type:
        evs = [e for e in evs if e["type"] == type]
    types = sorted({e["type"] for e in history.events()})
    return {"events": sorted(evs, key=lambda e: e["date"], reverse=True), "types": types, "names": {s: history.name(s) for e in evs for s in e["react"]}}


@router.get("/history/playbook")
def api_history_playbook(type: str):
    from app.intel import history

    return history.playbook(type)


@router.get("/history/symbol")
def api_history_symbol(symbol: str):
    """How one security reacted to every event in the library (chart markers)."""
    from app.intel import history

    return {"symbol": symbol.upper(), "events": history.for_symbol(symbol.upper())}


@router.get("/stock-news")
def api_stock_news(hours: int = 48):
    """Listed companies (any exchange) named in the news, with the AI's read and the measured reaction of each company's stock."""
    import json as _json
    import time as _time

    from app import db as _db
    from app.intel import newsimpact

    with _db.read_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, ts, title, sources, ntype, verdict, explain, measured, status, ai FROM news_stories "
                                              "WHERE ts > ? AND ai LIKE '%\"companies\": [{%' ORDER BY ts DESC LIMIT 200",
                                              (int(_time.time()) - max(1, min(hours, 240)) * 3600,)).fetchall()]
    out = []
    for r in rows:
        ai = _json.loads(r["ai"] or "{}")
        tri = ai.get("triage") or {}
        m = _json.loads(r["measured"]) if r["measured"] else {}
        comps = []
        for c in tri.get("companies") or []:
            mv = {sym: (m.get("assets") or {}).get(sym) for sym in (c["symbol"], c.get("us")) if sym}
            comps.append({**c, "moves": mv})
        an = ai.get("analysis") or {}
        out.append({"id": r["id"], "ts": r["ts"], "title": r["title"], "sources": _json.loads(r["sources"] or "[]"), "type": r["ntype"], "score": tri.get("score"),
                    "why": tri.get("why"), "verdict": r["verdict"], "explain": r["explain"], "status": r["status"], "companies": comps,
                    "event": an.get("event"), "labels": {s: newsimpact.lab(s) for c in comps for s in c["moves"]}})
    return {"stories": out}
