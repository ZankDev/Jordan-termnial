"""Thin client over Polymarket's public Gamma API (no key needed) — discovers the
recurring 5-minute crypto "Up or Down" (buzzer) series and pages through their
sessions, live and resolved.

Verified live against the real API while designing this (2026-09-23):
  - GET /events?tag_slug=up-or-down&active=true&closed=false returns each event's
    `series` array with a clean `slug` (e.g. "btc-up-or-down-5m") AND a `recurrence`
    field ("5m"|"15m"|"hourly"|"4h"|"daily") — filtering recurrence=="5m" is the
    reliable cross-asset way to find buzzer markets (assets vary over time; btc/eth/
    xrp/sol/bnb/zec/hype/doge were live today).
  - GET /events?series_slug=<slug>&closed=true&order=endDate&ascending=false&limit=
    &offset= pages backward through resolved sessions.
  - Each event's single `markets[0]` has `endDate` (the true session close — the
    ONLY trustworthy timestamp; `startDate` is when the market was *listed*, often
    hours/days earlier, never the 5-minute window's start), `clobTokenIds` (a
    JSON-encoded 2-element array, index 0 = "Up" token, index 1 = "Down" token —
    matches the `outcomes` array order), and `closed`/`active`/`volume`.
"""
from __future__ import annotations

import json
import time
from typing import Any

from app.polymarket import http

GAMMA_BASE = "https://gamma-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
TIMEOUT = 20


def _get(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    data = http.get_json(f"{GAMMA_BASE}{path}", params, timeout=TIMEOUT, retries=2)
    return data if isinstance(data, list) else []


def _session_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the one binary market a buzzer event wraps into our own shape, or
    None if it's missing the fields a tradeable session needs (e.g. still pending
    token assignment)."""
    markets = event.get("markets") or []
    if not markets:
        return None
    m = markets[0]
    try:
        token_ids = json.loads(m.get("clobTokenIds") or "[]")
        outcomes = json.loads(m.get("outcomes") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if len(token_ids) < 2 or len(outcomes) < 2:
        return None
    end_date = m.get("endDate")
    if not end_date:
        return None
    end_ts = int(_parse_iso(end_date))
    series = (event.get("series") or [{}])[0]
    up_idx = 0 if str(outcomes[0]).strip().lower() == "up" else 1
    down_idx = 1 - up_idx
    return {
        "slug": m.get("slug") or event.get("slug"),
        "series_slug": series.get("slug"),
        "asset": (series.get("slug") or "").split("-")[0] or None,
        "recurrence": series.get("recurrence"),
        "end_ts": end_ts,
        "start_ts": end_ts - 300,  # buzzer sessions are always the 5-minute window ending at endDate
        "up_token_id": str(token_ids[up_idx]),
        "down_token_id": str(token_ids[down_idx]),
        "question": m.get("question"),
        "closed": bool(m.get("closed")),
        "active": bool(m.get("active")),
        "volume": float(m.get("volume") or 0),
    }


def _parse_iso(s: str) -> float:
    import datetime as dt

    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def discover_5m_series() -> list[dict[str, Any]]:
    """Every currently-active 'Up or Down' series whose recurrence is exactly 5m —
    the buzzer-eligible assets right now. Returns [{slug, asset, recurrence,
    volume24hr}, ...]."""
    import datetime as dt

    end_min = dt.datetime.fromtimestamp(time.time() - 5, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = _get("/events", {"tag_slug": "up-or-down", "active": "true", "closed": "false",
                              "end_date_min": end_min, "order": "endDate", "ascending": "true", "limit": 100})
    seen: dict[str, dict[str, Any]] = {}
    for event in events:
        for series in event.get("series") or []:
            if series.get("recurrence") != "5m":
                continue
            slug = series.get("slug")
            if slug and slug not in seen:
                seen[slug] = {
                    "slug": slug,
                    "asset": slug.split("-")[0],
                    "recurrence": "5m",
                    "volume24hr": float(series.get("volume24hr") or 0),
                }
    return sorted(seen.values(), key=lambda s: -s["volume24hr"])


def list_active_sessions(horizon_secs: int = 600) -> list[dict[str, Any]]:
    """Every 5m buzzer session that is running now or starts within `horizon_secs`,
    across all assets, soonest-ending first.

    Gamma keeps flagging long-expired events active=true (seen: Dec-2025 sessions
    still listed), and its default ordering buries the live windows — so ask for
    only events ending in the future (end_date_min) ordered by end date, and drop
    anything further out than the horizon client-side."""
    import datetime as dt

    now = time.time()
    end_min = dt.datetime.fromtimestamp(now - 5, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = _get("/events", {"tag_slug": "up-or-down", "active": "true", "closed": "false",
                              "end_date_min": end_min, "order": "endDate", "ascending": "true", "limit": 100})
    out = []
    for event in events:
        series = (event.get("series") or [{}])[0]
        if series.get("recurrence") != "5m":
            continue
        sess = _session_from_event(event)
        if sess and not sess["closed"] and now - 5 <= sess["end_ts"] <= now + horizon_secs:
            out.append(sess)
    return sorted(out, key=lambda s: s["end_ts"])


def list_closed_sessions(series_slug: str, limit: int = 100, offset: int = 0, end_before_ts: int | None = None) -> list[dict[str, Any]]:
    """One page of resolved sessions for a series, NEWEST first. `end_before_ts`
    pages by time cursor (end_date_max) instead of offset — Gamma rejects large
    offsets (422), so deep history has to be walked with a moving date cursor."""
    import datetime as dt

    params: dict[str, Any] = {"series_slug": series_slug, "closed": "true", "order": "endDate",
                              "ascending": "false", "limit": limit}
    if end_before_ts is not None:
        params["end_date_max"] = dt.datetime.fromtimestamp(end_before_ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        params["offset"] = offset
    out = []
    for event in _get("/events", params):
        sess = _session_from_event(event)
        if sess:
            out.append(sess)
    return out


def session_outcome(slug: str) -> bool | None:
    """True if the session resolved UP, False if DOWN, None if not (yet) resolved —
    read from Polymarket's own settled outcome prices, the source of truth for a real
    position's payoff (Polymarket resolves on Chainlink, not Binance)."""
    events = _get("/events", {"slug": slug})
    if not events:
        return None
    m = (events[0].get("markets") or [{}])[0]
    if not m.get("closed"):
        return None
    try:
        prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
        outcomes = [str(x).strip().lower() for x in json.loads(m.get("outcomes") or "[]")]
    except (ValueError, TypeError):
        return None
    if len(prices) < 2 or len(outcomes) < 2:
        return None
    up = prices[outcomes.index("up")] if "up" in outcomes else prices[0]
    if up >= 0.99:
        return True
    if up <= 0.01:
        return False
    return None
