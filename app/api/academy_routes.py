"""ACADEMY API (prefix /api/academy): candle sessions for the replay trainer and the Wyckoff drill library.

  /session  random (or chosen) stretch of real history for free replay on BTC / ETH / SOL, 15m to 1d
  /drill    a real Wyckoff setup found in history (accumulation with a spring, distribution with an upthrust), with the textbook answer kept for the reveal
  /library  how many setups exist and how often the textbook trade actually worked
"""
from __future__ import annotations

import json
import random
import threading
import time
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.academy import data, wyckoff

router = APIRouter(prefix="/api/academy", tags=["academy"])
DRILL_TFS = ("4h", "1d")
BARS = 1000
_LIB: dict[str, list[dict[str, Any]] | str] = {}   # "sym:tf" -> setups, or "building"
_LIB_LOCK = threading.Lock()


def _check(symbol: str, tf: str) -> None:
    if symbol not in data.SYMBOLS or tf not in data.TF_MS:
        raise HTTPException(status_code=400, detail="symbol must be BTCUSDT, ETHUSDT or SOLUSDT and timeframe 15m, 1h, 4h or 1d")


def _build(symbol: str, tf: str) -> None:
    key = f"{symbol}:{tf}"
    try:
        f = data.DIR / f"setups_{symbol}_{tf}.json"
        a = data.history(symbol, tf)
        # reuse the stored library when it was built from (nearly) the same amount of history
        if f.exists():
            saved = json.loads(f.read_text())
            if saved.get("bars", 0) >= len(a) - 200:
                _LIB[key] = saved["setups"]
                return
        setups = wyckoff.detect(a, tf)
        f.write_text(json.dumps({"bars": len(a), "setups": setups}, separators=(",", ":")))
        _LIB[key] = setups
    except Exception:  # noqa: BLE001
        _LIB.pop(key, None)


def _library(symbol: str, tf: str) -> list[dict[str, Any]] | None:
    key = f"{symbol}:{tf}"
    with _LIB_LOCK:
        cur = _LIB.get(key)
        if isinstance(cur, list):
            return cur
        if cur != "building":
            _LIB[key] = "building"
            threading.Thread(target=_build, args=(symbol, tf), daemon=True, name=f"academy-{key}").start()
    return None


@router.get("/session")
def api_session(symbol: str = "BTCUSDT", tf: str = "1h", start: int | None = None):
    """BARS candles of real history from a random (or given, unix seconds) start. The client reveals them one bar at a time."""
    _check(symbol, tf)
    step = data.TF_MS[tf] // 1000
    lo = data._ms(data.HISTORY_FROM[tf]) // 1000
    if symbol == "SOLUSDT":
        lo = max(lo, data._ms("2020-09-01") // 1000)
    hi = int(time.time()) - (BARS + 5) * step
    st = int(start) if start else random.randint(lo, hi)
    st = max(lo, min(st, hi))
    rows = data.klines(symbol, tf, st * 1000, BARS)
    if len(rows) < 300:
        raise HTTPException(status_code=502, detail="not enough candles returned")
    return {"symbol": symbol, "tf": tf, "start": rows[0][0], "candles": rows, "reveal": 150}


@router.get("/drill")
def api_drill(symbol: str = "BTCUSDT", tf: str = "4h", kind: str = "any", seen: str = ""):
    _check(symbol, tf)
    if tf not in DRILL_TFS:
        raise HTTPException(status_code=400, detail="Wyckoff drills use the 4h and 1d charts")
    lib = _library(symbol, tf)
    if lib is None:
        return {"building": True, "message": f"Building the {symbol[:-4]} {tf} setup library from years of real candles (first time only, about a minute)."}
    done = {s for s in seen.split(",") if s}
    pool = [s for s in lib if (kind == "any" or s["kind"] == kind) and f"{symbol}:{tf}:{s['entry_t']}" not in done]
    if not pool:
        pool = [s for s in lib if kind == "any" or s["kind"] == kind]
    if not pool:
        raise HTTPException(status_code=404, detail="no setups of that kind for this market")
    s = random.choice(pool)
    a = data.history(symbol, tf)
    before = 260
    i0 = max(0, s["entry_i"] - before)
    i1 = min(len(a), i0 + BARS)
    rel = lambda i: (i - i0) if i is not None else None  # noqa: E731
    seg = a[i0:i1]
    entry_rel = s["entry_i"] - i0
    # every other setup the detector found inside this same stretch of chart, shown to the student after the drill
    others = []
    for o in lib:
        if o is s or o["entry_t"] == s["entry_t"] or not (i0 + 5 <= o["entry_i"] < i1) or o["events"].get("sc") is None or o["events"]["sc"] < i0:
            continue
        ev = {k: (rel(v) if v is not None and v >= i0 else None) for k, v in o["events"].items()}
        others.append({"kind": o["kind"], "entry_i": o["entry_i"] - i0, "events": ev, "range_low": o["range_low"], "range_high": o["range_high"], "entry": o["entry"], "stop": o["stop"], "target": o["target"], "rr": o["rr"],
                       "result": o["outcome"]["result"], "r": o["outcome"]["r"], "mfe_r": o["outcome"].get("mfe_r")})
    others.sort(key=lambda o: o["entry_i"])
    return {"others": others,"symbol": symbol, "tf": tf, "start": int(seg[0, 0]), "candles": seg.tolist(), "reveal": max(150, entry_rel - 40),
            "setup": {"id": f"{symbol}:{tf}:{s['entry_t']}", "kind": s["kind"], "entry_i": entry_rel, "events": {k: rel(v) for k, v in s["events"].items()}, "range_low": s["range_low"], "range_high": s["range_high"],
                      "entry": s["entry"], "stop": s["stop"], "target": s["target"], "rr": s["rr"], "spring_vol_ratio": s["spring_vol_ratio"], "sc_vol_ratio": s["sc_vol_ratio"], "outcome": s["outcome"]},
            "library": _stats(lib)}


def _stats(lib: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in ("accumulation", "distribution"):
        L = [s for s in lib if s["kind"] == kind]
        n = len(L)
        out[kind] = {"n": n, "target_hit": round(100 * sum(s["outcome"]["result"] == "win" for s in L) / n) if n else None, "hit_2r": round(100 * sum(s["outcome"]["hit_2r"] for s in L) / n) if n else None,
                     "stopped": round(100 * sum(s["outcome"]["result"] == "loss" for s in L) / n) if n else None, "avg_r": round(sum(s["outcome"]["r"] for s in L) / n, 2) if n else None}
    return out


@router.get("/library")
def api_library():
    res = {}
    for sym in data.SYMBOLS:
        for tf in DRILL_TFS:
            lib = _library(sym, tf)
            res[f"{sym}:{tf}"] = _stats(lib) if lib is not None else {"building": True}
    return res


# ------------------------------------------------------------------ Wyckoff scan for the GP chart's "AI mark a
# Wyckoff setup" action (window.NX.chartAI.markWyckoff in web/gp.js): runs the exact same detector the ACADEMY
# drill library uses, but on-demand against whatever candles the user's own chart already has loaded, instead of
# the cached BTC/ETH/SOL history - so it works on any security the chart supports, at the interval on screen.
GP_TF = {"15m": "15m", "60m": "1h", "4h": "4h", "1d": "1d"}  # web/gp.js interval codes -> wyckoff.PARAMS keys


class WyckoffScanIn(BaseModel):
    candles: list[list[float]]  # [t, o, h, l, c, v] rows, oldest first - exactly what the chart has loaded
    interval: str


@router.post("/wyckoff/scan")
def api_wyckoff_scan(body: WyckoffScanIn):
    tf = GP_TF.get(body.interval)
    if not tf:
        raise HTTPException(status_code=400, detail=f"Wyckoff detection needs a 15m, 1H, 4H or 1D chart (this chart is {body.interval})")
    if len(body.candles) < 300:
        raise HTTPException(status_code=400, detail=f"needs at least 300 bars loaded on the chart (this one has {len(body.candles)}) - widen the range")
    try:
        arr = np.array(body.candles, dtype=float)
        setups = wyckoff.detect(arr, tf)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"detection failed: {type(e).__name__}") from e
    return {"tf": tf, "n_bars": len(body.candles), "setups": setups}


def warm() -> None:
    """Download history and build the setup libraries in the background so the first drill does not wait."""
    def run() -> None:
        for sym in data.SYMBOLS:
            for tf in DRILL_TFS:
                _library(sym, tf)
                time.sleep(0.5)
    threading.Thread(target=run, daemon=True, name="academy-warm").start()
