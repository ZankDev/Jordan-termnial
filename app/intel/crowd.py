"""What the crowd prices: Polymarket's public markets on the topics that move equities (Fed, inflation, recession, war, tariffs, elections, oil, crypto, indices).

A Polymarket price is a probability people bet real money on. It is a sentiment gauge, not a forecast: thin markets are easy to move, so volume is shown next to
every number. `change1d` is the 24-hour move in that probability (Polymarket's own `oneDayPriceChange`). Public Gamma API, no key.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
GAMMA = "https://gamma-api.polymarket.com/events"
TOPICS = {
    "FED & RATES": r"\bfed\b|fomc|interest rate|rate cut|rate hike|powell|bps|inflation|\bcpi\b",
    "RECESSION & ECONOMY": r"recession|gdp|unemployment|jobs report|nonfarm|debt ceiling|shutdown|default",
    "WAR & GEOPOLITICS": r"\bwar\b|iran|israel|gaza|hezbollah|ukraine|russia|china|taiwan|nuclear|ceasefire|invade|strike|hostage|north korea",
    "TARIFFS & TRADE": r"tariff|trade deal|sanction|embargo",
    "ELECTIONS & POLITICS": r"election|president|prime minister|senate|congress|trump|netanyahu|knesset",
    "MARKETS & CRYPTO": r"s&p|nasdaq|\bspx\b|stock market|bitcoin|\bbtc\b|ethereum|\boil\b|crude|gold|nvidia|tesla|apple|microsoft|\bipo\b",
}
_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def _topic(text: str) -> str | None:
    t = text.lower()
    return next((name for name, rx in TOPICS.items() if re.search(rx, t)), None)


def _prob(m: dict[str, Any]) -> float | None:
    try:
        p = m.get("outcomePrices")
        p = json.loads(p) if isinstance(p, str) else p
        return float(p[0])
    except Exception:  # noqa: BLE001
        return None


def snapshot(max_age: float = 600.0) -> dict[str, Any]:
    if _cache["data"] and time.time() - _cache["ts"] < max_age:
        return _cache["data"]
    r = requests.get(GAMMA, params={"active": "true", "closed": "false", "order": "volume24hr", "ascending": "false", "limit": 250}, headers=UA, timeout=20)
    r.raise_for_status()
    groups: dict[str, list[dict[str, Any]]] = {k: [] for k in TOPICS}
    from app.intel.impact import BAD_TAGS

    bad = {t.lower() for t in BAD_TAGS} | {"games", "gaming", "counter strike", "cs2", "league of legends", "dota 2"}
    for ev in r.json():
        if {str(t.get("label", "")).lower() for t in ev.get("tags") or []} & bad:
            continue  # "strike" in a Counter-Strike match is not a war
        topic = _topic(f"{ev.get('title', '')} {ev.get('slug', '')}")
        if not topic:
            continue
        mk = []
        for m in ev.get("markets") or []:
            p = _prob(m)
            if p is None or m.get("closed"):
                continue
            mk.append({"question": m.get("groupItemTitle") or m.get("question"), "prob": round(p * 100, 1), "change1d": None if m.get("oneDayPriceChange") is None else round(float(m["oneDayPriceChange"]) * 100, 1),
                       "volume24h": float(m.get("volume24hr") or 0)})
        if not mk:
            continue
        mk.sort(key=lambda x: -x["prob"])
        groups[topic].append({"title": ev.get("title"), "url": f"https://polymarket.com/event/{ev.get('slug')}", "volume24h": float(ev.get("volume24hr") or 0), "ends": ev.get("endDate"),
                              "markets": mk[:4], "n_markets": len(mk)})
    data = {"topics": [{"topic": k, "events": sorted(v, key=lambda e: -e["volume24h"])[:6]} for k, v in groups.items() if v], "at": time.time(),
            "note": "Probabilities are what traders bet real money on: a sentiment gauge, not a forecast. Check volume - thin markets are easy to move."}
    _cache.update(ts=time.time(), data=data)
    return data
