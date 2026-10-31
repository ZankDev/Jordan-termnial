"""WORLD map API (prefix /api/world): country shapes, radio stations, live planes, public cameras, quakes, natural events, radar,
and a per-country dashboard. Slow feeds are sampled by app.world.sampler; everything else is cached (stale-while-revalidate)."""
from __future__ import annotations

import email.utils
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, HTTPException

from app.api.capital_routes import _POOL, _chart, cached
from app.world import newsheat, sampler
from app.world.countries import CAPITALS, COUNTRIES, NAME_TO_CC

router = APIRouter(prefix="/api/world", tags=["world"])
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
WORLD_DIR = Path(__file__).resolve().parents[2] / "data_cache" / "world"
RADIO_HOSTS = ("de1.api.radio-browser.info", "fi1.api.radio-browser.info", "at1.api.radio-browser.info")
GEOJSON_URL = "https://raw.githubusercontent.com/vasturiano/globe.gl/master/example/datasets/ne_110m_admin_0_countries.geojson"


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


# ------------------------------------------------------------------ countries (shapes cached on disk)
def _load_countries() -> dict[str, Any]:
    f = WORLD_DIR / "countries.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    WORLD_DIR.mkdir(parents=True, exist_ok=True)
    g = requests.get(GEOJSON_URL, headers=UA, timeout=40).json()
    feats = []
    for ft in g["features"]:
        p = ft["properties"]
        cc = p.get("ISO_A2")
        if not cc or cc == "-99":
            cc = NAME_TO_CC.get(p.get("NAME"), p.get("ISO_A3_EH", "")[:2])
        feats.append({"type": "Feature", "geometry": ft["geometry"], "properties": {"cc": cc, "name": p.get("NAME"), "pop": p.get("POP_EST"), "continent": p.get("CONTINENT")}})
    out = {"type": "FeatureCollection", "features": feats}
    f.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


@router.get("/countries")
def api_countries():
    return cached("world:countries", 7 * 86400, _load_countries)


# ------------------------------------------------------------------ radio (radio-browser.info)
_RADIO_BY_ID: dict[str, dict[str, Any]] = {}


def _radio_get(path: str, params: dict[str, Any], timeout: int = 60) -> Any:
    last: Exception | None = None
    for host in RADIO_HOSTS:
        try:
            r = requests.get(f"https://{host}/json/{path}", params=params, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"radio-browser unreachable: {last}")


def _radio_all() -> dict[str, Any]:
    rows = _radio_get("stations/search", {"has_geo_info": "true", "hidebroken": "true", "limit": 20000, "order": "votes", "reverse": "true"})
    out = []
    for s in rows:
        if not s.get("lastcheckok") or s.get("geo_lat") is None or s.get("geo_long") is None:
            continue
        _RADIO_BY_ID[s["stationuuid"]] = s
        out.append([s["stationuuid"], (s.get("name") or "").strip()[:42], round(s["geo_lat"], 3), round(s["geo_long"], 3), s.get("countrycode") or "", s.get("votes") or 0, 1 if s.get("hls") else 0])
    return {"n": len(out), "stations": out}


@router.get("/radio")
def api_radio():
    return cached("world:radio", 6 * 3600, _radio_all)


@router.get("/radio/country/{cc}")
def api_radio_country(cc: str):
    cc = cc.upper()
    cached("world:radio", 6 * 3600, _radio_all)
    rows = sorted((s for s in _RADIO_BY_ID.values() if s.get("countrycode") == cc), key=lambda s: -(s.get("votes") or 0))[:40]
    return {"cc": cc, "stations": [{"id": s["stationuuid"], "name": s["name"].strip(), "tags": (s.get("tags") or "")[:60], "votes": s.get("votes") or 0, "hls": bool(s.get("hls"))} for s in rows]}


@router.get("/radio/play/{uuid}")
def api_radio_play(uuid: str):
    if not re.fullmatch(r"[0-9a-f\-]{36}", uuid):
        raise HTTPException(status_code=400, detail="bad station id")
    cached("world:radio", 6 * 3600, _radio_all)
    s = _RADIO_BY_ID.get(uuid)
    if not s:
        raise HTTPException(status_code=404, detail="unknown station")
    # radio-browser asks players to register a click when a station is played
    threading.Thread(target=lambda: _safe(lambda: requests.get(f"https://{RADIO_HOSTS[0]}/json/url/{uuid}", headers=UA, timeout=10)), daemon=True).start()
    return {"id": uuid, "name": s["name"].strip(), "url": s.get("url_resolved") or s.get("url"), "hls": bool(s.get("hls")), "homepage": s.get("homepage"), "favicon": s.get("favicon"),
            "tags": (s.get("tags") or "")[:80], "country": s.get("country"), "cc": s.get("countrycode"), "codec": s.get("codec"), "bitrate": s.get("bitrate"), "lat": s.get("geo_lat"), "lon": s.get("geo_long")}


# ------------------------------------------------------------------ planes
@router.get("/planes")
def api_planes():
    st = sampler.STATE
    return {"ts": st["planes"]["ts"], "n": len(st["planes"]["rows"]), "rows": st["planes"]["rows"], "mil_ts": st["mil"]["ts"], "mil": st["mil"]["rows"], "errors": st["errors"],
            "history_from": (st["ring"][0][0] if st["ring"] else None)}


@router.get("/planes/history/index")
def api_planes_history_index():
    return {"ts": sampler.snapshot_times(), "step": sampler.SNAP_EVERY}


@router.get("/planes/history")
def api_planes_history(ts: float):
    snap = sampler.snapshot_at(ts)
    if not snap:
        raise HTTPException(status_code=404, detail="no history yet: the sampler stores a snapshot every 5 minutes")
    return {"ts": snap[0], "rows": snap[1]}


# ------------------------------------------------------------------ public cameras (official open data only)
def _tfl_cams() -> list[list[Any]]:
    d = requests.get("https://api.tfl.gov.uk/Place/Type/JamCam", headers=UA, timeout=40).json()
    out = []
    for c in d:
        props = {p["key"]: p["value"] for p in c.get("additionalProperties", [])}
        if props.get("available") != "true" or not props.get("imageUrl"):
            continue
        out.append([c["id"], c["commonName"], round(c["lat"], 5), round(c["lon"], 5), props["imageUrl"], props.get("videoUrl") or None, "tfl", "GB"])
    return out


def _sg_cams() -> list[list[Any]]:
    d = requests.get("https://api.data.gov.sg/v1/transport/traffic-images", headers=UA, timeout=20).json()
    cams = (d.get("items") or [{}])[0].get("cameras") or []
    return [[f"sg{c['camera_id']}", f"Singapore traffic {c['camera_id']}", round(c["location"]["latitude"], 5), round(c["location"]["longitude"], 5), c["image"], None, "sg", "SG"] for c in cams]


def _curated_cams() -> list[list[Any]]:
    f = Path(__file__).resolve().parents[1] / "world" / "cams_curated.json"
    try:
        return [[c["id"], c["name"], c["lat"], c["lon"], c.get("img"), None, "curated", c.get("cc", ""), c.get("embed")] for c in json.loads(f.read_text(encoding="utf-8"))]
    except Exception:  # noqa: BLE001
        return []


@router.get("/cams")
def api_cams():
    tfl = cached("world:cams:tfl", 6 * 3600, _tfl_cams) or []
    sg = cached("world:cams:sg", 60, _sg_cams) or []
    cur = _curated_cams()
    return {"n": len(tfl) + len(sg) + len(cur), "cams": tfl + sg + cur,
            "sources": [{"id": "tfl", "name": "London TfL JamCams", "n": len(tfl)}, {"id": "sg", "name": "Singapore LTA traffic images", "n": len(sg)}, {"id": "curated", "name": "Curated open cams", "n": len(cur)}],
            "policy": "Official open-data traffic cameras and streams their owners publish for embedding. No unsecured or private cameras."}


@router.get("/cams/sg")
def api_cams_sg():
    return {"cams": cached("world:cams:sg", 60, _sg_cams) or []}


# ------------------------------------------------------------------ hazards
_QUAKE_FEEDS = {"day": "2.5_day", "week": "2.5_week", "month": "4.5_month"}


@router.get("/quakes")
def api_quakes(range: str = "day"):
    feed = _QUAKE_FEEDS.get(range, "2.5_day")

    def go() -> list[list[Any]]:
        d = requests.get(f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson", headers=UA, timeout=25).json()
        return [[f["id"], round(f["geometry"]["coordinates"][1], 3), round(f["geometry"]["coordinates"][0], 3), f["properties"].get("mag"), round(f["geometry"]["coordinates"][2] or 0),
                 int((f["properties"].get("time") or 0) / 1000), f["properties"].get("place") or ""] for f in d.get("features", [])]
    rows = cached(f"world:quakes:{feed}", 180, go)
    return {"range": range, "n": len(rows), "quakes": rows}


def _centroid(geom: dict[str, Any]) -> tuple[float, float] | None:
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Point":
        return c[1], c[0]
    if t == "Polygon" and c and c[0]:
        ring = c[0]
        return sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring)
    return None


@router.get("/events")
def api_events():
    def go() -> list[list[Any]]:
        d = requests.get("https://eonet.gsfc.nasa.gov/api/v3/events", params={"status": "open", "limit": 300}, headers=UA, timeout=30).json()
        out = []
        for e in d.get("events", []):
            geoms = e.get("geometry") or []
            g = next((x for x in reversed(geoms) if _centroid(x)), None)
            if not g:
                continue
            lat, lon = _centroid(g)
            ts = 0
            try:
                ts = int(time.mktime(time.strptime(g["date"][:19], "%Y-%m-%dT%H:%M:%S")))
            except Exception:  # noqa: BLE001
                pass
            out.append([e["id"], e["title"], (e.get("categories") or [{}])[0].get("id", ""), round(lat, 3), round(lon, 3), ts, (e.get("sources") or [{}])[0].get("url") or e.get("link")])
        return out
    rows = cached("world:events", 900, go)
    return {"n": len(rows), "events": rows}


@router.get("/radar")
def api_radar():
    def go() -> dict[str, Any]:
        d = requests.get("https://api.rainviewer.com/public/weather-maps.json", headers=UA, timeout=15).json()
        frames = d["radar"]["past"] + d["radar"].get("nowcast", [])
        return {"host": d["host"], "frames": frames, "latest": d["radar"]["past"][-1]}
    return cached("world:radar", 300, go)


# ------------------------------------------------------------------ country dashboard
def _news_for(name: str) -> list[dict[str, Any]]:
    r = requests.get("https://news.google.com/rss/search", params={"q": f"{name} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"}, headers=UA, timeout=12)
    out = []
    for it in ET.fromstring(r.content).findall(".//item")[:12]:
        ts = time.time()
        try:
            ts = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except Exception:  # noqa: BLE001
            pass
        title, src = (it.findtext("title") or "").strip(), (it.findtext("source") or "").strip()
        if src and title.endswith(f" - {src}"):
            title = title[: -len(src) - 3]
        out.append({"title": title, "link": (it.findtext("link") or "").strip(), "source": src, "ts": ts})
    return out


def _weather(lat: float, lon: float) -> dict[str, Any] | None:
    d = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m,is_day"}, headers=UA, timeout=15).json()
    return d.get("current")


def _quote(sym: str | None) -> dict[str, Any] | None:
    if not sym:
        return None
    q = _chart(sym)
    if not q:
        return None
    return {"symbol": sym, "price": q["price"], "pct": (q["price"] / q["prev"] - 1) * 100 if q["prev"] else 0, "spark": q["spark"]}


_COUNTRY: dict[str, tuple[float, dict[str, Any], bool]] = {}


@router.get("/country/{cc}")
def api_country(cc: str, name: str | None = None, lat: float | None = None, lon: float | None = None):
    """Dashboard data for one country. The client passes the polygon name and centre so countries outside the reference table still work."""
    cc = cc.upper()
    if not re.fullmatch(r"[A-Z]{2}", cc):
        raise HTTPException(status_code=400, detail="bad country code")
    ref, cap = COUNTRIES.get(cc), CAPITALS.get(cc)
    nm = (ref[0] if ref else None) or (name or cc)[:60]
    la, lo = (ref[1], ref[2]) if ref else (lat, lon)

    def go() -> dict[str, Any]:
        futs = {
            "index": _POOL.submit(_quote, ref[3] if ref else None),
            "fx": _POOL.submit(_quote, ref[4] if ref else None),
            "weather": _POOL.submit(_safe, (lambda: _weather(la, lo)) if la is not None and lo is not None else (lambda: None)),
            "news": _POOL.submit(_safe, lambda: _news_for(nm), []),
        }
        res = {k: f.result() for k, f in futs.items()}
        return {"cc": cc, "name": nm, "capital": cap[0] if cap else None, "tz": cap[1] if cap else None, "index_name": ref[3] if ref else None,
                "currency_pair": ref[4] if ref else None, **res}
    # a dashboard with a failed part (empty news, no weather) is only kept for 20 s so the next click retries it
    hit = _COUNTRY.get(cc)
    if hit and time.time() - hit[0] < (300 if hit[2] else 20):
        return hit[1]
    out = go()
    _COUNTRY[cc] = (time.time(), out, bool(out.get("news")) and out.get("weather") is not None)
    return out


# ------------------------------------------------------------------ geo news heat
@router.get("/newsheat")
def api_newsheat(ts: float | None = None):
    st = newsheat.STATE
    if ts is not None:
        snap = newsheat.snapshot_at(ts)
        if not snap:
            raise HTTPException(status_code=404, detail="no news-heat history yet")
        return {"ts": snap[0], "rows": snap[1], "heads": {}}
    return {"ts": st["ts"], "rows": st["rows"], "heads": st["heads"], "history": [t for t, _ in st["ring"]], "error": st["error"],
            "note": "Headlines per country over the last 24 h on Google News (capped near 100), with how many carry negative or severity keywords. A keyword count, not analysis."}


# ------------------------------------------------------------------ bitcoin transactions (fallback for the browser WebSocket)
@router.get("/btc")
def api_btc():
    def go() -> dict[str, Any]:
        d = None
        for _ in range(2):
            try:
                d = requests.get("https://blockchain.info/unconfirmed-transactions?format=json", headers=UA, timeout=15).json()
                break
            except Exception:  # noqa: BLE001
                continue
        if d is None:
            raise HTTPException(status_code=502, detail="blockchain.info did not answer")
        txs = [{"h": t["hash"], "btc": round(sum(o.get("value", 0) for o in t.get("out", [])) / 1e8, 4), "t": t.get("time")} for t in d.get("txs", [])]
        q = _chart("BTC-USD")
        return {"price": q["price"] if q else None, "txs": txs}
    return cached("world:btc", 6, go)


@router.get("/tv")
def api_tv():
    f = Path(__file__).resolve().parents[1] / "world" / "tv_channels.json"
    return {"channels": json.loads(f.read_text(encoding="utf-8"))}


@router.get("/status")
def api_status():
    st = sampler.STATE
    return {"planes_ts": st["planes"]["ts"], "planes_n": len(st["planes"]["rows"]), "mil_ts": st["mil"]["ts"], "mil_n": len(st["mil"]["rows"]), "ring": len(st["ring"]), "errors": st["errors"]}


def _heat_countries() -> list[tuple[str, str]]:
    feats = _load_countries()["features"]
    rows = sorted(({(f["properties"]["cc"], f["properties"]["name"]) for f in feats if re.fullmatch(r"[A-Z]{2}", f["properties"].get("cc") or "")}), key=lambda x: 0)
    pop = {f["properties"]["cc"]: f["properties"].get("pop") or 0 for f in feats}
    rows.sort(key=lambda x: -pop.get(x[0], 0))
    return [(cc, COUNTRIES[cc][0] if cc in COUNTRIES else name) for cc, name in rows[:100]]


def warm() -> None:
    sampler.start()
    newsheat.start(_heat_countries)

    def pre() -> None:
        for fn in (lambda: cached("world:countries", 7 * 86400, _load_countries), lambda: cached("world:radio", 6 * 3600, _radio_all), api_cams):
            _safe(fn)
    threading.Thread(target=pre, daemon=True, name="world-warm").start()


@router.get("/exchanges")
def api_exchanges():
    """Every stock exchange with a free feed: status, local time, session, main index, move and z vs its normal day, 5-min sparkline."""
    from app.world import exchanges

    return exchanges.snapshot()
