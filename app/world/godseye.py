"""God's Eye layers for the WORLD › EVENTS MAP — ported / adapted from God's Eye View (MIT, (c) 2026 Bilawal Sidhu,
https://github.com/bilawalsidhu/gods-eye-view; see THIRD_PARTY_NOTICES.md). Python re-implementations of its keyless methods:

  wind      NOAA GFS 10 m wind (+ 2 m temperature / mean-sea-level pressure). God's Eye range-reads the GFS GRIB2 on AWS and decodes
            it with ecCodes; we read the same NOAA GFS run as JSON from the PacIOOS ERDDAP mirror (no GRIB decoder needed).
  nowcoast  NOAA nowCOAST WMS: global longwave-IR satellite mosaic, CONUS MRMS radar reflectivity, lightning strike density.
  flights   regional adsb.lol snapshots (250 NM around each watched zone) on top of the existing OpenSky / adsb.lol-mil sampler.
  gnss      navigation-integrity cells from the same adsb.lol snapshots, God's Eye classifier 'gev-nic-nacp-v2'
            (NACp < 8, or NIC < 7 unless NIC 0 with NACp >= 10, or gpsOkBefore set) aggregated with the gpsjam.org bands
            100 * max(0, bad - 1) / total over 0.5 deg cells, >= 3 aircraft, 30-minute window. Unvalidated: a signal, not proof of jamming.
  cyclones  NHC / CPHC GIS MapServer (forecast points, track, cone, past points, 7-day outlook areas) — the God's Eye method.
  sats      CelesTrak GP (TLE) groups, propagated with SGP4 in the browser (satellite.js).

Every upstream call has a timeout; every result is cached by the router with a TTL."""
from __future__ import annotations

import collections
import datetime as dt
import math
import threading
import time
import urllib.parse
from typing import Any

import requests

UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal; world map)", "Accept": "application/json"}
ERDDAP = "https://pae-paha.pacioos.hawaii.edu/erddap/griddap/ncep_global.csv0"
NOWCOAST = "https://nowcoast.noaa.gov/geoserver/observations/{service}/ows"
NHC_GIS = "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather_summary/MapServer"
ADSBLOL_POINT = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{nm}"
CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"

# ------------------------------------------------------------------ wind (GFS)
WIND_STEP = 4  # ERDDAP index stride on the 0.5 deg grid -> 2 deg


def _erddap_q(var: str, iso: str) -> str:
    return var + urllib.parse.quote(f"[({iso})][(-80):{WIND_STEP}:(80)][(0):{WIND_STEP}:(359.5)]")


def gfs_wind() -> dict[str, Any]:
    """u/v (m/s), 2 m temperature (deg C) and MSL pressure (hPa) on a 2 deg grid, valid at the GFS step nearest to now.
    Returned in the GRIB-JSON shape leaflet-velocity reads: rows north->south, columns 0->358 E."""
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{ERDDAP}?" + ",".join(_erddap_q(v, iso) for v in ("ugrd10m", "vgrd10m", "tmp2m", "prmslmsl"))
    r = requests.get(url, headers={"User-Agent": UA["User-Agent"]}, timeout=60)
    r.raise_for_status()
    rows = [ln.split(",") for ln in r.text.strip().splitlines() if ln and ln[0].isdigit()]
    if len(rows) < 1000:
        raise RuntimeError("GFS grid too small")
    valid = rows[0][0]
    lats = sorted({float(x[1]) for x in rows}, reverse=True)
    lons = sorted({float(x[2]) for x in rows})
    li, oi = {v: i for i, v in enumerate(lats)}, {v: i for i, v in enumerate(lons)}
    ny, nx = len(lats), len(lons)
    u, v, t, p = ([0.0] * (nx * ny) for _ in range(4))

    def num(s: str) -> float:
        try:
            f = float(s)
            return f if math.isfinite(f) else 0.0
        except ValueError:
            return 0.0
    for x in rows:
        k = li[float(x[1])] * nx + oi[float(x[2])]
        u[k], v[k] = round(num(x[3]), 2), round(num(x[4]), 2)
        t[k], p[k] = round(num(x[5]) - 273.15, 1), round(num(x[6]) / 100, 1)
    dx = lons[1] - lons[0]
    head = {"lo1": lons[0], "lo2": lons[-1], "la1": lats[0], "la2": lats[-1], "dx": dx, "dy": dx, "nx": nx, "ny": ny, "refTime": valid, "forecastTime": 0}
    return {"valid": valid, "model": "NOAA GFS 0.5 deg (via PacIOOS ERDDAP), resampled 2 deg", "level": "10 m above ground",
            "velocity": [{"header": {**head, "parameterCategory": 2, "parameterNumber": 2, "parameterUnit": "m.s-1"}, "data": u},
                         {"header": {**head, "parameterCategory": 2, "parameterNumber": 3, "parameterUnit": "m.s-1"}, "data": v}],
            "grid": head, "temp": t, "pressure": p}


# ------------------------------------------------------------------ nowCOAST WMS
NOWCOAST_PRODUCTS = {
    "clouds": {"service": "satellite", "layer": "global_longwave_imagery_mosaic", "title": "Global satellite infrared", "credit": "NOAA/NWS nowCOAST · GOES/Himawari/Meteosat IR mosaic"},
    "usradar": {"service": "weather_radar", "layer": "conus_base_reflectivity_mosaic", "title": "CONUS MRMS radar reflectivity", "credit": "NOAA/NWS nowCOAST · MRMS"},
    "lightning": {"service": "lightning_detection", "layer": "ldn_lightning_strike_density", "title": "Lightning density · 15 min", "credit": "NOAA/NWS nowCOAST · Vaisala NLDN/GLD360"},
}


def nowcoast_tile(product: str, bbox: str, width: int, height: int) -> bytes:
    spec = NOWCOAST_PRODUCTS[product]
    params = {"service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": spec["layer"], "styles": "", "format": "image/png",
              "transparent": "true", "crs": "EPSG:3857", "bbox": bbox, "width": width, "height": height}
    r = requests.get(NOWCOAST.format(service=spec["service"]), params=params, headers={"User-Agent": UA["User-Agent"]}, timeout=25)
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError("nowCOAST returned a non-image")
    return r.content


# ------------------------------------------------------------------ watched zones: flights + GNSS integrity
ZONES = [
    # id, name, lat, lon, assets
    ("ukraine", "Ukraine / Black Sea", 47.5, 34.0, [("ZW=F", "Wheat"), ("TTF=F", "EU gas"), ("ZC=F", "Corn")]),
    ("baltic", "Baltic / Kaliningrad", 55.5, 20.5, [("TTF=F", "EU gas"), ("EURUSD=X", "EUR/USD")]),
    ("levant", "Israel / Lebanon / Syria", 33.0, 35.5, [("BZ=F", "Brent"), ("GC=F", "Gold"), ("EIS", "iShares Israel")]),
    ("redsea", "Red Sea / Yemen", 15.0, 42.5, [("BZ=F", "Brent"), ("ZIM", "ZIM shipping"), ("FRO", "Frontline tankers")]),
    ("gulf", "Persian Gulf / Hormuz", 26.5, 54.0, [("BZ=F", "Brent"), ("CL=F", "WTI"), ("NG=F", "Natural gas")]),
    ("caucasus", "Caucasus / Caspian", 40.5, 47.0, [("BZ=F", "Brent")]),
    ("taiwan", "Taiwan Strait", 24.0, 120.0, [("TSM", "TSMC"), ("SOXX", "Semis ETF"), ("^TWII", "TAIEX")]),
    ("korea", "Korean Peninsula", 38.0, 127.0, [("EWY", "iShares Korea"), ("KRW=X", "USD/KRW")]),
    ("kashmir", "India / Pakistan", 33.0, 74.5, [("INR=X", "USD/INR"), ("INDA", "iShares India")]),
]
ZONE_NM = 250
ZONE_EVERY = 100        # seconds per full pass over the zones
GNSS_WINDOW = 30 * 60
GNSS_CELL = 0.5
GNSS_NIC, GNSS_NACP = 7, 8
QUAL_TYPES = {"adsb_icao", "mlat"}

ZSTATE: dict[str, Any] = {"zones": {}, "seen": collections.deque(), "errors": {}, "ts": 0.0, "running": False}
_ZLOCK = threading.Lock()


def _degraded(a: dict[str, Any]) -> bool | None:
    """God's Eye 'gev-nic-nacp-v2' per-aircraft rule; None when the aircraft does not qualify."""
    if a.get("type") not in QUAL_TYPES or a.get("version") not in (1, 2):
        return None
    if a.get("alt_baro") == "ground" or not isinstance(a.get("alt_baro"), (int, float)):
        return None
    if not isinstance(a.get("seen_pos"), (int, float)) or not 0 <= a["seen_pos"] <= 60:
        return None
    derived = lambda f: f in (a.get("tisb") or []) or f in (a.get("mlat") or [])  # noqa: E731
    nic = a.get("nic") if isinstance(a.get("nic"), (int, float)) and not derived("nic") else None
    nacp = a.get("nac_p") if isinstance(a.get("nac_p"), (int, float)) and not derived("nac_p") else None
    lost = isinstance(a.get("gpsOkBefore"), (int, float)) and a["gpsOkBefore"] > 0
    if nic is None and nacp is None and not lost:
        return None
    return bool(lost or (nacp is not None and nacp < GNSS_NACP) or (nic is not None and nic < GNSS_NIC and not (nic == 0 and (nacp or 0) >= 10)))


def _zone_fetch(z: tuple) -> dict[str, Any]:
    zid, _name, lat, lon, _assets = z
    r = requests.get(ADSBLOL_POINT.format(lat=lat, lon=lon, nm=ZONE_NM), headers=UA, timeout=15)
    if r.status_code == 429:
        raise RuntimeError("adsb.lol rate limit")
    r.raise_for_status()
    d = r.json()
    now = time.time()
    flights, gnss = [], []
    for a in d.get("ac") or []:
        if a.get("lat") is None or a.get("lon") is None:
            continue
        alt = a.get("alt_baro")
        mil = bool((a.get("dbFlags") or 0) & 1)
        flights.append([a.get("hex", ""), (a.get("flight") or "").strip(), round(a["lat"], 3), round(a["lon"], 3),
                        round(alt * 0.3048) if isinstance(alt, (int, float)) else 0, round((a.get("gs") or 0) * 0.5144), round(a.get("track") or 0), a.get("t") or "", 1 if mil else 0])
        bad = _degraded(a)
        if bad is not None:
            la, lo = a["lat"], a["lon"]
            if bad and isinstance(a.get("gpsOkLat"), (int, float)) and isinstance(a.get("gpsOkLon"), (int, float)):
                la, lo = a["gpsOkLat"], a["gpsOkLon"]
            gnss.append((now, a["hex"], la, lo, bad))
    return {"id": zid, "ts": now, "flights": flights, "gnss": gnss, "total": len(flights), "mil": sum(f[8] for f in flights)}


def _zone_loop() -> None:
    while True:
        for z in ZONES:
            try:
                res = _zone_fetch(z)
                with _ZLOCK:
                    prev = ZSTATE["zones"].get(z[0])
                    hist = (prev or {}).get("hist", collections.deque(maxlen=60))
                    hist.append((res["ts"], res["total"], res["mil"]))
                    res["hist"] = hist
                    ZSTATE["zones"][z[0]] = res
                    ZSTATE["seen"].extend(res.pop("gnss"))
                    cut = time.time() - GNSS_WINDOW
                    while ZSTATE["seen"] and ZSTATE["seen"][0][0] < cut:
                        ZSTATE["seen"].popleft()
                    ZSTATE["errors"].pop(z[0], None)
                    ZSTATE["ts"] = time.time()
            except Exception as e:  # noqa: BLE001 - keep the last good sample
                ZSTATE["errors"][z[0]] = f"{type(e).__name__}: {e}"[:120]
                if "rate limit" in str(e):
                    time.sleep(60)
            time.sleep(ZONE_EVERY / len(ZONES))


def start_zones() -> None:
    with _ZLOCK:
        if ZSTATE["running"]:
            return
        ZSTATE["running"] = True
    threading.Thread(target=_zone_loop, daemon=True, name="gx-zones").start()


def gnss_cells() -> list[list[Any]]:
    """[lat, lon, pct, n_aircraft, n_bad, band] per 0.5 deg cell with >= 3 aircraft seen in the window."""
    with _ZLOCK:
        seen = list(ZSTATE["seen"])
    cells: dict[tuple[int, int], dict[str, set]] = {}
    for _t, hx, la, lo, bad in seen:
        k = (math.floor(la / GNSS_CELL), math.floor(lo / GNSS_CELL))
        c = cells.setdefault(k, {"all": set(), "bad": set()})
        c["all"].add(hx)
        if bad:
            c["bad"].add(hx)
    out = []
    for (i, j), c in cells.items():
        n, b = len(c["all"]), len(c["bad"])
        if n < 3:
            continue
        pct = 100 * max(0, b - 1) / n
        out.append([round((i + 0.5) * GNSS_CELL, 2), round((j + 0.5) * GNSS_CELL, 2), round(pct, 1), n, b, "high" if pct >= 10 else "medium" if pct >= 2 else "low"])
    return out


def zone_flights() -> list[list[Any]]:
    with _ZLOCK:
        return [f for z in ZSTATE["zones"].values() for f in z["flights"]]


def zone_summary(mil_rows: list[list[Any]], cells: list[list[Any]]) -> list[dict[str, Any]]:
    out = []
    with _ZLOCK:
        zs = dict(ZSTATE["zones"])
        errs = dict(ZSTATE["errors"])
    for zid, name, lat, lon, assets in ZONES:
        z = zs.get(zid)
        rad = ZONE_NM * 1.852
        near = lambda la, lo: _km(lat, lon, la, lo) <= rad  # noqa: E731
        mil_glob = [m for m in mil_rows if near(m[3], m[4])]
        zc = [c for c in cells if near(c[0], c[1])]
        bad = sum(c[4] for c in zc)
        alln = sum(c[3] for c in zc)
        hist = list(z["hist"]) if z else []
        h1 = next((h for h in hist if time.time() - h[0] <= 3600), None)
        out.append({"id": zid, "name": name, "lat": lat, "lon": lon, "assets": [{"sym": s, "name": n} for s, n in assets],
                    "aircraft": z["total"] if z else None, "mil": max(z["mil"] if z else 0, len(mil_glob)),
                    "mil_calls": sorted({(m[1] or m[0]) for m in mil_glob if (m[1] or m[0])})[:6],
                    "gnss_cells_high": sum(1 for c in zc if c[5] == "high"), "gnss_cells_med": sum(1 for c in zc if c[5] == "medium"),
                    "gnss_bad_share": round(100 * bad / alln, 1) if alln else None, "trend_1h": (z["total"] - h1[1]) if z and h1 else None,
                    "ts": z["ts"] if z else None, "error": errs.get(zid)})
    return out


def _km(la1: float, lo1: float, la2: float, lo2: float) -> float:
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, h)))


# ------------------------------------------------------------------ NHC / CPHC GIS (God's Eye cyclone method)
def _gis(layer: int) -> list[dict[str, Any]]:
    r = requests.get(f"{NHC_GIS}/{layer}/query", params={"where": "1=1", "outFields": "*", "f": "geojson"}, headers=UA, timeout=25)
    r.raise_for_status()
    return r.json().get("features") or []


def nhc_gis() -> dict[str, Any]:
    """Forecast points / cone / past points per active storm (keyed by upper-case storm name) + 7-day outlook areas."""
    out: dict[str, dict[str, Any]] = {}
    for f in _gis(5):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        s = out.setdefault((p.get("stormname") or "").upper(), {"fc": [], "cone": [], "past": []})
        kt = p.get("maxwind")
        s["fc"].append({"lat": round(g["coordinates"][1], 2), "lon": round(g["coordinates"][0], 2), "kt": kt, "label": p.get("datelbl") or "", "tau": p.get("tau") or 0, "fc": True})
    for f in _gis(7):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates") or []
        s = out.setdefault((p.get("stormname") or "").upper(), {"fc": [], "cone": [], "past": []})
        for poly in polys:
            if poly and len(poly[0]) > 3:
                s["cone"].append([[round(c[1], 3), round(c[0], 3)] for c in poly[0]])
    for f in _gis(10):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        s = out.setdefault((p.get("stormname") or "").upper(), {"fc": [], "cone": [], "past": []})
        s["past"].append({"lat": round(g["coordinates"][1], 2), "lon": round(g["coordinates"][0], 2), "kt": p.get("intensity") or p.get("maxwind"), "label": p.get("dtg") or "", "fc": False})
    for s in out.values():
        s["fc"].sort(key=lambda x: x["tau"])
    outlook = []
    try:
        for f in _gis(3):
            p, g = f.get("properties") or {}, f.get("geometry") or {}
            polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates") or []
            rings = [[[round(c[1], 3), round(c[0], 3)] for c in poly[0]] for poly in polys if poly]
            prob = next((str(v) for k, v in p.items() if "prob7" in k.lower() or k.lower() in ("prob7day", "prob_7day")), None)
            risk = next((str(v) for k, v in p.items() if "risk7" in k.lower()), None)
            outlook.append({"rings": rings, "prob7": prob, "risk": risk, "basin": p.get("basin"), "id": p.get("objectid")})
    except Exception:  # noqa: BLE001 - outlook areas are a bonus
        pass
    return {"storms": out, "outlook": outlook}


# ------------------------------------------------------------------ satellites (CelesTrak GP / TLE)
SAT_GROUPS = {"stations": "Space stations", "gps-ops": "GPS", "weather": "Weather sats", "geo": "Geostationary"}


def celestrak(group: str) -> list[list[str]]:
    r = requests.get(CELESTRAK, params={"GROUP": group, "FORMAT": "tle"}, headers={"User-Agent": UA["User-Agent"]}, timeout=30)
    r.raise_for_status()
    lines = [ln.rstrip() for ln in r.text.splitlines() if ln.strip()]
    out = []
    for i in range(0, len(lines) - 2, 3):
        if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            out.append([lines[i].strip(), lines[i + 1], lines[i + 2]])
    if not out:
        raise RuntimeError("CelesTrak returned no TLEs")
    return out[:400]


CREDITS = [
    {"layer": "Methods", "source": "God's Eye View (MIT) by Bilawal Sidhu", "url": "https://github.com/bilawalsidhu/gods-eye-view", "note": "wind, nowCOAST, GNSS-integrity, NHC GIS and flight-fallback methods adapted; see THIRD_PARTY_NOTICES.md"},
    {"layer": "Earthquakes", "source": "USGS Earthquake Hazards Program", "url": "https://earthquake.usgs.gov", "note": "public domain"},
    {"layer": "Cyclones", "source": "NOAA NHC / CPHC (CurrentStorms, GIS MapServer)", "url": "https://www.nhc.noaa.gov", "note": "public domain"},
    {"layer": "Cyclones / hazards", "source": "GDACS (EC JRC / UN OCHA)", "url": "https://www.gdacs.org", "note": "free use with attribution"},
    {"layer": "Natural events", "source": "NASA EONET", "url": "https://eonet.gsfc.nasa.gov", "note": "public domain"},
    {"layer": "Wind / temp / pressure", "source": "NOAA GFS via PacIOOS ERDDAP", "url": "https://pae-paha.pacioos.hawaii.edu/erddap/", "note": "public domain"},
    {"layer": "IR clouds, US radar, lightning", "source": "NOAA/NWS nowCOAST", "url": "https://nowcoast.noaa.gov", "note": "lightning derived from Vaisala NLDN/GLD360"},
    {"layer": "Global radar", "source": "RainViewer", "url": "https://www.rainviewer.com", "note": "free tiles, attribution required"},
    {"layer": "Flights", "source": "OpenSky Network · adsb.lol", "url": "https://adsb.lol", "note": "adsb.lol data ODbL 1.0; OpenSky anonymous API"},
    {"layer": "GNSS integrity", "source": "adsb.lol + gpsjam.org banding", "url": "https://gpsjam.org/faq", "note": "unvalidated signal: low NIC/NACp has other causes"},
    {"layer": "Ships", "source": "aisstream.io", "url": "https://aisstream.io", "note": "only when AISSTREAM_API_KEY is set"},
    {"layer": "Satellites", "source": "CelesTrak GP data · satellite.js (MIT)", "url": "https://celestrak.org", "note": "SGP4 in the browser"},
    {"layer": "Basemap", "source": "Esri World Dark Gray Canvas", "url": "https://www.esri.com", "note": "Esri, HERE, Garmin, OSM contributors"},
]
