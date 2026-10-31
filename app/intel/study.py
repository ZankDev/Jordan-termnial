"""Event study: what actually happened to the market after a given kind of event. Built from price history only, so every number is checkable.

For a trigger (a VIX spike, a big S&P drop, a new 52-week high, an oil spike) it finds every historical day the trigger fired (events within 5 trading days of
the previous one are merged so one crisis is not counted ten times), then measures the S&P 500's forward return over 1, 5 and 20 trading days and compares it with
the average over ALL days. The t-statistic says how far the result sits from that baseline in standard errors: below about 2 the difference is within noise.
Past behaviour is not a promise; small samples are flagged. This is the evidence base for "what moves the market", not a trading signal by itself.
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

TRIGGERS = {
    "vix_spike": ("^VIX", "VIX daily change at least +{t}%", 20.0),
    "spx_drop": ("^GSPC", "S&P 500 daily drop of at least {t}%", 2.0),
    "spx_surge": ("^GSPC", "S&P 500 daily gain of at least {t}%", 2.0),
    "oil_spike": ("CL=F", "WTI crude daily gain of at least {t}%", 5.0),
    "gold_spike": ("GC=F", "Gold daily gain of at least {t}%", 3.0),
    "spx_new_high": ("^GSPC", "S&P 500 closes at a 252-day high (threshold unused)", 0.0),
}
HORIZONS = (1, 5, 20)
_cache: dict[str, Any] = {}


def _closes(symbol: str) -> tuple[list[str], np.ndarray]:
    from app.api import invest_routes as ir

    key = f"study:{symbol}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    h = ir._history(symbol, "max", "1mo") if False else ir._history(symbol, "30y", "1d")
    pts = [(time.strftime("%Y-%m-%d", time.gmtime(c[0])), c[4]) for c in h["candles"] if c[4]]
    out = ([p[0] for p in pts], np.array([p[1] for p in pts], dtype=float))
    _cache[key] = (time.time(), out)
    return out


def run(trigger: str, threshold: float | None = None) -> dict[str, Any]:
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger; choose one of {', '.join(TRIGGERS)}")
    sym, label, default_t = TRIGGERS[trigger]
    t = default_t if threshold is None else float(threshold)
    d_t, c_t = _closes(sym)
    d_s, c_s = _closes("^GSPC")
    spx = dict(zip(d_s, c_s))
    ret = {d_t[i]: (c_t[i] / c_t[i - 1] - 1) * 100 for i in range(1, len(c_t))}
    days = [d for d in d_s]
    idx = {d: i for i, d in enumerate(days)}
    fired = []
    for i in range(252, len(d_t)):
        d = d_t[i]
        if trigger == "spx_new_high":
            ok = c_t[i] >= c_t[i - 252:i].max()
        elif trigger == "spx_drop":
            ok = ret.get(d, 0) <= -t
        else:
            ok = ret.get(d, 0) >= t
        if ok and d in idx:
            fired.append(d)
    events, last = [], -99
    for d in fired:  # merge clusters: keep the first day of each run within 5 trading days
        i = idx[d]
        if i - last > 5:
            events.append(d)
        last = i
    res = []
    for h in HORIZONS:
        base = (c_s[h:] / c_s[:-h] - 1) * 100
        fwd = [(c_s[idx[d] + h] / c_s[idx[d]] - 1) * 100 for d in events if idx[d] + h < len(c_s)]
        if len(fwd) < 2:
            res.append({"h": h, "n": len(fwd)})
            continue
        f = np.array(fwd)
        se = base.std(ddof=1) / math.sqrt(len(f))
        res.append({"h": h, "n": len(f), "mean": round(float(f.mean()), 2), "median": round(float(np.median(f)), 2), "pos_pct": round(float((f > 0).mean() * 100), 0),
                    "base_mean": round(float(base.mean()), 2), "base_pos_pct": round(float((base > 0).mean() * 100), 0), "t": round(float((f.mean() - base.mean()) / se), 2) if se else None,
                    "worst": round(float(f.min()), 1), "best": round(float(f.max()), 1)})
    return {"trigger": trigger, "label": label.format(t=t), "threshold": t, "symbol": sym, "since": d_t[252] if len(d_t) > 252 else d_t[0], "events": len(events), "recent": events[-8:],
            "horizons": res, "small_sample": len(events) < 20,
            "reading": "t below about 2 means the difference from the all-days average is within noise. Few events (under 20) means the numbers are anecdotes, not statistics. Past behaviour is not a promise."}
