"""Chart setups across the liquid US universe, each with its own measured base rate.

Setups (on daily bars, judged at the close):
  breakout_52w     close above the highest close of the prior 252 sessions, on >= 2x the 20-day average volume
  gap_and_hold     opened >= 3% above the prior close, closed at or above the open, volume >= 1.5x
  oversold_uptrend RSI(14) < 30 while the close is above the 200-day average
  squeeze_break    20-day range among the tightest 10% of the past year, then a close above the 20-day high
Base rate: every past occurrence across the universe (5 years), forward 5- and 20-session return MINUS SPY over the same window: mean, hit rate and n.
Only setups whose base rate is positive are offered as ideas; the rest are still shown with their numbers so nothing is hidden.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from app import db

logger = logging.getLogger(__name__)
SETUPS = ("breakout_52w", "gap_and_hold", "oversold_uptrend", "squeeze_break")
LABEL = {"breakout_52w": "52-week breakout on volume", "gap_and_hold": "Gap up and hold", "oversold_uptrend": "Oversold in an uptrend", "squeeze_break": "Squeeze breakout"}


def _bars(sym: str, rng: str = "5y") -> dict[str, np.ndarray] | None:
    from app.api import invest_routes as ir

    try:
        h = ir.cached(f"setup:{sym}:{rng}", 6 * 3600, lambda: ir._history(sym, rng, "1d"))
        c = np.array([x for x in h["candles"] if x[4]], dtype=float)
        if len(c) < 260:
            return None
        return {"t": c[:, 0], "o": c[:, 1], "h": c[:, 2], "l": c[:, 3], "c": c[:, 4], "v": c[:, 5]}
    except Exception:  # noqa: BLE001
        return None


def _rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    a = np.zeros_like(c)
    b = np.zeros_like(c)
    a[n], b[n] = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        a[i] = (a[i - 1] * (n - 1) + up[i]) / n
        b[i] = (b[i - 1] * (n - 1) + dn[i]) / n
    rs = np.divide(a, b, out=np.full_like(a, np.inf), where=b > 0)
    out = 100 - 100 / (1 + rs)
    out[:n + 1] = 50
    return out


def signals(b: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Boolean array per setup, True on the day the setup triggers."""
    c, o, v = b["c"], b["o"], b["v"]
    n = len(c)
    av20 = np.array([v[max(0, i - 20):i].mean() if i >= 20 else np.nan for i in range(n)])
    hi252 = np.array([c[i - 252:i].max() if i >= 252 else np.inf for i in range(n)])
    hi20 = np.array([c[i - 20:i].max() if i >= 20 else np.inf for i in range(n)])
    rng20 = np.array([(c[i - 20:i].max() - c[i - 20:i].min()) / c[i - 1] if i >= 20 else np.nan for i in range(n)])
    tight = np.array([rng20[i] <= np.nanpercentile(rng20[i - 252:i], 10) if i >= 272 else False for i in range(n)])
    sma200 = np.array([c[i - 200:i].mean() if i >= 200 else np.nan for i in range(n)])
    prev = np.roll(c, 1)
    rsi = _rsi(c)
    with np.errstate(invalid="ignore"):
        return {
            "breakout_52w": (c > hi252) & (v >= 2 * av20),
            "gap_and_hold": (o >= prev * 1.03) & (c >= o) & (v >= 1.5 * av20) & (np.arange(n) > 0),
            "oversold_uptrend": (rsi < 30) & (c > sma200),
            "squeeze_break": np.roll(tight, 1) & (c > hi20),
        }


def base_rates(symbols: list[str]) -> dict[str, Any]:
    """Forward 5/20-session excess return vs SPY after each setup, pooled over the universe. Cached 7 days."""
    hit = db.meta_get("setup_base_rates")
    if hit:
        d = json.loads(hit)
        if time.time() - d.get("at", 0) < 7 * 86400:
            return d
    spy = _bars("SPY")
    if spy is None:
        return {"at": 0, "rates": {}}
    spy_ix = {int(t): i for i, t in enumerate(spy["t"])}
    pooled: dict[str, dict[str, list[float]]] = {s: {"x5": [], "x20": []} for s in SETUPS}
    with ThreadPoolExecutor(8) as ex:
        allbars = dict(zip(symbols, ex.map(_bars, symbols)))
    for sym, b in allbars.items():
        if b is None:
            continue
        sig = signals(b)
        c = b["c"]
        for name, arr in sig.items():
            idx = np.where(arr[:-20])[0]
            last = -99
            for i in idx:
                if i - last < 10:  # one occurrence per 10 sessions per stock (no double counting of a run)
                    continue
                last = i
                j = spy_ix.get(int(b["t"][i]))
                if j is None or j + 20 >= len(spy["c"]):
                    continue
                for k, key in ((5, "x5"), (20, "x20")):
                    pooled[name][key].append((c[i + k] / c[i] - 1) - (spy["c"][j + k] / spy["c"][j] - 1))
    rates = {}
    for name, d in pooled.items():
        x5, x20 = np.array(d["x5"]), np.array(d["x20"])
        if len(x5) < 30:
            continue
        rates[name] = {"n": int(len(x5)), "x5": round(float(x5.mean()) * 100, 2), "hit5": round(float((x5 > 0).mean()) * 100), "x20": round(float(x20.mean()) * 100, 2),
                       "hit20": round(float((x20 > 0).mean()) * 100), "t20": round(float(x20.mean() / (x20.std() / np.sqrt(len(x20)))), 1) if x20.std() > 0 else 0.0}
    out = {"at": time.time(), "rates": rates, "universe": len([b for b in allbars.values() if b is not None])}
    db.meta_set("setup_base_rates", json.dumps(out))
    return out


def scan(symbols: list[str] | None = None) -> dict[str, Any]:
    """Which names trigger a setup on the latest session, with the setup's base rate. Ideas = setups with a positive 20-session base rate."""
    from app.api import market_routes as mr

    symbols = symbols or list(mr.UNIVERSE)
    br = base_rates(symbols)
    hits = []
    with ThreadPoolExecutor(8) as ex:
        allbars = dict(zip(symbols, ex.map(_bars, symbols)))
    for sym, b in allbars.items():
        if b is None:
            continue
        sig = signals(b)
        for name, arr in sig.items():
            if arr[-1]:
                r = br["rates"].get(name, {})
                hits.append({"symbol": sym, "setup": name, "label": LABEL[name], "close": round(float(b["c"][-1]), 2),
                             "chg": round(float(b["c"][-1] / b["c"][-2] - 1) * 100, 2), "base": r, "idea": bool(r and r.get("x20", 0) > 0 and r.get("t20", 0) >= 2)})
    hits.sort(key=lambda h: (-h["idea"], -(h["base"].get("x20") or -99)))
    return {"hits": hits, "rates": br["rates"], "universe": br.get("universe"), "at": time.time(),
            "note": "Base rate = average return vs SPY after every past occurrence across the universe (5y). t20 >= 2 means unlikely to be luck. Not advice."}
