"""Rule-based Wyckoff setup finder used to build the ACADEMY drill library from real history.

It looks for the classic accumulation sequence and, by mirroring the prices, the distribution sequence:

  accumulation:  decline -> SC (selling climax: wide bar, heavy volume, swing low) -> AR (automatic rally) -> ST (secondary test, lighter volume)
                 -> SPRING (dip below the range low that closes back inside) -> SOS (sign of strength: break up on volume) -> LPS (last point of support)
  distribution:  advance -> BC (buying climax) -> AR (automatic reaction) -> ST -> UT / UTAD (poke above the range high that fails)
                 -> SOW (sign of weakness) -> LPSY (last point of supply)

Wyckoff is discretionary: these are heuristics that find textbook-looking examples. They are teaching references, not a trading system, and
each setup carries the measured outcome so the trainer can be honest about how often the textbook trade worked.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# per timeframe: minimum decline before the climax, look-back for that decline, swing window, max bars the range may last
PARAMS = {
    "1d": {"leg": 0.20, "look": 120, "swing": 12, "ar_span": 30, "range": 140, "gap": 6},
    "4h": {"leg": 0.09, "look": 240, "swing": 18, "ar_span": 40, "range": 220, "gap": 10},
    "1h": {"leg": 0.05, "look": 300, "swing": 24, "ar_span": 48, "range": 300, "gap": 12},
    "15m": {"leg": 0.03, "look": 400, "swing": 24, "ar_span": 60, "range": 400, "gap": 16},
}


def _accumulation(a: np.ndarray, tf: str) -> list[dict[str, Any]]:
    p = PARAMS[tf]
    n = len(a)
    o, h, l, c, v = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
    prev_c = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    atr = pd.Series(tr).rolling(14, min_periods=5).mean().to_numpy()
    vma = pd.Series(v).rolling(50, min_periods=20).mean().to_numpy()
    out: list[dict[str, Any]] = []
    w, i = p["swing"], max(p["swing"] + 60, 80)
    while i < n - 40:
        if l[i] > l[max(0, i - w): i + w + 1].min() or not np.isfinite(atr[i]) or not np.isfinite(vma[i]):
            i += 1
            continue
        if v[i] < 1.5 * vma[i] or (h[i] - l[i]) < 1.2 * atr[i]:
            i += 1
            continue
        hi_prev = h[max(0, i - p["look"]): i].max()
        if (hi_prev - l[i]) / hi_prev < p["leg"]:
            i += 1
            continue
        j0, j1 = i + 2, min(n - 1, i + p["ar_span"])
        if j1 <= j0:
            i += 1
            continue
        ar = j0 + int(np.argmax(h[j0: j1 + 1]))
        low, high = l[i], h[ar]
        height = high - low
        if height < 3 * atr[i]:
            i += 1
            continue
        st = spring = back = None
        for k in range(ar + 1, min(n - 2, ar + p["range"])):
            if c[k] > high or c[k] < low - 0.5 * height:
                break  # range broken without a spring
            if st is None and k > ar + 2 and low - 0.05 * height <= l[k] <= low + 0.25 * height and v[k] < 0.8 * v[i]:
                st = k
            if k - ar >= p["gap"] and low * 0.999 > l[k] >= low - 0.4 * height:
                for m in range(k, min(n - 1, k + 3) + 1):
                    if c[m] > low:
                        back = m
                        break
                if back is not None:
                    spring = k
                    break
        if spring is None or back is None:
            i += 1
            continue
        sos = lps = None
        for m in range(back + 1, min(n - 1, back + 50)):
            if c[m] > low + 0.6 * height and v[m] >= 1.1 * vma[m]:
                sos = m
                break
        if sos is not None and sos + 3 < n - 1:
            seg = l[sos + 2: min(n, sos + 30)]
            if len(seg):
                cand = sos + 2 + int(np.argmin(seg))
                if l[cand] > low + 0.1 * height:
                    lps = cand
        entry = float(c[back])
        stop = float(l[spring: back + 1].min()) * 0.999
        risk = entry - stop
        if risk <= 0 or risk / entry > 0.15:
            i = back + 1
            continue
        target = float(high)
        # forward outcome of the textbook trade: long at the close that came back inside, stop under the spring low, first target = range top
        res, r_exit, bars, mfe, mae, hit2, hit3 = "open", 0.0, 0, 0.0, 0.0, False, False
        for m in range(back + 1, min(n, back + 90)):
            if l[m] <= stop:
                res, r_exit, bars = "loss", -1.0, m - back
                break
            mfe = max(mfe, (h[m] - entry) / risk)
            mae = min(mae, (l[m] - entry) / risk)
            hit2, hit3 = hit2 or mfe >= 2, hit3 or mfe >= 3
            if h[m] >= target:
                res, r_exit, bars = "win", (target - entry) / risk, m - back
                break
        out.append({"entry_i": int(back), "events": {"sc": int(i), "ar": int(ar), "st": (int(st) if st is not None else None), "spring": int(spring), "sos": (int(sos) if sos is not None else None), "lps": (int(lps) if lps is not None else None)},
                    "range_low": float(low), "range_high": float(high), "entry": entry, "stop": stop, "target": target, "rr": round((target - entry) / risk, 2),
                    "spring_vol_ratio": round(float(v[spring] / vma[spring]), 2) if np.isfinite(vma[spring]) and vma[spring] else None, "sc_vol_ratio": round(float(v[i] / vma[i]), 2),
                    "outcome": {"result": res, "r": round(r_exit, 2), "bars": int(bars), "mfe_r": round(float(mfe), 2), "mae_r": round(float(mae), 2), "hit_2r": bool(hit2), "hit_3r": bool(hit3)}})
        i = (sos or back) + 1
    return out


def detect(a: np.ndarray, tf: str) -> list[dict[str, Any]]:
    """All accumulation and distribution setups in the candle array a (columns t, o, h, l, c, v), oldest first."""
    if tf not in PARAMS or len(a) < 300:
        return []
    found = [{**s, "kind": "accumulation"} for s in _accumulation(a, tf)]
    m = a.copy()
    # mirror the price axis in log space (p -> 1/p): an advance becomes a decline, so a distribution reads as an accumulation
    m[:, 1], m[:, 2], m[:, 3], m[:, 4] = 1e6 / a[:, 1], 1e6 / a[:, 3], 1e6 / a[:, 2], 1e6 / a[:, 4]
    for s in _accumulation(m, tf):
        found.append({**s, "kind": "distribution", "range_low": 1e6 / s["range_high"], "range_high": 1e6 / s["range_low"], "entry": 1e6 / s["entry"], "stop": 1e6 / s["stop"], "target": 1e6 / s["target"]})
    found.sort(key=lambda s: s["entry_i"])
    for s in found:
        s["entry_t"] = int(a[s["entry_i"], 0])
        s["events_t"] = {k: (int(a[i, 0]) if i is not None else None) for k, i in s["events"].items()}
    return found
