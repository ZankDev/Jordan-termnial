"""Background sampling for the WORLD map. Free public feeds are slow and rate-limited, so they are fetched here on a schedule and the
API only ever serves the last good sample (a failed fetch never blanks the map).

  * planes: OpenSky Network, all airborne aircraft worldwide (anonymous, ~10k rows, slow: about 30 s per call)
  * mil:    adsb.lol military feed
  * ring:   a gzip snapshot of the planes every 5 minutes, the last 6 hours (used by the time machine)
"""
from __future__ import annotations

import collections
import gzip
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}

PLANES_EVERY = 150      # seconds; ~580 of the ~4000 anonymous OpenSky credits per day
MIL_EVERY = 60
SNAP_EVERY = 300
RING_MAX = 72           # 6 hours at 5-minute steps

SNAP_DIR = Path(__file__).resolve().parents[2] / "data_cache" / "world"

STATE: dict[str, Any] = {
    "planes": {"ts": 0.0, "rows": []},
    "mil": {"ts": 0.0, "rows": []},
    "ring": collections.deque(maxlen=RING_MAX),   # (ts, gzip(json rows))
    "errors": {},
    "started": 0.0,
}
_LOCK = threading.Lock()
_started = False


def _opensky() -> list[list[Any]]:
    r = requests.get("https://opensky-network.org/api/states/all", headers=UA, timeout=70)
    if r.status_code == 429:
        raise RuntimeError("OpenSky rate limit")
    r.raise_for_status()
    rows = []
    for s in r.json().get("states") or []:
        # icao24, callsign, country, time_position, last_contact, lon, lat, baro_alt, on_ground, velocity, track, vrate, sensors, geo_alt, squawk
        if s[6] is None or s[5] is None or s[8]:
            continue
        rows.append([s[0], (s[1] or "").strip(), s[2], round(s[6], 3), round(s[5], 3), round((s[13] if s[13] is not None else s[7]) or 0), round(s[9] or 0), round(s[10] or 0), s[14] or ""])
    return rows


def _adsb_mil() -> list[list[Any]]:
    r = requests.get("https://api.adsb.lol/v2/mil", headers=UA, timeout=25)
    r.raise_for_status()
    rows = []
    for a in r.json().get("ac") or []:
        if a.get("lat") is None or a.get("lon") is None:
            continue
        alt = a.get("alt_baro")
        rows.append([a.get("hex", ""), (a.get("flight") or "").strip(), a.get("t") or "", round(a["lat"], 3), round(a["lon"], 3), round((alt if isinstance(alt, (int, float)) else 0) * 0.3048),
                     round((a.get("gs") or 0) * 0.5144), round(a.get("track") or 0), a.get("r") or ""])
    return rows


def _persist(ts: float, blob: bytes) -> None:
    """Keep the snapshots on disk so a restart does not empty the time machine; only the newest RING_MAX files are kept."""
    try:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        (SNAP_DIR / f"planes_{int(ts)}.json.gz").write_bytes(blob)
        for old in sorted(SNAP_DIR.glob("planes_*.json.gz"))[:-RING_MAX]:
            old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _restore() -> None:
    try:
        for f in sorted(SNAP_DIR.glob("planes_*.json.gz"))[-RING_MAX:]:
            m = re.search(r"planes_(\d+)", f.name)
            if m and time.time() - int(m.group(1)) < RING_MAX * SNAP_EVERY + 600:
                STATE["ring"].append((float(m.group(1)), f.read_bytes()))
    except Exception:  # noqa: BLE001
        pass


def _loop_planes() -> None:
    last_snap = 0.0
    wait = 5.0
    while True:
        time.sleep(wait)
        try:
            rows = _opensky()
            with _LOCK:
                STATE["planes"] = {"ts": time.time(), "rows": rows}
                STATE["errors"].pop("planes", None)
                if time.time() - last_snap >= SNAP_EVERY:
                    blob = gzip.compress(json.dumps(rows, separators=(",", ":")).encode(), 5)
                    STATE["ring"].append((time.time(), blob))
                    last_snap = time.time()
                    _persist(last_snap, blob)
            wait = PLANES_EVERY
        except Exception as e:  # noqa: BLE001 - keep serving the last sample
            STATE["errors"]["planes"] = f"{type(e).__name__}: {e}"
            wait = 600 if "rate limit" in str(e) else 60
            logger.warning("world planes sample failed: %s", e)


def _loop_mil() -> None:
    while True:
        try:
            rows = _adsb_mil()
            with _LOCK:
                STATE["mil"] = {"ts": time.time(), "rows": rows}
                STATE["errors"].pop("mil", None)
        except Exception as e:  # noqa: BLE001
            STATE["errors"]["mil"] = f"{type(e).__name__}: {e}"
        time.sleep(MIL_EVERY)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    STATE["started"] = time.time()
    _restore()
    threading.Thread(target=_loop_planes, daemon=True, name="world-planes").start()
    threading.Thread(target=_loop_mil, daemon=True, name="world-mil").start()


def snapshot_at(ts: float) -> tuple[float, list[Any]] | None:
    """The stored planes snapshot closest to ts (unix seconds), or None when the ring is empty."""
    with _LOCK:
        ring = list(STATE["ring"])
    if not ring:
        return None
    t, blob = min(ring, key=lambda x: abs(x[0] - ts))
    return t, json.loads(gzip.decompress(blob))


def snapshot_times() -> list[float]:
    with _LOCK:
        return [t for t, _ in STATE["ring"]]
