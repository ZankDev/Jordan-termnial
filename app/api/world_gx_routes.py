"""God's Eye layers API (prefix /api/world/gx) for the WORLD › EVENTS MAP: GFS wind, nowCOAST tiles, flights, GNSS integrity,
watched-zone airspace, ships + chokepoints, satellites and per-source credits. Upstreams are proxied and TTL-cached here so the
browser never talks to them directly (one exception: map tiles from Esri / RainViewer). Methods adapted from God's Eye View (MIT),
see app/world/godseye.py and THIRD_PARTY_NOTICES.md. Background samplers start on first use, not at boot."""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from app.api.capital_routes import cached
from app.world import godseye, sampler

router = APIRouter(prefix="/api/world/gx", tags=["world-godseye"])
_TILES: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()
_TLOCK = threading.Lock()
TILE_TTL, TILE_MAX = 240, 600
_EMPTY_PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def _try(key: str, ttl: float, fn, default):
    try:
        return cached(key, ttl, fn), None
    except Exception as e:  # noqa: BLE001
        return default, f"{type(e).__name__}: {e}"[:160]


# ------------------------------------------------------------------ wind / temperature / pressure
@router.get("/wind")
def api_wind():
    d, err = _try("gx:wind", 1800, godseye.gfs_wind, None)
    if d is None:
        raise HTTPException(status_code=502, detail=f"GFS wind unavailable: {err}")
    return d


# ------------------------------------------------------------------ nowCOAST tiles (z/x/y -> WMS bbox in EPSG:3857)
@router.get("/nowcoast/{product}/{z}/{x}/{y}.png")
def api_nowcoast(product: str, z: int, x: int, y: int):
    if product not in godseye.NOWCOAST_PRODUCTS or not (0 <= z <= 10) or not (0 <= x < 2**z) or not (0 <= y < 2**z):
        raise HTTPException(status_code=404, detail="unknown tile")
    span = 2 * math.pi * 6378137 / 2**z
    o = math.pi * 6378137
    bbox = f"{-o + x * span},{o - (y + 1) * span},{-o + (x + 1) * span},{o - y * span}"
    key = f"{product}:{z}:{x}:{y}"
    with _TLOCK:
        hit = _TILES.get(key)
        if hit and time.time() - hit[0] < TILE_TTL:
            _TILES.move_to_end(key)
            return Response(hit[1], media_type="image/png", headers={"Cache-Control": "public, max-age=180"})
    try:
        png = godseye.nowcoast_tile(product, bbox, 256, 256)
    except Exception:  # noqa: BLE001 - a failed tile is transparent, never an error page in the map
        return Response(hit[1] if hit else _EMPTY_PNG, media_type="image/png", headers={"Cache-Control": "no-store"})
    with _TLOCK:
        _TILES[key] = (time.time(), png)
        while len(_TILES) > TILE_MAX:
            _TILES.popitem(last=False)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=180"})


@router.get("/nowcoast")
def api_nowcoast_meta():
    return {"products": godseye.NOWCOAST_PRODUCTS}


# ------------------------------------------------------------------ flights (+ military, regional adsb.lol fallback)
def _flights() -> dict[str, Any]:
    sampler.start()
    godseye.start_zones()
    st = sampler.STATE
    rows: dict[str, list[Any]] = {}
    for r in st["planes"]["rows"]:  # [icao, call, country, lat, lon, alt, vel, track, squawk]
        rows[r[0]] = [r[0], r[1], r[3], r[4], r[5], r[6], r[7], "", 0]
    regional = 0
    for f in godseye.zone_flights():  # [hex, call, lat, lon, alt_m, gs_ms, trk, type, mil]
        if f[0] not in rows:
            regional += 1
        rows[f[0]] = f
    for m in st["mil"]["rows"]:  # [hex, call, type, lat, lon, alt_m, gs_ms, trk, reg]
        rows[m[0]] = [m[0], m[1], m[3], m[4], m[5], m[6], m[7], m[2], 1]
    return {"ts": max(st["planes"]["ts"], st["mil"]["ts"], godseye.ZSTATE["ts"]), "n": len(rows), "rows": list(rows.values()),
            "global_ts": st["planes"]["ts"], "global_n": len(st["planes"]["rows"]), "mil_n": len(st["mil"]["rows"]), "regional_extra": regional,
            "errors": {**st["errors"], **{f"zone:{k}": v for k, v in godseye.ZSTATE["errors"].items()}}}


@router.get("/flights")
def api_flights():
    return cached("gx:flights", 15, _flights)


# ------------------------------------------------------------------ GNSS integrity + watched zones
@router.get("/gnss")
def api_gnss():
    godseye.start_zones()
    cells = godseye.gnss_cells()
    return {"cells": cells, "n": len(cells), "ts": godseye.ZSTATE["ts"], "window_min": godseye.GNSS_WINDOW // 60,
            "zones": [{"id": z[0], "name": z[1], "lat": z[2], "lon": z[3], "radius_km": round(godseye.ZONE_NM * 1.852)} for z in godseye.ZONES],
            "method": "God's Eye 'gev-nic-nacp-v2' per aircraft (NACp<8, NIC<7, or GPS-lost) · gpsjam.org bands 100*max(0,bad-1)/total: medium >=2%, high >=10% · 0.5° cells, >=3 aircraft, 30 min",
            "caveat": "Low navigation accuracy reported by aircraft. Suspected interference is one possible cause; avionics faults also report low values. Not validated."}


@router.get("/zones")
def api_zones():
    sampler.start()
    godseye.start_zones()
    return {"zones": godseye.zone_summary(sampler.STATE["mil"]["rows"], godseye.gnss_cells()), "ts": godseye.ZSTATE["ts"], "radius_nm": godseye.ZONE_NM}


# ------------------------------------------------------------------ ships + chokepoints (reuses BMAP's aisstream client and risk screen)
COMMODITY_TICKERS = {
    "crude": [("BZ=F", "Brent"), ("CL=F", "WTI"), ("FRO", "Frontline (tankers)")],
    "LNG": [("NG=F", "Henry Hub"), ("TTF=F", "TTF gas"), ("FLNG", "FLEX LNG")],
    "grain": [("ZW=F", "Wheat"), ("ZC=F", "Corn")],
    "containers": [("ZIM", "ZIM"), ("MAERSK-B.CO", "Maersk"), ("MATX", "Matson")],
    "chips": [("TSM", "TSMC"), ("SOXX", "Semis ETF")],
}
CHOKE_NOTE = {
    "hormuz": "~20% of world oil and ~20% of LNG (Qatar) pass here",
    "bab": "Red Sea route to Suez; Houthi attacks forced Cape re-routing in 2024",
    "suez": "~12% of world trade; closure (Ever Given 2021) spiked container rates",
    "malacca": "the main lane from the Gulf to China / Japan / Korea",
    "panama": "US Gulf LNG + grain to Asia; drought cut transits in 2023",
    "bosporus": "Russian / Kazakh (CPC) crude and Black Sea grain",
    "danish": "Baltic exit for Russian crude and products",
    "gibraltar": "Mediterranean gateway",
    "cape": "the detour when the Red Sea is closed (+10-14 days to Europe)",
    "taiwan": "most of the world's container traffic between NE Asia and the rest",
    "dover": "busiest strait in the world by traffic",
}


@router.get("/ships")
def api_ships():
    from app.api import bmap_routes as bm

    d = bm.api_ships()
    d["hint"] = None if d["configured"] else "Set AISSTREAM_API_KEY (free key from aisstream.io) to stream live AIS ships around the chokepoints."
    return d


@router.get("/chokepoints")
def api_chokepoints():
    from app.api import bmap_routes as bm

    bm._ensure_ais()  # noqa: SLF001 - no-op without a key
    risk = bm._mem.get("risk")  # noqa: SLF001
    if not risk or time.time() - risk[0] > 900:
        def bg() -> None:
            try:
                bm.api_risk()
            except Exception:  # noqa: BLE001
                pass
        if not getattr(api_chokepoints, "_busy", False):
            api_chokepoints._busy = True  # type: ignore[attr-defined]
            threading.Thread(target=lambda: (bg(), setattr(api_chokepoints, "_busy", False)), daemon=True, name="gx-risk").start()
    rk = {r["id"]: r for r in (risk[1]["rows"] if risk else [])}
    configured = bool(bm._ais_key())  # noqa: SLF001
    rows = []
    for cid, name, lat, lon, box, comms, _q in bm.CHOKEPOINTS:
        r = rk.get(cid, {})
        ships = bm._ships_near(lat, lon, box) if configured else None  # noqa: SLF001
        tick, seen = [], set()
        for c in comms:
            for s, n in COMMODITY_TICKERS.get(c, []):
                if s not in seen:
                    seen.add(s)
                    tick.append({"sym": s, "name": n, "for": c})
        rows.append({"id": cid, "name": name, "lat": lat, "lon": lon, "box": box, "commodities": comms, "note": CHOKE_NOTE.get(cid, ""),
                     "ships": ships, "risk": r.get("risk"), "level": r.get("level"), "negative_48h": r.get("negative_48h"), "hazards": r.get("hazards") or [],
                     "top": (r.get("top") or [])[:2], "tickers": tick[:6]})
    rows.sort(key=lambda x: -(x["risk"] or 0))
    return {"rows": rows, "ais": {"configured": configured, "connected": bm._ais["connected"], "error": bm._ais["error"], "messages": bm._ais["msgs"]},  # noqa: SLF001
            "risk_ready": bool(risk), "note": "Risk 0-100 = disruption headlines (48 h) + Orange/Red disasters within 700 km (BMAP screen). Vessel counts need an aisstream.io key."}


# ------------------------------------------------------------------ satellites
@router.get("/sats")
def api_sats(group: str = "stations"):
    if group not in godseye.SAT_GROUPS:
        raise HTTPException(status_code=400, detail="unknown group")
    tles, err = _try(f"gx:sats:{group}", 6 * 3600, lambda: godseye.celestrak(group), [])
    return {"group": group, "name": godseye.SAT_GROUPS[group], "n": len(tles), "tles": tles, "error": err}


@router.get("/credits")
def api_credits():
    return {"credits": godseye.CREDITS}
