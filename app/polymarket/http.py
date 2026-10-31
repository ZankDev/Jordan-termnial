"""Shared HTTP helper for the Polymarket vertical: one keep-alive requests.Session
per thread (connection pooling — a fresh TLS handshake per call was a large share
of every backfill/radar call's latency) plus a small retry loop.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
_local = threading.local()
_throttle_lock = threading.Lock()
_throttle_next: dict[str, float] = {}


def _throttle(key: str, min_interval: float) -> None:
    """Global pacing per key (e.g. "binance-hist"): callers wait so calls under one
    key start at least min_interval apart, however many threads share it. Used by
    the deep backfill so it can never crowd the live engine out of Binance's
    per-IP rate budget."""
    with _throttle_lock:
        now = time.time()
        start = max(now, _throttle_next.get(key, 0.0))
        _throttle_next[key] = start + min_interval
    wait = start - now
    if wait > 0:
        time.sleep(wait)


def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(UA)
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _local.s = s
    return s


def get_json(url: str, params: dict[str, Any] | None = None, timeout: float = 15, retries: int = 2, backoff: float = 0.6,
             throttle: tuple[str, float] | None = None) -> Any:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if throttle:
                _throttle(*throttle)
            r = session().get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(backoff * (attempt + 2))
                last = RuntimeError("429 rate limited")
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")
