"""Geo news heat: how much news each country is making right now, and how much of it reads as trouble.

Every 20 minutes the biggest ~100 countries are searched on Google News (last 24 hours). Per country we keep the headline count (capped by the
feed at ~100), how many headlines carry negative words and how many carry severity words (attack, protest, earthquake, blackout...).
It is a keyword count, not analytics: use it to see where to look, then read the headlines. Snapshots are kept for the time machine."""
from __future__ import annotations

import concurrent.futures as cf
import email.utils
import json
import logging
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any, Callable

import requests

from app.api.ai_routes import NEG, POS

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
FILE = Path(__file__).resolve().parents[2] / "data_cache" / "world" / "newsheat.json"
EVERY = 1200
SEVERE = ("killed", "dead", "death", "attack", "bomb", "explosion", "shooting", "war", "missile", "drone", "strike", "protest", "riot", "coup", "earthquake", "flood", "wildfire", "storm",
          "hurricane", "typhoon", "crisis", "hostage", "terror", "collapse", "blackout", "evacuat", "clashes", "sanction")

STATE: dict[str, Any] = {"ts": 0.0, "rows": {}, "heads": {}, "ring": deque(maxlen=144), "error": None}
_started = False


def _load() -> None:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        for s in d.get("snaps", []):
            STATE["ring"].append((s["ts"], s["rows"]))
        STATE["heads"] = d.get("heads", {})
        if STATE["ring"]:
            STATE["ts"], STATE["rows"] = STATE["ring"][-1]
    except Exception:  # noqa: BLE001
        pass


def _save() -> None:
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps({"snaps": [{"ts": t, "rows": r} for t, r in STATE["ring"]], "heads": STATE["heads"]}, separators=(",", ":")), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _one(cc: str, name: str) -> tuple[str, list[int], list[dict[str, Any]]] | None:
    try:
        r = requests.get("https://news.google.com/rss/search", params={"q": f'"{name}" when:1d', "hl": "en-US", "gl": "US", "ceid": "US:en"}, headers=UA, timeout=15)
        r.raise_for_status()
        items = ET.fromstring(r.content).findall(".//item")
    except Exception:  # noqa: BLE001
        return None
    n = neg = sev = pos = 0
    heads: list[dict[str, Any]] = []
    for it in items:
        title = (it.findtext("title") or "").strip()
        low = title.lower()
        n += 1
        neg += any(w in low for w in NEG)
        pos += any(w in low for w in POS)
        bad = any(w in low for w in SEVERE)
        sev += bad
        if len(heads) < 6 and (bad or len(heads) < 3):
            ts = time.time()
            try:
                ts = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
            except Exception:  # noqa: BLE001
                pass
            src = (it.findtext("source") or "").strip()
            heads.append({"title": title[: -len(src) - 3] if src and title.endswith(f" - {src}") else title, "link": (it.findtext("link") or "").strip(), "source": src, "ts": ts, "sev": bool(bad)})
    return cc, [n, neg, sev, pos], heads


def run_once(countries: list[tuple[str, str]]) -> None:
    rows: dict[str, list[int]] = {}
    heads: dict[str, list[dict[str, Any]]] = {}
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        for res in pool.map(lambda c: _one(*c), countries):
            if res:
                rows[res[0]], heads[res[0]] = res[1], res[2]
    if not rows:
        raise RuntimeError("no news feed answered")
    now = time.time()
    STATE.update(ts=now, rows=rows, heads=heads, error=None)
    STATE["ring"].append((now, rows))
    _save()


def start(get_countries: Callable[[], list[tuple[str, str]]]) -> None:
    global _started
    if _started:
        return
    _started = True
    _load()

    def loop() -> None:
        time.sleep(25)
        while True:
            try:
                run_once(get_countries())
            except Exception as e:  # noqa: BLE001
                STATE["error"] = f"{type(e).__name__}: {e}"
                logger.warning("news heat sample failed: %s", e)
            time.sleep(EVERY)

    threading.Thread(target=loop, daemon=True, name="world-newsheat").start()


def snapshot_at(ts: float) -> tuple[float, dict[str, list[int]]] | None:
    ring = list(STATE["ring"])
    return min(ring, key=lambda x: abs(x[0] - ts)) if ring else None
