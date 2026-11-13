"""EVENTS MAP API (prefix /api/world/live): live earthquakes, tropical storms (track + cone), GDACS / EONET hazards,
the market-impact sidebar and the bottom ticker. Every upstream feed is fetched server-side and cached with a TTL
(stale-while-revalidate via capital_routes.cached), so a page left open never hammers USGS / NOAA / GDACS / NASA, and
a feed that is down only blanks its own layer: /all reports per-source status instead of failing."""
from __future__ import annotations

import concurrent.futures as cf
import re
import threading
import time
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from app.api.capital_routes import _chart, cached
from app.world import hazards, impact

router = APIRouter(prefix="/api/world/live", tags=["world-live"])
_POOL = cf.ThreadPoolExecutor(max_workers=12, thread_name_prefix="wlive")
_STATUS: dict[str, dict[str, Any]] = {}
_SLOCK = threading.Lock()

TTL = {"quakes": 90, "nhc": 120, "gdacs": 300, "eonet": 600, "tcgeom": 1800, "quote": 300}


def _src(name: str, key: str, ttl: float, fn: Callable[[], Any], default: Any) -> Any:
    """cached(fn) with per-source health bookkeeping; returns `default` (and records the error) when the feed is down
    and nothing is cached yet."""
    try:
        val = cached(key, ttl, fn)
        with _SLOCK:
            _STATUS[name] = {"ok": True, "ts": int(time.time()), "err": None}
        return val
    except Exception as e:  # noqa: BLE001
        with _SLOCK:
            prev = _STATUS.get(name) or {}
            _STATUS[name] = {"ok": False, "ts": prev.get("ts"), "err": str(e)[:160]}
        return default


# ------------------------------------------------------------------ sources
def _quakes(feed: str) -> dict[str, Any]:
    feed = feed if feed in hazards.QUAKE_FEEDS else "day"
    return _src("usgs", f"wl:quakes:{feed}", TTL["quakes"], lambda: hazards.quakes(feed), {"feed": feed, "n": 0, "quakes": []})


def _gdacs() -> list[dict[str, Any]]:
    return _src("gdacs", "wl:gdacs", TTL["gdacs"], hazards.gdacs_events, [])


def _eonet() -> list[dict[str, Any]]:
    return _src("eonet", "wl:eonet", TTL["eonet"], hazards.eonet_events, [])


def _nhc() -> list[dict[str, Any]]:
    return _src("nhc", "wl:nhc", TTL["nhc"], hazards.nhc_storms, [])


def _gdacs_storms(gd: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Current GDACS cyclones with geometry; when none is current, the latest one from the last 30 days is returned
    separately as `recent` so the track animation still has something real to replay."""
    tcs = [e for e in gd if e["code"] == "TC" and e.get("_f")]
    cur = [e for e in tcs if e["current"]]

    def build(e: dict[str, Any]) -> dict[str, Any]:
        return cached(f"wl:tc:{e['eventid']}:{e['episodeid']}", TTL["tcgeom"], lambda: hazards._gdacs_storm(e["_f"]))  # noqa: SLF001
    storms = [s for s in _POOL.map(lambda e: _safe(lambda: build(e)), cur) if s]
    recent = None
    if not cur and tcs:
        last = max(tcs, key=lambda e: e["t"])
        recent = _safe(lambda: build(last))
        if recent:
            recent = {**recent, "active": False, "recent": True}
    return storms, recent


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _storms() -> dict[str, Any]:
    f_nhc, f_gd, f_eo = _POOL.submit(_nhc), _POOL.submit(_gdacs), _POOL.submit(_eonet)
    nhc, gd, eo = f_nhc.result(), f_gd.result(), f_eo.result()
    gstorms, recent = _gdacs_storms(gd)
    eostorms = [_safe(lambda e=e: hazards.eonet_storm(e)) for e in eo if e["code"] == "severeStorms" and e.get("_track")]
    eostorms = [s for s in eostorms if s]
    storms = hazards.merge_storms(nhc, gstorms, eostorms)
    for s in storms:
        s["kind"] = "cyclone"
    if recent:
        recent["kind"] = "cyclone"
    return {"storms": storms, "recent": recent if not storms else None, "n": len(storms)}


def _hazards() -> list[dict[str, Any]]:
    gd, eo = _gdacs(), _eonet()
    out = [{k: v for k, v in e.items() if not k.startswith("_")} for e in gd if e["code"] not in ("TC", "EQ")]  # cyclones -> storms layer, quakes -> USGS
    now = time.time()
    out = [e for e in out if e["current"] or now - (e["t"] or 0) < 14 * 86400]
    out += [{k: v for k, v in e.items() if not k.startswith("_")} for e in eo if e["code"] not in ("severeStorms", "seaLakeIce", "waterColor", "earthquakes")]
    out.sort(key=lambda e: -(e["t"] or 0))
    return out


# ------------------------------------------------------------------ impact + ticker
def _impact(qk: list[dict[str, Any]], storms: list[dict[str, Any]], hz: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for q in qk:
        if (q.get("mag") or 0) < 4.8:
            continue
        ev = {"kind": "quake", **q}
        a = impact.assess(ev)
        if a:
            rows.append({"id": q["id"], "kind": "quake", "title": f"M{q['mag']:.1f} {q['place']}", "lat": q["lat"], "lon": q["lon"], "t": q["t"], **a})
    for s in storms:
        a = impact.assess(s)
        if a:
            rows.append({"id": s["id"], "kind": "cyclone", "title": f"{s.get('name')} ({s.get('cat')}{', ' + str(int(s['kt'])) + ' kt' if s.get('kt') else ''})", "lat": s["lat"], "lon": s["lon"], "t": s["t"], **a})
    for h in hz:
        a = impact.assess(h)
        if a:
            rows.append({"id": h["id"], "kind": h["kind"], "title": h["title"], "lat": h["lat"], "lon": h["lon"], "t": h["t"], **a})
    rows.sort(key=lambda r: (-r["sev"], -(r["t"] or 0)))
    return rows[:30]


def _ticker(qk: list[dict[str, Any]], storms: list[dict[str, Any]], hz: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for q in qk:
        if (q.get("mag") or 0) >= 4.5:
            items.append({"id": q["id"], "kind": "quake", "t": q["t"], "lat": q["lat"], "lon": q["lon"], "sev": impact.severity({"kind": "quake", **q}),
                          "text": f"M{q['mag']:.1f} EARTHQUAKE · {q['place'].upper()}{' · TSUNAMI FLAG' if q.get('tsunami') else ''}"})
    for s in storms:
        items.append({"id": s["id"], "kind": "cyclone", "t": s["t"], "lat": s["lat"], "lon": s["lon"], "sev": impact.severity(s),
                      "text": f"{(s.get('name') or '').upper()} · {s.get('cat')}{' · ' + str(int(s['kt'])) + ' KT' if s.get('kt') else ''}{' · ' + s['move'] if s.get('move') else ''}"})
    for h in hz:
        if h["src"] == "GDACS" and h.get("alert") in ("Orange", "Red") or h["kind"] in ("volcano", "flood", "drought") or h["src"] == "EONET" and h["kind"] != "wildfire":
            items.append({"id": h["id"], "kind": h["kind"], "t": h["t"], "lat": h["lat"], "lon": h["lon"], "sev": impact.severity(h),
                          "text": f"{h['kind'].upper()} · {(h['title'] or '').upper()[:90]}{' · ' + h['alert'].upper() + ' ALERT' if h.get('alert') and h['alert'] != 'Green' else ''}"})
    items.sort(key=lambda i: -(i["t"] or 0))
    return items[:60]


# ------------------------------------------------------------------ endpoints
@router.get("/quakes")
def api_quakes(feed: str = "day"):
    return _quakes(feed)


@router.get("/storms")
def api_storms():
    return _storms()


@router.get("/events")
def api_events():
    hz = _hazards()
    return {"n": len(hz), "events": hz}


@router.get("/all")
def api_all(feed: str = "day"):
    """Everything the map needs in one round trip (each part cached on its own TTL)."""
    fq, fs, fh = _POOL.submit(_quakes, feed), _POOL.submit(_storms), _POOL.submit(_hazards)
    q, st, hz = fq.result(), fs.result(), fh.result()
    storms_for_impact = st["storms"] + ([st["recent"]] if st.get("recent") else [])
    with _SLOCK:
        status = {k: dict(v) for k, v in _STATUS.items()}
    return {"ts": int(time.time()), "quakes": q, "storms": st, "events": hz, "impact": _impact(q["quakes"], storms_for_impact, hz),
            "ticker": _ticker(q["quakes"], st["storms"], hz), "status": status, "refresh": 90}


@router.get("/impact")
def api_impact(feed: str = "day"):
    d = api_all(feed)
    return {"impact": d["impact"]}


@router.get("/quotes")
def api_quotes(s: str):
    syms = [x for x in s.split(",") if re.fullmatch(r"[A-Za-z0-9.=^\-]{1,15}", x)][:40]
    if not syms:
        raise HTTPException(status_code=400, detail="no symbols")

    def one(sym: str) -> tuple[str, Any]:
        q = _safe(lambda: cached(f"wl:q:{sym}", TTL["quote"], lambda: _chart(sym) or {}))
        if not q:
            return sym, None
        return sym, {"price": q["price"], "pct": (q["price"] / q["prev"] - 1) * 100 if q.get("prev") else 0, "spark": q.get("spark", [])[-24:]}
    return {"quotes": dict(_POOL.map(one, syms))}


@router.get("/rules")
def api_rules():
    return {"rules": impact.rule_table()}


@router.get("/status")
def api_status():
    with _SLOCK:
        return {"sources": dict(_STATUS)}


def warm() -> None:
    """Prefetch every feed once in the background so the first page open is instant."""
    threading.Thread(target=lambda: _safe(lambda: api_all("day")), daemon=True, name="world-live-warm").start()
