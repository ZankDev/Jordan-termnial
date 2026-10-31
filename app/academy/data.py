"""Historical candles for the ACADEMY trainer (Binance public klines, no key). Full history per symbol / timeframe is downloaded once, kept on
disk under data_cache/academy/, and topped up when it is older than a bar."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import requests

DIR = Path(__file__).resolve().parents[2] / "data_cache" / "academy"
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
# how far back each timeframe is downloaded for the setup library (later bars are always available for free replay)
HISTORY_FROM = {"1d": "2017-09-01", "4h": "2019-01-01", "1h": "2021-06-01", "15m": "2024-01-01"}
SYMBOLS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
_LOCKS: dict[str, threading.Lock] = {}


def _ms(date: str) -> int:
    return int(time.mktime(time.strptime(date, "%Y-%m-%d"))) * 1000


def klines(symbol: str, tf: str, start_ms: int, limit: int = 1000) -> list[list[float]]:
    """One page of klines as [t_seconds, open, high, low, close, volume]."""
    last: Exception | None = None
    for _ in range(3):
        try:
            r = requests.get("https://api.binance.com/api/v3/klines", params={"symbol": symbol, "interval": tf, "startTime": int(start_ms), "limit": min(limit, 1000)}, headers=UA, timeout=25)
            r.raise_for_status()
            return [[k[0] // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in r.json()]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2)
    raise RuntimeError(f"Binance klines unavailable: {last}")


def history(symbol: str, tf: str) -> np.ndarray:
    """All candles for symbol/timeframe as an (n, 6) float array [t, o, h, l, c, v], downloading or topping up the disk cache."""
    if symbol not in SYMBOLS or tf not in TF_MS:
        raise ValueError("unknown symbol or timeframe")
    lock = _LOCKS.setdefault(f"{symbol}:{tf}", threading.Lock())
    with lock:
        DIR.mkdir(parents=True, exist_ok=True)
        f = DIR / f"{symbol}_{tf}.json"
        rows: list[list[float]] = json.loads(f.read_text()) if f.exists() else []
        step = TF_MS[tf]
        start = (int(rows[-1][0]) * 1000 + step) if rows else _ms(HISTORY_FROM[tf])
        changed = False
        while start < time.time() * 1000 - step:
            page = klines(symbol, tf, start)
            if not page:
                break
            rows.extend(page)
            changed = True
            start = int(page[-1][0]) * 1000 + step
            if len(page) < 1000:
                break
        # the newest bar may still be forming: drop it so replays only contain closed candles
        while rows and rows[-1][0] * 1000 + step > time.time() * 1000:
            rows.pop()
            changed = True
        if changed:
            f.write_text(json.dumps(rows, separators=(",", ":")))
    return np.array(rows, dtype=float)
