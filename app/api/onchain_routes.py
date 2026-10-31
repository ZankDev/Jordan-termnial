"""SENTIMENT & ONCHAIN API (prefix /api/onchain): Fear and Greed history, perpetual futures funding / open interest (Bybit), trending coins
(CoinGecko) and the Bitcoin network (blockstream.info: mempool, fees, latest blocks). Exchange flows and wallet labels are paid data: not offered."""
from __future__ import annotations

import time
from typing import Any

import requests
from fastapi import APIRouter

from app.api.capital_routes import cached

router = APIRouter(prefix="/api/onchain", tags=["onchain"])
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}


def _get(url: str, **kw: Any) -> requests.Response:
    """Free public APIs drop the odd request: try twice before giving up."""
    last: Exception | None = None
    for _ in range(2):
        try:
            r = requests.get(url, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
    raise last  # type: ignore[misc]


@router.get("/fng")
def api_fng(limit: int = 90):
    def go() -> dict[str, Any]:
        d = _get("https://api.alternative.me/fng/", params={"limit": min(limit, 365)}, headers=UA, timeout=20).json()["data"]
        rows = [[int(x["timestamp"]), int(x["value"]), x["value_classification"]] for x in reversed(d)]
        return {"now": rows[-1] if rows else None, "history": rows}
    return cached(f"onchain:fng:{limit}", 3600, go)


@router.get("/perps")
def api_perps(top: int = 40):
    def go() -> dict[str, Any]:
        d = _get("https://api.bybit.com/v5/market/tickers", params={"category": "linear"}, headers=UA, timeout=25).json()["result"]["list"]
        rows = []
        for t in d:
            if not t["symbol"].endswith("USDT") or not t.get("fundingRate"):
                continue
            hours = float(t.get("fundingIntervalHour") or 8)
            fr = float(t["fundingRate"])
            rows.append({"symbol": t["symbol"], "price": float(t["lastPrice"]), "pct24": float(t.get("price24hPcnt") or 0) * 100, "funding": fr * 100, "apr": fr * (24 / hours) * 365 * 100,
                         "oi_usd": float(t.get("openInterestValue") or 0), "turnover": float(t.get("turnover24h") or 0), "next": int(t.get("nextFundingTime") or 0) // 1000})
        rows.sort(key=lambda r: -r["turnover"])
        return {"ts": time.time(), "rows": rows[:top], "n": len(rows)}
    return cached(f"onchain:perps:{top}", 30, go)


@router.get("/trending")
def api_trending():
    def go() -> list[dict[str, Any]]:
        d = _get("https://api.coingecko.com/api/v3/search/trending", headers=UA, timeout=20).json()
        out = []
        for c in d.get("coins", []):
            it = c["item"]
            out.append({"name": it["name"], "symbol": it["symbol"], "rank": it.get("market_cap_rank"), "thumb": it.get("thumb"), "pct24": (it.get("data", {}).get("price_change_percentage_24h") or {}).get("usd"),
                        "price": it.get("data", {}).get("price")})
        return out
    return {"coins": cached("onchain:trending", 600, go)}


@router.get("/btcnet")
def api_btcnet():
    def go() -> dict[str, Any]:
        mem = _get("https://blockstream.info/api/mempool", headers=UA, timeout=20).json()
        fees = _get("https://blockstream.info/api/fee-estimates", headers=UA, timeout=20).json()
        blocks = _get("https://blockstream.info/api/blocks", headers=UA, timeout=20).json()
        return {"mempool": {"count": mem.get("count"), "vsize": mem.get("vsize"), "total_fee": mem.get("total_fee")},
                "fees": {"fast": fees.get("1"), "medium": fees.get("3"), "slow": fees.get("6"), "economy": fees.get("144")},
                "blocks": [{"height": b["height"], "tx": b["tx_count"], "size": b["size"], "t": b["timestamp"]} for b in blocks[:8]]}
    return cached("onchain:btcnet", 30, go)
