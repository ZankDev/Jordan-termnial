"""ALT DATA (WORLD › ALT DATA) — "Spot it first. Trade it first."

  sites     oil tank farms, oil export terminals, container ports, mines, crop regions: coordinates (approximate, for imagery) + linked
            tickers + what to look for. Imagery is keyless and loaded by the browser: Esri World Imagery (latest), Esri World Imagery
            Wayback (dated archive releases) and NASA GIBS HLS (Harmonized Landsat-Sentinel, 30 m, near-daily).
  ships     AIS vessel counts around each chokepoint (aisstream.io via BMAP, only with AISSTREAM_API_KEY), sampled every 5 minutes into a
            rolling baseline; "ships stacking up" = stopped vessels above max(5, mean + 2 sd) of the last 24 h.
  alerts    every anomaly in one list: ship stacking, chokepoint risk HIGH, GNSS-interference cells, EVENTS MAP impact events, and
            unusual daily moves (|z| >= 2.5) in the tickers linked to the sites. Each lookup has a hard time budget."""
from __future__ import annotations

import collections
import concurrent.futures as cf
import re
import statistics
import threading
import time
from typing import Any

import requests

from app.api.capital_routes import _chart, cached

_POOL = cf.ThreadPoolExecutor(max_workers=24, thread_name_prefix="altdata")
BUDGET = 8.0
WAYBACK_CFG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"

TABS = {
    "tanks": {"title": "OIL TANKS", "look": "Floating roofs ride on the oil: a roof that sinks (bigger crescent shadow) = tank emptying = less oil in storage."},
    "terminals": {"title": "OIL TERMINALS", "look": "Watch the tankers load: count hulls at the berths / single-point moorings and the queue offshore."},
    "ports": {"title": "PORTS", "look": "Count the containers: full stacks and idle cranes = congestion; empty yards = falling trade."},
    "mines": {"title": "MINES", "look": "See output before the report: pit expansion, haul-road traffic, tailings and leach-pad growth."},
    "crops": {"title": "CROPS", "look": "See the harvest early: green-up, browning and harvested (bare) fields across the season."},
    "ship": {"title": "SHIP", "look": "Every tanker, live: ships stack up at a chokepoint before the headline."},
}

# id, tab, name, country, lat, lon, zoom, tickers, note  (coordinates approximate)
SITES: list[tuple[str, str, str, str, float, float, int, list[str], str]] = [
    ("cushing", "tanks", "Cushing tank farm", "US", 35.9395, -96.7480, 15, ["CL=F", "USO"], "WTI delivery hub; weekly EIA Cushing stocks move the front spread."),
    ("houston", "tanks", "Houston Ship Channel tanks", "US", 29.7300, -95.1350, 15, ["CL=F", "RB=F"], "Gulf coast storage between refineries and export docks."),
    ("europoort", "tanks", "Rotterdam Europoort tanks", "NL", 51.9530, 4.1250, 15, ["BZ=F", "TTF=F"], "ARA storage: Europe's crude + product buffer."),
    ("rastanura_t", "tanks", "Ras Tanura tank farm", "SA", 26.6430, 50.1580, 15, ["BZ=F", "2222.SR"], "Saudi Aramco's main export storage."),
    ("fujairah", "tanks", "Fujairah storage (FOTT)", "AE", 25.1700, 56.3550, 15, ["BZ=F"], "Outside Hormuz; Platts publishes weekly inventories."),
    ("ningbo_t", "tanks", "Ningbo-Zhoushan crude storage", "CN", 29.9180, 121.9680, 14, ["BZ=F", "FXI"], "China's largest crude import + strategic storage area."),
    ("rastanura", "terminals", "Ras Tanura export terminal", "SA", 26.6800, 50.1800, 14, ["BZ=F", "FRO"], "Largest crude export terminal in the world."),
    ("basra", "terminals", "Basra Oil Terminal (offshore)", "IQ", 29.6800, 48.8100, 14, ["BZ=F"], "Iraq's southern exports, ~3 mb/d."),
    ("primorsk", "terminals", "Primorsk", "RU", 60.3500, 28.6300, 14, ["BZ=F", "FRO"], "Russian Baltic crude exports (Urals)."),
    ("novorossiysk", "terminals", "Novorossiysk / CPC", "RU", 44.7200, 37.7900, 14, ["BZ=F"], "Black Sea crude: Russian Urals + Kazakh CPC blend."),
    ("loop", "terminals", "LOOP (Louisiana Offshore Oil Port)", "US", 28.8850, -90.0250, 13, ["CL=F"], "Only US port able to load VLCCs."),
    ("ingleside", "terminals", "Corpus Christi / Ingleside", "US", 27.8300, -97.2100, 14, ["CL=F", "FRO"], "Largest US crude export hub."),
    ("yangshan", "ports", "Shanghai Yangshan", "CN", 30.6200, 122.0700, 15, ["ZIM", "FXI"], "World's busiest container port."),
    ("lalb", "ports", "Los Angeles / Long Beach", "US", 33.7550, -118.2350, 15, ["ZIM", "MATX", "XRT"], "Gateway for ~40% of US container imports."),
    ("maasvlakte", "ports", "Rotterdam Maasvlakte II", "NL", 51.9600, 4.0300, 15, ["MAERSK-B.CO", "EWN"], "Europe's largest port."),
    ("singapore", "ports", "Singapore Pasir Panjang", "SG", 1.2700, 103.7600, 15, ["MAERSK-B.CO", "EWS"], "World's largest transshipment hub."),
    ("jebelali", "ports", "Jebel Ali", "AE", 25.0100, 55.0600, 15, ["MAERSK-B.CO"], "Middle East's biggest container port."),
    ("escondida", "mines", "Escondida copper mine", "CL", -24.2700, -69.0700, 13, ["HG=F", "BHP"], "World's largest copper mine (~5% of supply)."),
    ("chuqui", "mines", "Chuquicamata", "CL", -22.2900, -68.9000, 13, ["HG=F", "ECH"], "Codelco's giant open pit, now going underground."),
    ("grasberg", "mines", "Grasberg", "ID", -4.0500, 137.1100, 13, ["FCX", "HG=F", "GC=F"], "Copper + gold; Freeport-McMoRan."),
    ("whaleback", "mines", "Mt Whaleback, Pilbara", "AU", -23.3600, 119.6700, 13, ["BHP", "RIO", "TIO=F"], "Iron ore: one of the largest single open pits."),
    ("morenci", "mines", "Morenci", "US", 33.0800, -109.3500, 13, ["FCX", "HG=F"], "Largest US copper mine."),
    ("bingham", "mines", "Bingham Canyon", "US", 40.5200, -112.1500, 13, ["RIO", "HG=F"], "Kennecott: copper, gold, molybdenum."),
    ("iowa", "crops", "US Corn Belt (Iowa)", "US", 42.0300, -93.6200, 14, ["ZC=F", "ZS=F", "DE"], "Corn and soybeans; USDA crop-progress every Monday."),
    ("kansas", "crops", "Kansas wheat pivots", "US", 37.9700, -100.8700, 13, ["ZW=F", "KE=F"], "Hard red winter wheat under center pivots."),
    ("poltava", "crops", "Ukraine wheat belt (Poltava)", "UA", 49.5900, 34.5500, 13, ["ZW=F", "ZC=F"], "Black Sea wheat and corn."),
    ("sorriso", "crops", "Brazil soy (Mato Grosso)", "BR", -12.5500, -55.7100, 13, ["ZS=F", "BRL=X"], "Heart of Brazilian soy; harvest Jan-Apr."),
]


def sites() -> dict[str, Any]:
    return {"tabs": TABS, "sites": [{"id": i, "tab": t, "name": n, "country": c, "lat": la, "lon": lo, "zoom": z, "tickers": tk, "note": note}
                                    for i, t, n, c, la, lo, z, tk, note in SITES],
            "imagery": {"latest": "Esri World Imagery", "archive": "Esri World Imagery Wayback (dated releases)", "recent": "NASA GIBS HLS Sentinel-2 / Landsat 30 m"}}


def wayback() -> list[dict[str, Any]]:
    d = requests.get(WAYBACK_CFG, timeout=7, headers={"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}).json()
    out = []
    for k, v in d.items():
        m = re.search(r"(\d{4}-\d{2}-\d{2})", v.get("itemTitle") or "")
        if m and v.get("itemURL"):
            out.append({"id": int(k), "date": m.group(1), "url": v["itemURL"].replace("{level}", "{z}").replace("{row}", "{y}").replace("{col}", "{x}")})
    out.sort(key=lambda x: x["date"])
    return out


# ------------------------------------------------------------------ ship stacking baseline
HIST: dict[str, collections.deque] = {}
_HLOCK = threading.Lock()
_sampling = False
SAMPLE_EVERY = 300


def _sample_loop() -> None:
    from app.api import bmap_routes as bm

    while True:
        try:
            if bm._ais_key() and bm._ais["connected"]:  # noqa: SLF001
                now = time.time()
                with _HLOCK:
                    for cid, _n, lat, lon, box, _c, _q in bm.CHOKEPOINTS:
                        s = bm._ships_near(lat, lon, box)  # noqa: SLF001
                        HIST.setdefault(cid, collections.deque(maxlen=288)).append((now, s["stopped"], s["tanker"] + s["cargo"] + s["other"]))
        except Exception:  # noqa: BLE001
            pass
        time.sleep(SAMPLE_EVERY)


def start_sampler() -> None:
    global _sampling
    if _sampling:
        return
    _sampling = True
    from app.api import bmap_routes as bm

    bm._ensure_ais()  # noqa: SLF001
    threading.Thread(target=_sample_loop, daemon=True, name="altdata-ships").start()


def stacking() -> list[dict[str, Any]]:
    from app.api import bmap_routes as bm

    out = []
    configured = bool(bm._ais_key())  # noqa: SLF001
    for cid, name, lat, lon, box, comms, _q in bm.CHOKEPOINTS:
        with _HLOCK:
            h = list(HIST.get(cid, []))
        now = bm._ships_near(lat, lon, box) if configured else None  # noqa: SLF001
        base = [x[1] for x in h[:-1]]
        mean = statistics.mean(base) if len(base) >= 6 else None
        sd = statistics.pstdev(base) if len(base) >= 6 else None
        stopped = now["stopped"] if now else None
        thr = max(5.0, mean + 2 * sd) if mean is not None else None
        out.append({"id": cid, "name": name, "lat": lat, "lon": lon, "commodities": comms, "now": now, "stopped": stopped, "baseline": round(mean, 1) if mean is not None else None,
                    "threshold": round(thr, 1) if thr is not None else None, "samples": len(h), "stacking": bool(thr is not None and stopped is not None and stopped > thr),
                    "z": round((stopped - mean) / sd, 1) if sd and stopped is not None and mean is not None else None})
    return out


# ------------------------------------------------------------------ alerts
TICK_FOR = {"crude": ["BZ=F", "CL=F", "FRO"], "LNG": ["NG=F", "TTF=F"], "grain": ["ZW=F", "ZC=F"], "containers": ["ZIM", "MAERSK-B.CO"], "chips": ["TSM", "SOXX"]}


def _zscore(sym: str) -> dict[str, Any] | None:
    q = cached(f"alt:q:{sym}", 300, lambda: _chart(sym, "3mo", "1d") or {})
    sp = (q or {}).get("spark") or []
    if len(sp) < 12 or not q.get("prev"):
        return None
    rets = [sp[i] / sp[i - 1] - 1 for i in range(1, len(sp) - 1)]
    sd = statistics.pstdev(rets)
    d1 = q["price"] / q["prev"] - 1
    if abs(d1) > 0.25:  # a >25% daily move in these futures / large caps is a bad print or contract roll, not a signal
        return None
    return {"sym": sym, "d1": d1 * 100, "z": d1 / sd if sd else 0.0, "price": q["price"]}


def alerts() -> dict[str, Any]:
    from app.api import bmap_routes as bm
    from app.api import world_live_routes as wl
    from app.world import godseye

    syms = sorted({s for row in SITES for s in row[7]})
    f_z = {s: _POOL.submit(_zscore, s) for s in syms}
    f_imp = _POOL.submit(lambda: wl.api_all("day")["impact"])
    f_st = _POOL.submit(stacking)
    cf.wait([f_imp, f_st, *f_z.values()], timeout=BUDGET)
    ok = lambda f: f.done() and f.exception() is None  # noqa: E731
    out: list[dict[str, Any]] = []
    now = time.time()
    if ok(f_st):
        for s in f_st.result():
            if s["stacking"]:
                out.append({"kind": "ships", "asset": ", ".join(t for c in s["commodities"] for t in TICK_FOR.get(c, [])[:2]), "signal": f"Ships stacking up at {s['name']}",
                            "magnitude": f"{s['stopped']} stopped vs {s['baseline']} normal (z {s['z']})", "ts": now, "sev": 3, "source": "AIS · aisstream.io", "lat": s["lat"], "lon": s["lon"]})
    risk = bm._mem.get("risk")  # noqa: SLF001
    for r in (risk[1]["rows"] if risk else []):
        if r.get("level") == "HIGH":
            out.append({"kind": "chokepoint", "asset": ", ".join(t for c in r["commodities"] for t in TICK_FOR.get(c, [])[:2]), "signal": f"{r['name']}: disruption headlines",
                        "magnitude": f"risk {r['risk']}/100 · {r['negative_48h']} negative / 48 h", "ts": max([n.get("ts") or 0 for n in r.get("top") or []] or [now]),
                        "sev": 3, "source": "News + GDACS (BMAP)", "lat": r["lat"], "lon": r["lon"], "link": (r.get("top") or [{}])[0].get("link")})
    if godseye.ZSTATE.get("running"):
        for c in godseye.gnss_cells():
            if c[5] == "high":
                out.append({"kind": "gnss", "asset": "BZ=F, GC=F", "signal": f"GNSS interference near {c[0]:.1f}, {c[1]:.1f}", "magnitude": f"{c[2]}% of {c[3]} aircraft degraded",
                            "ts": godseye.ZSTATE["ts"], "sev": 2, "source": "adsb.lol NIC/NACp", "lat": c[0], "lon": c[1]})
    if ok(f_imp):
        for r in f_imp.result():
            if r["sev"] >= 0.45:
                out.append({"kind": r["kind"], "asset": ", ".join(a["sym"] for a in r["assets"][:4]), "signal": r["title"], "magnitude": f"severity {round(r['sev'] * 100)} · {', '.join(r['regions'][:2]) or 'global'}",
                            "ts": r["t"], "sev": 2 if r["sev"] < 0.7 else 3, "source": "EVENTS MAP", "lat": r["lat"], "lon": r["lon"]})
    zs = {s: f.result() for s, f in f_z.items() if ok(f) and f.result()}
    for s, z in zs.items():
        if abs(z["z"]) >= 2.5:
            site = next(row[2] for row in SITES if s in row[7])
            out.append({"kind": "price", "asset": s, "signal": f"Unusual move · linked to {site}", "magnitude": f"{z['d1']:+.2f}% today (z {z['z']:+.1f})", "ts": now, "sev": 2 if abs(z["z"]) < 3.5 else 3, "source": "Yahoo daily"})
    out.sort(key=lambda a: (-a["sev"], -(a["ts"] or 0)))
    return {"alerts": out[:40], "n": len(out), "ts": now, "checked": {"prices": len(zs), "prices_total": len(syms), "events": ok(f_imp), "ships": ok(f_st), "risk": bool(risk)}}
