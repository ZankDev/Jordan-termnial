"""BMAP — global logistics & physical-asset map (top-level BMAP tab).

Layers (real data, free):
  ships       live AIS positions of tankers / cargo / LNG carriers around the world's shipping chokepoints and top ports,
              streamed from aisstream.io (free API key, pasted on the page or AISSTREAM_API_KEY env). Kept in memory.
  power       every power plant >= POWER_MIN_MW in the WRI Global Power Plant Database (fuel, capacity), disk-cached.
  infra       curated critical infrastructure: shipping chokepoints, LNG export terminals, crude export terminals,
              mega-refineries and the largest container ports (coordinates approximate, for map display).
  hazards     GDACS global disaster alerts (cyclones, floods, earthquakes, volcanoes, droughts, wildfires, with Green/Orange/Red
              alert levels) + NOAA NHC active tropical storms.
  risk        per chokepoint: headlines in the last 48 h (Google News) with conflict/disruption words, plus any Orange/Red hazard
              within HAZARD_RADIUS_KM -> a 0-100 risk score, and the commodities that flow through it.
  markets     Brent, WTI, Henry Hub gas, TTF gas, copper, wheat, gold — the prices those risks move.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/api/bmap")
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
CACHE = Path(__file__).resolve().parents[2] / "data_cache" / "bmap"
POWER_URL = "https://raw.githubusercontent.com/wri/global-power-plant-database/master/output_database/global_power_plant_database.csv"
POWER_MIN_MW = 1000
HAZARD_RADIUS_KM = 700
_mem: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _mem[key] = (time.time(), v)
    return v


# ------------------------------------------------------------------ curated infrastructure (approximate coordinates)
CHOKEPOINTS = [
    # id, name, lat, lon, box half-size deg, commodities, news query
    ("hormuz", "Strait of Hormuz", 26.57, 56.25, 1.2, ["crude", "LNG"], "Strait of Hormuz shipping"),
    ("bab", "Bab el-Mandeb / Red Sea", 12.6, 43.4, 1.5, ["crude", "containers"], "Red Sea Bab el-Mandeb shipping"),
    ("suez", "Suez Canal", 30.5, 32.35, 1.0, ["crude", "containers", "LNG"], "Suez Canal shipping"),
    ("malacca", "Strait of Malacca", 2.5, 101.4, 1.6, ["crude", "containers", "LNG"], "Strait of Malacca shipping"),
    ("panama", "Panama Canal", 9.1, -79.7, 0.8, ["containers", "LNG", "grain"], "Panama Canal shipping"),
    ("bosporus", "Bosporus", 41.12, 29.07, 0.6, ["crude", "grain"], "Bosporus tanker"),
    ("danish", "Danish Straits", 55.6, 11.0, 1.2, ["crude", "containers"], "Baltic Danish straits tanker"),
    ("gibraltar", "Strait of Gibraltar", 35.95, -5.6, 0.7, ["containers", "crude"], "Strait of Gibraltar shipping"),
    ("cape", "Cape of Good Hope", -34.6, 18.8, 1.5, ["crude", "containers"], "Cape of Good Hope shipping rerouting"),
    ("taiwan", "Taiwan Strait", 24.3, 119.6, 1.4, ["containers", "chips"], "Taiwan Strait shipping"),
    ("dover", "Dover Strait", 51.0, 1.5, 0.7, ["containers"], "English Channel Dover shipping"),
]
INFRA = [
    # kind, name, lat, lon, note
    ("lng", "Ras Laffan LNG (Qatar)", 25.91, 51.55, "world's largest LNG export hub"),
    ("lng", "Sabine Pass LNG (US)", 29.73, -93.87, "largest US LNG export terminal"),
    ("lng", "Corpus Christi LNG (US)", 27.88, -97.25, ""),
    ("lng", "Freeport LNG (US)", 28.94, -95.31, ""),
    ("lng", "Cameron LNG (US)", 30.03, -93.33, ""),
    ("lng", "Gorgon LNG (Australia)", -20.79, 115.45, ""),
    ("lng", "Yamal LNG / Sabetta (Russia)", 71.27, 72.07, ""),
    ("lng", "Bonny LNG (Nigeria)", 4.43, 7.17, ""),
    ("lng", "Gate LNG terminal (Rotterdam)", 51.96, 4.03, "EU import"),
    ("oil", "Ras Tanura (Saudi Arabia)", 26.64, 50.16, "largest crude export terminal"),
    ("oil", "Basra Oil Terminal (Iraq)", 29.68, 48.81, ""),
    ("oil", "Kharg Island (Iran)", 29.24, 50.31, ""),
    ("oil", "Fujairah (UAE)", 25.17, 56.36, "bunkering + storage hub"),
    ("oil", "Novorossiysk / CPC (Russia)", 44.72, 37.79, ""),
    ("oil", "Primorsk (Russia)", 60.35, 28.63, ""),
    ("oil", "Ceyhan (Turkey)", 36.87, 35.93, ""),
    ("oil", "LOOP (Louisiana Offshore Oil Port)", 28.88, -90.02, ""),
    ("oil", "Cushing OK (WTI delivery hub)", 35.98, -96.77, "WTI pricing point"),
    ("refinery", "Jamnagar refinery (India)", 22.35, 69.85, "world's largest"),
    ("refinery", "Ulsan refinery (S. Korea)", 35.48, 129.36, ""),
    ("refinery", "Port Arthur refinery (US)", 29.89, -93.95, ""),
    ("refinery", "Ruwais refinery (UAE)", 24.11, 52.73, ""),
    ("refinery", "Baytown refinery (US)", 29.75, -95.01, ""),
    ("refinery", "Pernis refinery (Rotterdam)", 51.88, 4.39, ""),
    ("refinery", "Jurong Island (Singapore)", 1.26, 103.7, ""),
    ("port", "Port of Shanghai", 30.63, 122.07, "busiest container port"),
    ("port", "Port of Singapore", 1.26, 103.84, ""),
    ("port", "Port of Ningbo-Zhoushan", 29.93, 121.85, ""),
    ("port", "Port of Rotterdam", 51.95, 4.14, "largest EU port"),
    ("port", "Port of Los Angeles / Long Beach", 33.74, -118.26, ""),
    ("port", "Port of Busan", 35.1, 129.04, ""),
    ("port", "Jebel Ali (Dubai)", 25.01, 55.06, ""),
]
MARKETS = [("BZ=F", "Brent"), ("CL=F", "WTI"), ("NG=F", "Henry Hub gas"), ("TTF=F", "TTF gas (EU)"), ("HG=F", "Copper"),
           ("ZW=F", "Wheat"), ("GC=F", "Gold")]
NEG_WORDS = ("attack", "strike", "seiz", "blockade", "clos", "missile", "drone", "houthi", "tension", "conflict", "war", "sanction",
             "disrupt", "delay", "halt", "suspend", "explosion", "fire", "collision", "aground", "piracy", "pirate", "drought", "low water",
             "military", "navy", "threat", "mine")


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


# ------------------------------------------------------------------ power plants
def _power() -> list[list[Any]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / "power_plants.json"
    if f.exists() and time.time() - f.stat().st_mtime < 30 * 86400:
        return json.loads(f.read_text())
    r = requests.get(POWER_URL, headers=UA, timeout=90)
    r.raise_for_status()
    out = []
    for x in csv.DictReader(io.StringIO(r.text)):
        try:
            mw = float(x["capacity_mw"] or 0)
            if mw >= POWER_MIN_MW:
                out.append([x["name"], x["country_long"] or x["country"], round(mw), float(x["latitude"]), float(x["longitude"]), x["primary_fuel"]])
        except (ValueError, KeyError):
            continue
    f.write_text(json.dumps(out))
    return out


@router.get("/static")
def api_static():
    try:
        plants = _cached("power", 86400, _power)
    except Exception:  # noqa: BLE001
        plants = []
    return {"chokepoints": [{"id": c[0], "name": c[1], "lat": c[2], "lon": c[3], "commodities": c[5]} for c in CHOKEPOINTS],
            "infra": [{"kind": k, "name": n, "lat": la, "lon": lo, "note": note} for k, n, la, lo, note in INFRA],
            "power": plants, "power_min_mw": POWER_MIN_MW}


# ------------------------------------------------------------------ hazards
def _hazards() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    try:
        j = requests.get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP", headers=UA, timeout=30).json()
        for f in j.get("features", []):
            p, g = f.get("properties", {}), f.get("geometry", {})
            if g.get("type") != "Point" or str(p.get("iscurrent")).lower() != "true":
                continue
            lon, lat = g["coordinates"][:2]
            items.append({"src": "GDACS", "type": p.get("eventtype"), "level": p.get("alertlevel"), "name": p.get("name"),
                          "country": p.get("country"), "lat": lat, "lon": lon, "from": p.get("fromdate"), "to": p.get("todate"),
                          "url": (p.get("url") or {}).get("report") if isinstance(p.get("url"), dict) else None})
    except Exception:  # noqa: BLE001
        pass
    try:
        n = requests.get("https://www.nhc.noaa.gov/CurrentStorms.json", headers=UA, timeout=20).json()
        for s in n.get("activeStorms", []):
            items.append({"src": "NHC", "type": "TC", "level": "Red" if s.get("classification") == "HU" and int(s.get("intensity") or 0) >= 96 else
                          "Orange" if s.get("classification") in ("HU", "TS") else "Green",
                          "name": f"{s.get('classification')} {s.get('name')} · {s.get('intensity')} kt", "country": None,
                          "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"), "from": s.get("lastUpdate"), "to": None,
                          "movement": f"{s.get('movementDir')}° {s.get('movementSpeed')} mph" if s.get("movementDir") is not None else None,
                          "url": (s.get("publicAdvisory") or {}).get("url")})
    except Exception:  # noqa: BLE001
        pass
    return {"at": time.time(), "items": items}


@router.get("/hazards")
def api_hazards():
    return _cached("hazards", 600, _hazards)


# ------------------------------------------------------------------ chokepoint risk
def _risk_one(c: tuple) -> dict[str, Any]:
    from app.api import invest_routes as inv

    cid, name, lat, lon, _box, comms, q = c
    news: list[dict[str, Any]] = []
    for attempt in range(3):  # Google News sometimes answers a burst of requests with an empty feed: retry, spaced out
        try:
            news = inv._google_news(q)
        except Exception:  # noqa: BLE001
            news = []
        if news:
            break
        time.sleep(1.5 * (attempt + 1))
    cutoff = time.time() - 48 * 3600
    recent = [n for n in news if (n.get("ts") or 0) >= cutoff]
    neg = [n for n in recent if any(w in n["title"].lower() for w in NEG_WORDS)]
    haz = [h for h in _cached("hazards", 600, _hazards)["items"]
           if h.get("lat") is not None and h.get("level") in ("Orange", "Red") and _km((lat, lon), (h["lat"], h["lon"])) <= HAZARD_RADIUS_KM]
    score = min(100, 12 * len(neg) + 2 * len(recent) + sum(35 if h["level"] == "Red" else 20 for h in haz))
    ships = _ships_near(lat, lon, c[4])
    return {"id": cid, "name": name, "lat": lat, "lon": lon, "commodities": comms, "risk": score,
            "level": "HIGH" if score >= 60 else "ELEVATED" if score >= 30 else "NORMAL",
            "headlines_48h": len(recent), "negative_48h": len(neg), "hazards": [h["name"] for h in haz],
            "top": [{"title": n["title"], "source": n.get("source"), "link": n.get("link"), "ts": n.get("ts")} for n in (neg or recent)[:4]],
            "ships": ships}


def _risk() -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(3) as pool:
        rows = list(pool.map(_risk_one, CHOKEPOINTS))
    rows.sort(key=lambda r: -r["risk"])
    news_ok = sum(1 for r in rows if r["headlines_48h"]) >= 2
    if not news_ok:  # the news source failed: do not keep a fake all-zero reading for 15 minutes
        _mem.pop("risk", None)
    return {"at": time.time(), "rows": rows, "news_ok": news_ok,
            "note": "Risk 0-100 = conflict/disruption headlines in the last 48 h + Orange/Red disasters within 700 km. A screen, not a forecast."}


@router.get("/risk")
def api_risk():
    out = _cached("risk", 900, _risk)
    if not out.get("news_ok"):
        _mem.pop("risk", None)  # retry on the next request instead of serving zeros
    return out


@router.get("/markets")
def api_markets():
    from app.api import invest_routes as inv

    def go():
        return [r for r in (inv._mon_row(s, n) for s, n in MARKETS) if r]
    return {"rows": _cached("markets", 120, go)}


# ------------------------------------------------------------------ AIS ships (aisstream.io)
_ships: dict[int, dict[str, Any]] = {}
_types: dict[int, int] = {}
_ais = {"running": False, "error": None, "msgs": 0, "started": None, "connected": False}
_ais_lock = threading.Lock()
SHIP_TTL = 45 * 60
MAX_SHIPS = 8000


def _ais_key() -> str | None:
    import os

    return os.environ.get("AISSTREAM_API_KEY") or db.meta_get("aisstream_key")


def _category(t: int | None) -> str:
    if t is None:
        return "unknown"
    if 80 <= t <= 89:
        return "tanker"
    if 70 <= t <= 79:
        return "cargo"
    if t in (35,):
        return "military"
    if 60 <= t <= 69:
        return "passenger"
    return "other"


def _boxes() -> list[list[list[float]]]:
    boxes = [[[la - b, lo - b], [la + b, lo + b]] for _i, _n, la, lo, b, _c, _q in CHOKEPOINTS]
    boxes += [[[la - 0.6, lo - 0.6], [la + 0.6, lo + 0.6]] for k, _n, la, lo, _note in INFRA if k in ("port", "lng", "oil")]
    return boxes


async def _ais_run(key: str) -> None:
    import websockets

    sub = {"APIKey": key, "BoundingBoxes": _boxes(), "FilterMessageTypes": ["PositionReport", "ShipStaticData", "StandardClassBPositionReport"]}
    while _ais["running"]:
        try:
            async with websockets.connect("wss://stream.aisstream.io/v0/stream", open_timeout=20, ping_interval=30, max_size=2**22) as ws:
                await ws.send(json.dumps(sub))
                _ais.update(connected=True, error=None)
                async for raw in ws:
                    if not _ais["running"]:
                        break
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    if m.get("error"):
                        _ais["error"] = str(m["error"])
                        break
                    meta = m.get("MetaData") or {}
                    mmsi = meta.get("MMSI")
                    if not mmsi:
                        continue
                    msg = m.get("Message") or {}
                    _ais["msgs"] += 1
                    if m.get("MessageType") == "ShipStaticData":
                        sd = msg.get("ShipStaticData") or {}
                        _types[mmsi] = sd.get("Type")
                        s = _ships.get(mmsi)
                        if s:
                            s.update(type=sd.get("Type"), cat=_category(sd.get("Type")), dest=(sd.get("Destination") or "").strip() or None)
                        continue
                    pr = msg.get("PositionReport") or msg.get("StandardClassBPositionReport") or {}
                    lat, lon = meta.get("latitude", pr.get("Latitude")), meta.get("longitude", pr.get("Longitude"))
                    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
                        continue
                    t = _types.get(mmsi)
                    with _ais_lock:
                        prev = _ships.get(mmsi, {})
                        _ships[mmsi] = {"mmsi": mmsi, "name": (meta.get("ShipName") or "").strip() or prev.get("name"), "lat": lat, "lon": lon,
                                        "sog": pr.get("Sog"), "cog": pr.get("Cog"), "hdg": pr.get("TrueHeading"), "type": t, "cat": _category(t),
                                        "dest": prev.get("dest"), "ts": time.time()}
                        if len(_ships) > MAX_SHIPS:
                            for k in sorted(_ships, key=lambda k: _ships[k]["ts"])[: len(_ships) - MAX_SHIPS]:
                                _ships.pop(k, None)
        except Exception as e:  # noqa: BLE001
            _ais.update(connected=False, error=f"{type(e).__name__}: {e}")
        _ais["connected"] = False
        if _ais["running"]:
            await asyncio.sleep(15)


def _ensure_ais() -> None:
    if _ais["running"]:
        return
    key = _ais_key()
    if not key:
        return
    _ais.update(running=True, started=time.time(), error=None)
    threading.Thread(target=lambda: asyncio.run(_ais_run(key)), daemon=True, name="ais-stream").start()


def _ships_near(lat: float, lon: float, box: float) -> dict[str, int]:
    now = time.time()
    out = {"tanker": 0, "cargo": 0, "other": 0, "stopped": 0}
    with _ais_lock:
        vals = list(_ships.values())
    for s in vals:
        if now - s["ts"] > SHIP_TTL or abs(s["lat"] - lat) > box or abs(s["lon"] - lon) > box:
            continue
        out[s["cat"] if s["cat"] in ("tanker", "cargo") else "other"] += 1
        if (s.get("sog") or 0) < 0.5:
            out["stopped"] += 1
    return out


@router.get("/ships")
def api_ships():
    _ensure_ais()
    now = time.time()
    with _ais_lock:
        rows = [s for s in _ships.values() if now - s["ts"] <= SHIP_TTL]
    return {"configured": bool(_ais_key()), "connected": _ais["connected"], "error": _ais["error"], "messages": _ais["msgs"],
            "count": len(rows), "ships": [[s["mmsi"], s.get("name"), round(s["lat"], 4), round(s["lon"], 4), s.get("sog"), s.get("cog"),
                                           s["cat"], s.get("dest")] for s in rows]}


class KeyReq(BaseModel):
    key: str


@router.post("/ais-key")
def api_ais_key(req: KeyReq):
    k = req.key.strip()
    if len(k) < 20:
        raise HTTPException(status_code=422, detail="That does not look like an aisstream.io API key.")
    db.meta_set("aisstream_key", k)
    _ais["running"] = False  # restart the stream with the new key
    time.sleep(0.2)
    _ensure_ais()
    return {"ok": True}


def warm() -> None:
    """Start the AIS stream at boot when a key is configured; prefetch the power-plant file."""
    _ensure_ais()
    threading.Thread(target=lambda: _cached("power", 86400, _power), daemon=True, name="bmap-power").start()
