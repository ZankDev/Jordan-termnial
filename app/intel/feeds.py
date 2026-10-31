"""News feeds: Israeli outlets (Hebrew + English) and global macro / geopolitics queries. Public RSS only, no keys.

Every item is normalised to {title, link, source, ts, lang, group}. `group` is where it came from (il = Israeli outlets, world = global queries) so the UI
can split them. A feed that fails is skipped and reported in `status()`; nothing here ever raises into the caller.
"""
from __future__ import annotations

import concurrent.futures as cf
import email.utils
import logging
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}

# name, url, lang
ISRAEL: list[tuple[str, str, str]] = [
    ("Ynet", "https://www.ynet.co.il/Integration/StoryRss2.xml", "he"),
    ("Ynet כלכלה", "https://www.ynet.co.il/Integration/StoryRss6.xml", "he"),
    ("Globes", "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=2", "he"),
    ("TheMarker", "https://www.themarker.com/cmlink/1.145", "he"),
    ("Maariv", "https://www.maariv.co.il/Rss/RssFeedsMivzakim", "he"),
    ("Walla", "https://rss.walla.co.il/feed/22", "he"),
    ("N12", "https://www.mako.co.il/rss/31750a2610f26110VgnVCM1000005201000aRCRD.xml", "he"),
    ("Israel Hayom", "https://www.israelhayom.co.il/rss.xml", "he"),
    ("Jerusalem Post", "https://www.jpost.com/Rss/RssFeedsHeadlines.aspx", "en"),
]


def _gn(q: str, lang: str = "en") -> str:
    hl, gl, ceid = ("iw", "IL", "IL:iw") if lang == "he" else ("en-US", "US", "US:en")
    return f"https://news.google.com/rss/search?q={quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"


WORLD: list[tuple[str, str, str]] = [
    ("War & security", _gn("war OR missile OR airstrike OR invasion OR ceasefire OR hostage when:1d"), "en"),
    ("Fed & inflation", _gn("Federal Reserve OR FOMC OR CPI OR inflation OR \"jobs report\" OR Powell when:1d"), "en"),
    ("Market stress", _gn("stock market crash OR selloff OR \"circuit breaker\" OR \"bank run\" OR default OR \"flash crash\" when:1d"), "en"),
    ("Oil & energy", _gn("oil price OR OPEC OR \"Strait of Hormuz\" OR pipeline attack when:1d"), "en"),
    ("ישראל בטחון", _gn("טילים OR תקיפה OR אזעקה OR פיגוע OR \"הפסקת אש\" when:1d", "he"), "he"),
    ("בנק ישראל וריבית", _gn("בנק ישראל ריבית OR אינפלציה OR \"מדד המחירים\" when:2d", "he"), "he"),
]

_status: dict[str, dict[str, Any]] = {}
_cache: dict[str, Any] = {"ts": 0.0, "items": []}
_lock = threading.Lock()


def _parse_ts(s: str | None) -> float:
    try:
        return email.utils.parsedate_to_datetime(s or "").timestamp()
    except Exception:  # noqa: BLE001
        return time.time()


def _one(name: str, url: str, lang: str, group: str) -> list[dict[str, Any]]:
    t0 = time.time()
    try:
        r = requests.get(url, headers=UA, timeout=12)
        r.raise_for_status()
        out = []
        for it in ET.fromstring(r.content).findall(".//item")[:40]:
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            src = (it.findtext("source") or "").strip()
            if src and title.endswith(f" - {src}"):
                title = title[: -len(src) - 3]
            if title and link.startswith(("http://", "https://")):
                out.append({"title": title, "link": link, "source": src or name, "feed": name, "ts": _parse_ts(it.findtext("pubDate")), "lang": lang, "group": group})
        _status[name] = {"ok": True, "n": len(out), "ms": int((time.time() - t0) * 1000), "at": time.time()}
        return out
    except Exception as e:  # noqa: BLE001 - one broken feed must not stop the rest
        _status[name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}", "at": time.time()}
        return []


def fetch_all(max_age: float = 240.0) -> list[dict[str, Any]]:
    """All feeds, newest first, de-duplicated. Cached for `max_age` seconds."""
    with _lock:
        if time.time() - _cache["ts"] < max_age and _cache["items"]:
            return _cache["items"]
    jobs = [(n, u, lg, "il") for n, u, lg in ISRAEL] + [(n, u, lg, "world") for n, u, lg in WORLD]
    items: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(8) as pool:
        for res in pool.map(lambda j: _one(*j), jobs):
            items += res
    seen, out = set(), []
    for it in sorted(items, key=lambda i: i["ts"], reverse=True):
        key = "".join(ch for ch in it["title"].lower() if ch.isalnum())[:70]
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    with _lock:
        _cache.update(ts=time.time(), items=out)
    return out


def status() -> dict[str, Any]:
    return {"feeds": [{"name": n, "group": g, **_status.get(n, {"ok": None})} for g, lst in (("il", ISRAEL), ("world", WORLD)) for n, _u, _l in lst]}
