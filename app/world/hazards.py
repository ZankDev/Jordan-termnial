"""Live natural-hazard feeds for the EVENTS MAP (all free, keyless, fetched server-side and cached by the router).

    USGS   earthquakes (GeoJSON summary feeds)
    NHC    Atlantic / East-Pacific storms: CurrentStorms.json + forecast track / cone KMZ
    GDACS  global cyclones (track + uncertainty cone), floods, volcanoes, wildfires, droughts
    EONET  NASA open natural events (severe storms with observed track, volcanoes, wildfires, ...)

Every fetcher returns plain dicts and raises on a hard failure; the router decides what to serve when a feed is down.
Storms from the three sources are merged by name so one cyclone is drawn once (NHC > GDACS > EONET by detail)."""
from __future__ import annotations

import datetime as dt
import io
import math
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

import requests

UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal; events map)"}
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson"
NHC = "https://www.nhc.noaa.gov/CurrentStorms.json"
GDACS_LIST = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"
GDACS_SEARCH = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GDACS_GEOM = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"
EONET = "https://eonet.gsfc.nasa.gov/api/v3/events"

QUAKE_FEEDS = {"hour": "all_hour", "day": "all_day", "week": "2.5_week", "month": "4.5_month", "sig": "significant_month"}
GDACS_TYPES = {"TC": "cyclone", "FL": "flood", "VO": "volcano", "WF": "wildfire", "DR": "drought", "EQ": "quake", "TS": "tsunami"}
EONET_KIND = {"wildfires": "wildfire", "volcanoes": "volcano", "severeStorms": "storm", "floods": "flood", "drought": "drought", "dustHaze": "dust",
              "landslides": "landslide", "tempExtremes": "heat", "snow": "snow", "seaLakeIce": "ice", "earthquakes": "quake", "manmade": "manmade", "waterColor": "water"}
# Saffir-Simpson-ish category from wind in knots
_SS = [(137, "C5"), (113, "C4"), (96, "C3"), (83, "C2"), (64, "C1"), (34, "TS"), (0, "TD")]


def cat_from_kt(kt: float | None) -> str:
    if kt is None:
        return "TS"
    return next(c for lim, c in _SS if kt >= lim)


def _get(url: str, params: dict[str, Any] | None = None, timeout: int = 25) -> requests.Response:
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


def _iso(s: str | None) -> int:
    if not s:
        return 0
    try:
        s = s.strip().replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return int(d.timestamp())
    except Exception:  # noqa: BLE001
        return 0


def _norm_name(s: str) -> str:
    s = re.sub(r"-\d{2}$", "", (s or "").strip().upper())
    s = re.sub(r"^(SUPER TYPHOON|TYPHOON|HURRICANE|TROPICAL CYCLONE|TROPICAL STORM|TROPICAL DEPRESSION|SUBTROPICAL STORM|POST-TROPICAL CYCLONE|POTENTIAL TROPICAL CYCLONE|CYCLONE)\s+", "", s)
    return s.strip()


# ------------------------------------------------------------------ earthquakes
def quakes(feed_key: str = "day") -> dict[str, Any]:
    feed = QUAKE_FEEDS.get(feed_key, "all_day")
    d = _get(USGS.format(feed=feed)).json()
    out = []
    for f in d.get("features", []):
        p, c = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or [0, 0, 0]
        if p.get("mag") is None or p.get("type", "earthquake") != "earthquake" and p.get("mag", 0) < 4:
            continue
        out.append({"id": f["id"], "lat": round(c[1], 3), "lon": round(c[0], 3), "depth": round(c[2] or 0, 1), "mag": round(p["mag"], 1),
                    "place": p.get("place") or "", "t": int((p.get("time") or 0) / 1000), "tsunami": bool(p.get("tsunami")), "alert": p.get("alert"),
                    "felt": p.get("felt"), "sig": p.get("sig") or 0, "url": p.get("url"), "type": p.get("type")})
    out.sort(key=lambda q: -q["t"])
    return {"feed": feed, "title": (d.get("metadata") or {}).get("title"), "n": len(out), "quakes": out, "ts": int(time.time())}


# ------------------------------------------------------------------ KML helpers (NHC KMZ)
def _kml_root(content: bytes) -> ET.Element:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            content = z.read(name)
    return ET.fromstring(content)


def _coords(text: str) -> list[list[float]]:
    pts = []
    for tok in (text or "").split():
        bits = tok.split(",")
        if len(bits) >= 2:
            try:
                pts.append([round(float(bits[1]), 3), round(float(bits[0]), 3)])
            except ValueError:
                pass
    return pts


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _nhc_cone(url: str) -> list[list[list[float]]]:
    root = _strip_ns(_kml_root(_get(url, timeout=20).content))
    rings = []
    for poly in root.iter("Polygon"):
        ob = poly.find("./outerBoundaryIs/LinearRing/coordinates")
        if ob is not None:
            pts = _coords(ob.text or "")
            if len(pts) > 3:
                rings.append(pts)
    return rings


def _nhc_track(url: str) -> list[dict[str, Any]]:
    root = _strip_ns(_kml_root(_get(url, timeout=20).content))
    pts = []
    for pm in root.iter("Placemark"):
        pt = pm.find("./Point/coordinates")
        if pt is None:
            continue
        c = _coords(pt.text or "")
        if not c:
            continue
        txt = " ".join(t for t in pm.itertext())
        kt = None
        m = re.search(r"(\d{2,3})\s*(?:knots|kt)", txt, re.I)
        if m:
            kt = float(m.group(1))
        m2 = re.search(r"Maximum Wind:\s*(\d+)", txt)
        if m2:
            kt = float(m2.group(1))
        valid = (pm.findtext("./name") or "").strip()
        pts.append({"lat": c[0][0], "lon": c[0][1], "kt": kt, "cat": cat_from_kt(kt), "label": valid[:40], "fc": True})
    return pts


def nhc_storms() -> list[dict[str, Any]]:
    d = _get(NHC, timeout=15).json()
    out = []
    for s in d.get("activeStorms") or []:
        try:
            kt = float(s.get("intensity") or 0) or None
        except ValueError:
            kt = None
        lat, lon = s.get("latitudeNumeric"), s.get("longitudeNumeric")
        if lat is None or lon is None:
            continue
        st: dict[str, Any] = {"id": f"nhc-{s.get('id')}", "src": "NHC", "name": s.get("name") or s.get("id"), "cls": s.get("classification"),
                              "kt": kt, "cat": cat_from_kt(kt), "mb": s.get("pressure"), "lat": lat, "lon": lon,
                              "move": f"{s.get('movementDir', '')}° {s.get('movementSpeed', '')} mph".strip() if s.get("movementDir") is not None else None,
                              "t": _iso(s.get("lastUpdate")), "url": (s.get("publicAdvisory") or {}).get("url"), "active": True, "track": [], "cone": [], "cone_kind": "official"}
        past = [{"lat": lat, "lon": lon, "kt": kt, "cat": cat_from_kt(kt), "label": "NOW", "fc": False}]
        try:
            if (s.get("trackCone") or {}).get("kmzFile"):
                st["cone"] = _nhc_cone(s["trackCone"]["kmzFile"])
        except Exception:  # noqa: BLE001
            pass
        fc: list[dict[str, Any]] = []
        try:
            if (s.get("forecastTrack") or {}).get("kmzFile"):
                fc = _nhc_track(s["forecastTrack"]["kmzFile"])
        except Exception:  # noqa: BLE001
            pass
        st["track"] = past + [p for p in fc if abs(p["lat"] - lat) + abs(p["lon"] - lon) > 0.05]
        out.append(st)
    return out


# ------------------------------------------------------------------ GDACS
def _gdacs_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = _get(url, params=params, timeout=30)
    if not r.content.strip():
        return {"features": []}  # GDACS answers an empty body when a search has no results
    return r.json()


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    return sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring)


def _gdacs_tc_geometry(eventid: int, episodeid: int) -> tuple[list[dict[str, Any]], list[list[list[float]]]]:
    g = _gdacs_json(GDACS_GEOM, {"eventtype": "TC", "eventid": eventid, "episodeid": episodeid})
    lines, points, cone = {}, {}, []
    for f in g.get("features", []):
        p, geo = f.get("properties") or {}, f.get("geometry") or {}
        cl = p.get("Class") or ""
        if cl.startswith("Line_Line_") and geo.get("type") == "LineString":
            lines[int(cl.rsplit("_", 1)[1])] = (geo["coordinates"], p.get("polygonlabel"), bool(p.get("forecast")))
        elif cl.startswith("Point_Polygon_Point_") and geo.get("type") == "Polygon":
            lat, lon = _ring_centroid(geo["coordinates"][0])
            points[int(cl.rsplit("_", 1)[1])] = (lat, lon, (p.get("polygonlabel") or "").replace(" UTC", ""))
        elif cl == "Poly_Cones":
            polys = [geo["coordinates"]] if geo.get("type") == "Polygon" else geo.get("coordinates") or []
            for poly in polys:
                if poly and len(poly[0]) > 3:
                    cone.append([[round(c[1], 3), round(c[0], 3)] for c in poly[0]])
    track = []
    n = max(len(lines) + 1, len(points))
    for i in range(n):
        ln = lines.get(i) or lines.get(i - 1)
        if i in points:
            lat, lon, label = points[i]
        elif i in lines:
            lon, lat = lines[i][0][0][0], lines[i][0][0][1]
            label = ""
        elif i - 1 in lines:
            lon, lat = lines[i - 1][0][-1][0], lines[i - 1][0][-1][1]
            label = ""
        else:
            continue
        lab_cat = (ln[1] if ln else "TS") or "TS"
        cat = {"HU": "C1", "TY": "C1", "STY": "C4"}.get(lab_cat, lab_cat if lab_cat in ("TD", "TS") else "TS")
        track.append({"lat": round(lat, 3), "lon": round(lon, 3), "kt": None, "cat": cat, "label": label, "fc": bool(ln[2]) if ln else False})
    return track, cone


def _gdacs_storm(f: dict[str, Any], with_geom: bool = True) -> dict[str, Any]:
    p, c = f["properties"], f["geometry"]["coordinates"]
    sev = p.get("severitydata") or {}
    kt = None
    m = re.search(r"([\d.]+)\s*km/h", sev.get("severitytext") or "")
    if m:
        kt = round(float(m.group(1)) / 1.852)
    st = {"id": f"gdacs-{p['eventid']}", "src": "GDACS", "name": p.get("eventname") or p.get("name"), "cls": (sev.get("severitytext") or "").split("(")[0].strip(),
          "kt": kt, "cat": cat_from_kt(kt), "mb": None, "lat": round(c[1], 3), "lon": round(c[0], 3), "move": None, "t": _iso(p.get("todate")),
          "url": (p.get("url") or {}).get("report"), "active": str(p.get("iscurrent")).lower() == "true", "alert": p.get("alertlevel"),
          "countries": [a.get("countryname") for a in (p.get("affectedcountries") or []) if a.get("countryname")][:8], "track": [], "cone": [], "cone_kind": "official"}
    if with_geom:
        try:
            st["track"], st["cone"] = _gdacs_tc_geometry(p["eventid"], p["episodeid"])
            if st["track"]:
                now = [pt for pt in st["track"] if not pt["fc"]]
                last = now[-1] if now else st["track"][0]
                st["lat"], st["lon"] = last["lat"], last["lon"]
        except Exception:  # noqa: BLE001
            pass
    return st


def gdacs_events() -> list[dict[str, Any]]:
    """Current GDACS events (everything, cyclones included) as flat dicts."""
    feats = list(_gdacs_json(GDACS_LIST).get("features", []))
    seen = {(f["properties"]["eventtype"], f["properties"]["eventid"]) for f in feats}
    since = (dt.datetime.utcnow() - dt.timedelta(days=30)).strftime("%Y-%m-%d")
    until = (dt.datetime.utcnow() + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    for t in ("TC", "FL", "VO", "DR"):
        try:
            for f in _gdacs_json(GDACS_SEARCH, {"eventlist": t, "fromdate": since, "todate": until}).get("features", []):
                k = (f["properties"]["eventtype"], f["properties"]["eventid"])
                if k not in seen:
                    seen.add(k)
                    feats.append(f)
        except Exception:  # noqa: BLE001
            continue
    out = []
    for f in feats:
        p = f.get("properties") or {}
        geo = f.get("geometry") or {}
        if geo.get("type") != "Point":
            continue
        sev = p.get("severitydata") or {}
        out.append({"id": f"gdacs-{p.get('eventtype')}-{p.get('eventid')}", "src": "GDACS", "kind": GDACS_TYPES.get(p.get("eventtype"), "other"), "code": p.get("eventtype"),
                    "title": p.get("name") or p.get("description"), "lat": round(geo["coordinates"][1], 3), "lon": round(geo["coordinates"][0], 3),
                    "t": _iso(p.get("todate")), "t0": _iso(p.get("fromdate")), "alert": p.get("alertlevel") or "Green", "sev": sev.get("severitytext"),
                    "country": p.get("country"), "current": str(p.get("iscurrent")).lower() == "true", "url": (p.get("url") or {}).get("report"),
                    "eventid": p.get("eventid"), "episodeid": p.get("episodeid"), "_f": f if p.get("eventtype") == "TC" else None})
    return out


# ------------------------------------------------------------------ EONET
def eonet_events() -> list[dict[str, Any]]:
    d = _get(EONET, {"status": "open", "days": 45}, timeout=30).json()
    out = []
    for e in d.get("events", []):
        cat = ((e.get("categories") or [{}])[0]).get("id", "")
        geoms = [g for g in (e.get("geometry") or []) if g.get("type") == "Point"]
        if not geoms:
            continue
        g = geoms[-1]
        out.append({"id": e["id"], "src": "EONET", "kind": EONET_KIND.get(cat, cat), "code": cat, "title": e.get("title"),
                    "lat": round(g["coordinates"][1], 3), "lon": round(g["coordinates"][0], 3), "t": _iso(g.get("date")), "t0": _iso(geoms[0].get("date")),
                    "alert": None, "sev": f"{g['magnitudeValue']:g} {g.get('magnitudeUnit') or ''}".strip() if g.get("magnitudeValue") is not None else None,
                    "url": ((e.get("sources") or [{}])[0]).get("url") or e.get("link"), "current": True,
                    "_track": [{"lat": round(x["coordinates"][1], 3), "lon": round(x["coordinates"][0], 3), "kt": x.get("magnitudeValue"), "t": _iso(x.get("date"))} for x in geoms] if cat == "severeStorms" else None})
    return out


# ------------------------------------------------------------------ projected cone for storms with no official forecast
def _dest(lat: float, lon: float, brg: float, km: float) -> tuple[float, float]:
    r, d = 6371.0, km / 6371.0
    la1, lo1, b = math.radians(lat), math.radians(lon), math.radians(brg)
    la2 = math.asin(math.sin(la1) * math.cos(d) + math.cos(la1) * math.sin(d) * math.cos(b))
    lo2 = lo1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(la1), math.cos(d) - math.sin(la1) * math.sin(la2))
    _ = r
    return math.degrees(la2), (math.degrees(lo2) + 540) % 360 - 180


def _bearing(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, float]:
    la1, la2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlo = math.radians(b["lon"] - a["lon"])
    y = math.sin(dlo) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
    brg = (math.degrees(math.atan2(y, x)) + 360) % 360
    hav = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return brg, 2 * 6371.0 * math.asin(math.sqrt(min(1.0, hav)))


def project(track: list[dict[str, Any]], hours: int = 72, step: int = 12) -> tuple[list[dict[str, Any]], list[list[float]]]:
    """Straight-line extrapolation of the last ~24 h of motion, with a cone that widens like the NHC average error
    (~80 km at 12 h to ~330 km at 72 h). Labelled 'projected' in the UI: it is not an official forecast."""
    obs = [p for p in track if p.get("t")]
    if len(obs) < 2:
        return [], []
    last = obs[-1]
    ref = next((p for p in reversed(obs[:-1]) if last["t"] - p["t"] >= 18 * 3600), obs[0])
    brg, km = _bearing(ref, last)
    hrs = max(1.0, (last["t"] - ref["t"]) / 3600)
    speed = min(km / hrs, 45.0)  # km/h, capped
    fc, left, right = [], [], []
    for h in range(step, hours + 1, step):
        la, lo = _dest(last["lat"], last["lon"], brg, speed * h)
        fc.append({"lat": round(la, 3), "lon": round(lo, 3), "kt": last.get("kt"), "cat": cat_from_kt(last.get("kt")), "label": f"+{h}h", "fc": True, "t": last["t"] + h * 3600})
        rad = 40 + 4 * h
        l1, o1 = _dest(la, lo, brg - 90, rad)
        l2, o2 = _dest(la, lo, brg + 90, rad)
        left.append([round(l1, 3), round(o1, 3)])
        right.append([round(l2, 3), round(o2, 3)])
    ring = [[last["lat"], last["lon"]]] + left + [[fc[-1]["lat"], fc[-1]["lon"]]] + list(reversed(right)) + [[last["lat"], last["lon"]]]
    return fc, ring


def eonet_storm(e: dict[str, Any]) -> dict[str, Any]:
    tr = e.get("_track") or []
    kt = tr[-1]["kt"] if tr else None
    track = [{"lat": p["lat"], "lon": p["lon"], "kt": p["kt"], "cat": cat_from_kt(p["kt"]), "label": dt.datetime.utcfromtimestamp(p["t"]).strftime("%d/%m %H:%M") if p["t"] else "", "fc": False, "t": p["t"]} for p in tr]
    fc, cone = project(track)
    return {"id": f"eonet-{e['id']}", "src": "EONET", "name": e["title"], "cls": e["title"].split(" ")[0] if e.get("title") else "", "kt": kt, "cat": cat_from_kt(kt), "mb": None,
            "lat": e["lat"], "lon": e["lon"], "move": None, "t": e["t"], "url": e.get("url"), "active": True, "track": track + fc, "cone": [cone] if cone else [], "cone_kind": "projected" if cone else None}


def merge_storms(nhc: list[dict[str, Any]], gd: list[dict[str, Any]], eo: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for src in (nhc, gd, eo):
        for s in src:
            k = _norm_name(s.get("name") or s["id"])
            if k in out:
                cur = out[k]
                # keep the more detailed one, but borrow an observed track if it has none
                if not any(not p["fc"] for p in cur["track"][1:]) and len([p for p in s["track"] if not p["fc"]]) > 2:
                    obs = [p for p in s["track"] if not p["fc"]]
                    cur["track"] = obs[:-1] + [p for p in cur["track"]]
                cur.setdefault("also", []).append(s["src"])
                continue
            out[k] = s
    return sorted(out.values(), key=lambda s: (-(s.get("kt") or 0)))
