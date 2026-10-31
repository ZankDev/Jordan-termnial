"""Whole-market Polymarket data for the POLYMARKET › MARKETS page (read-only, public APIs, no keys).

Gamma  /events          — every open event with its markets, odds, 24h volume, liquidity, 1h/24h odds change
CLOB   /prices-history  — the odds line of one outcome token
data-api /trades        — recent large ("whale") trades across all markets

Everything is cached in-process (60-120 s) so the page can poll freely without hammering Polymarket.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable

from . import http

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    with _lock:  # one fetch per key at a time; others wait and reuse it
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        _cache[key] = (time.time(), val)
        return val


# tag label (lower case) -> page category; the first tag of an event that maps decides its category
_CAT = {
    "politics": "POLITICS", "elections": "POLITICS", "us politics": "POLITICS", "trump": "POLITICS", "world elections": "POLITICS",
    "geopolitics": "GEOPOLITICS", "world": "GEOPOLITICS", "middle east": "GEOPOLITICS", "ukraine": "GEOPOLITICS", "israel": "GEOPOLITICS",
    "iran": "GEOPOLITICS", "china": "GEOPOLITICS",
    "crypto": "CRYPTO", "bitcoin": "CRYPTO", "ethereum": "CRYPTO", "crypto prices": "CRYPTO",
    "economy": "ECONOMY", "fed": "ECONOMY", "fed rates": "ECONOMY", "inflation": "ECONOMY", "macro": "ECONOMY", "recession": "ECONOMY",
    "finance": "FINANCE", "business": "FINANCE", "stocks": "FINANCE", "earnings": "FINANCE", "ipos": "FINANCE", "commodities": "FINANCE",
    "tech": "TECH", "ai": "TECH", "science": "TECH", "space": "TECH",
    "sports": "SPORTS", "nba": "SPORTS", "nfl": "SPORTS", "soccer": "SPORTS", "mlb": "SPORTS", "nhl": "SPORTS", "tennis": "SPORTS", "esports": "SPORTS",
    "culture": "CULTURE", "pop culture": "CULTURE", "movies": "CULTURE", "music": "CULTURE", "awards": "CULTURE", "weather": "CULTURE",
}
CATEGORIES = ["POLITICS", "GEOPOLITICS", "ECONOMY", "FINANCE", "CRYPTO", "TECH", "SPORTS", "CULTURE", "OTHER"]


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _f(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _jl(v: Any) -> list:
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except (TypeError, ValueError):
        return []


def _market(m: dict[str, Any]) -> dict[str, Any] | None:
    if m.get("closed") or not m.get("active", True):
        return None
    outcomes, prices, tokens = _jl(m.get("outcomes")), [_f(p) for p in _jl(m.get("outcomePrices"))], _jl(m.get("clobTokenIds"))
    if not outcomes or not prices or None in prices:
        return None
    if max(prices) >= 0.995 and min(prices) <= 0.005:  # already decided (game over, release out) and waiting to be closed
        return None
    return {
        "q": m.get("question") or "", "label": m.get("groupItemTitle") or "", "slug": m.get("slug"), "cid": m.get("conditionId"),
        "outcomes": outcomes, "prices": prices, "tokens": tokens,
        "p": prices[0], "chg1h": _f(m.get("oneHourPriceChange")), "chg1d": _f(m.get("oneDayPriceChange")),
        "chg1w": _f(m.get("oneWeekPriceChange")),
        "vol24": _f(m.get("volume24hr")) or 0.0, "vol": _f(m.get("volumeNum") or m.get("volume")) or 0.0,
        "liq": _f(m.get("liquidityNum") or m.get("liquidity")) or 0.0,
        "spread": _f(m.get("spread")), "end": _ts(m.get("endDate")),
    }


def _event(e: dict[str, Any]) -> dict[str, Any] | None:
    markets = [x for x in (_market(m) for m in e.get("markets") or []) if x]
    if not markets:
        return None
    tags = [t.get("label") or "" for t in e.get("tags") or []]
    cat = next((_CAT[t.lower()] for t in tags if t.lower() in _CAT), "OTHER")
    # lead market first: in a one-winner event (neg-risk: candidates, rate outcomes) the favourite; otherwise (a game with its
    # props, a ladder of price targets) the most traded line
    markets.sort(key=(lambda m: m["p"]) if e.get("negRisk") else (lambda m: m["vol24"]), reverse=True)
    return {
        "id": e.get("id"), "title": e.get("title") or markets[0]["q"], "slug": e.get("slug"),
        "image": e.get("icon") or e.get("image"), "cat": cat, "tags": tags[:6],
        "vol24": _f(e.get("volume24hr")) or 0.0, "vol": _f(e.get("volume")) or 0.0, "vol1w": _f(e.get("volume1wk")) or 0.0,
        "liq": _f(e.get("liquidity")) or 0.0, "oi": _f(e.get("openInterest")),
        "end": _ts(e.get("endDate")), "start": _ts(e.get("startDate")), "comments": e.get("commentCount") or 0,
        "neg_risk": bool(e.get("negRisk")), "n_markets": len(markets), "markets": markets,
    }


def _fetch_events() -> list[dict[str, Any]]:
    def page(offset: int) -> list[dict[str, Any]]:
        return http.get_json(f"{GAMMA}/events", {"active": "true", "closed": "false", "archived": "false", "order": "volume24hr",
                                                 "ascending": "false", "limit": 100, "offset": offset}, timeout=20, retries=2) or []
    # Gamma pages are capped at 100: the 500 most-traded open events (fetched in parallel) cover ~all of the daily volume
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        pages = list(ex.map(lambda o: _safe(page, o), range(0, 500, 100)))
    seen, out = set(), []
    for e in (x for pg in pages for x in pg):
        ev = _event(e)
        if ev and ev["id"] not in seen:
            seen.add(ev["id"])
            out.append(ev)
    if not out:
        raise RuntimeError("Gamma returned no events")
    return out


def _safe(fn: Callable[..., list], *a: Any) -> list:
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001  (one failed page must not blank the whole page)
        return []


def events() -> list[dict[str, Any]]:
    return _cached("events", 90, _fetch_events)


def _slim(e: dict[str, Any], n_markets: int = 4) -> dict[str, Any]:
    return {**{k: v for k, v in e.items() if k != "markets"},
            "markets": [{k: m[k] for k in ("q", "label", "slug", "outcomes", "prices", "p", "chg1d", "chg1h", "vol24")} for m in e["markets"][:n_markets]]}


def overview(cat: str = "", sort: str = "vol24", q: str = "", limit: int = 80) -> dict[str, Any]:
    evs = events()
    now = time.time()
    cats = {c: {"n": 0, "vol24": 0.0} for c in CATEGORIES}
    for e in evs:
        cats[e["cat"]]["n"] += 1
        cats[e["cat"]]["vol24"] += e["vol24"]
    rows = [e for e in evs if (not cat or e["cat"] == cat) and (not q or q.lower() in e["title"].lower())]
    key = {"vol24": lambda e: -e["vol24"], "liq": lambda e: -e["liq"], "vol": lambda e: -e["vol"],
           "new": lambda e: -(e["start"] or 0), "ending": lambda e: (e["end"] or 9e12) if (e["end"] or 0) > now else 9e12}.get(sort)
    if key:
        rows.sort(key=key)
    mv = movers(cat)
    return {
        "kpis": {"events": len(evs), "markets": sum(e["n_markets"] for e in evs), "vol24": sum(e["vol24"] for e in evs),
                 "liq": sum(e["liq"] for e in evs), "vol1w": sum(e["vol1w"] for e in evs),
                 "top_mover": mv[0] if mv else None},
        "categories": [{"cat": c, **v} for c, v in cats.items() if v["n"]],
        "events": [_slim(e) for e in rows[:limit]], "total": len(rows), "at": now,
    }


def movers(cat: str = "", n: int = 30) -> list[dict[str, Any]]:
    """Biggest 24h odds changes among liquid, still-undecided markets (a price pinned near 0/1 is left out). In-play sports
    lines swing every minute, so SPORTS only shows when asked for."""
    out = []
    for e in events():
        if (cat and e["cat"] != cat) or (not cat and e["cat"] == "SPORTS"):
            continue
        for m in e["markets"]:
            if m["chg1d"] is None or m["vol24"] < 10_000 or not (0.03 <= (m["p"] or 0) <= 0.97):
                continue
            out.append({"event": e["title"], "slug": e["slug"], "cat": e["cat"], "image": e["image"], "q": m["label"] or m["q"],
                        "outcome": m["outcomes"][0], "p": m["p"], "chg1d": m["chg1d"], "chg1h": m["chg1h"], "vol24": m["vol24"]})
    out.sort(key=lambda r: -abs(r["chg1d"]))
    return out[:n]


def event_detail(slug: str) -> dict[str, Any]:
    def load() -> dict[str, Any]:
        page = http.get_json(f"{GAMMA}/events", {"slug": slug}, timeout=15, retries=2) or []
        if not page:
            raise LookupError(f"no Polymarket event '{slug}'")
        raw = page[0]
        e = _event(raw)
        if not e:
            raise LookupError("event has no open markets")
        e["description"] = (raw.get("description") or "")[:1500]
        e["resolution"] = raw.get("resolutionSource") or ""
        return e
    return _cached(f"ev:{slug}", 60, load)


def history(token: str, interval: str = "1w") -> list[list[float]]:
    if not re.fullmatch(r"\d{10,90}", token or ""):
        raise ValueError("bad token id")
    fid = {"1d": 5, "1w": 60, "1m": 240, "max": 1440}.get(interval, 60)

    def load() -> list[list[float]]:
        r = http.get_json(f"{CLOB}/prices-history", {"market": token, "interval": interval if interval != "1m" else "1m", "fidelity": fid},
                          timeout=15, retries=1)
        return [[p["t"], p["p"]] for p in (r or {}).get("history", [])]
    return _cached(f"h:{token}:{interval}", 120, load)


def whales(min_usd: float = 5000, limit: int = 60, market: str = "") -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": min(500, limit * 2), "filterType": "CASH", "filterAmount": min_usd, "takerOnly": "true"}
        if market:
            if not re.fullmatch(r"(0x[0-9a-fA-F]{64},?)+", market):
                raise ValueError("bad market id")
            params["market"] = market
        rows = http.get_json(f"{DATA}/trades", params, timeout=15, retries=1) or []
        out = []
        for t in rows:
            size, price = _f(t.get("size")) or 0.0, _f(t.get("price")) or 0.0
            out.append({"ts": t.get("timestamp"), "side": t.get("side"), "outcome": t.get("outcome"), "price": price,
                        "usd": size * price, "shares": size, "title": t.get("title"), "slug": t.get("eventSlug") or t.get("slug"),
                        "trader": t.get("name") or t.get("pseudonym") or (t.get("proxyWallet") or "")[:10],
                        "wallet": t.get("proxyWallet"), "tx": t.get("transactionHash")})
        return out[:limit]
    return _cached(f"w:{min_usd}:{limit}:{market}", 45, load)


_STOP = set("will the a an of in on to by be is for and or at as vs vs. with what who which win next before after than more less "
            "2025 2026 2027 end market price above below reach hit".split())


def linked_news(title: str, news: list[dict[str, Any]], n: int = 12) -> list[dict[str, Any]]:
    """Headlines that share at least two meaningful words with the event title (one if the title has a single key word)."""
    words = {w for w in re.findall(r"[a-z0-9$]{3,}", title.lower()) if w not in _STOP}
    if not words:
        return []
    need = 1 if len(words) <= 3 else 2
    hits = []
    for it in news:
        tw = set(re.findall(r"[a-z0-9$]{3,}", it.get("title", "").lower()))
        k = len(words & tw)
        if k >= need:
            hits.append((k, it))
    hits.sort(key=lambda x: (-x[0], -x[1].get("ts", 0)))
    return [it for _k, it in hits[:n]]
