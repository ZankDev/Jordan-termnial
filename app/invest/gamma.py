"""Gamma exposure (GEX) levels from the listed option chain.

For the expirations in the next ~45 days (up to 8), every strike's Black-Scholes gamma is computed from the option's own implied volatility, then
  dollar GEX per 1% move = gamma x open interest x 100 x S^2 x 0.01
signed with the usual dealer-positioning assumption: customers buy calls and buy puts, so dealers are LONG call gamma (+) and SHORT put gamma (-).
Levels:
  CALL WALL   strike with the largest call GEX (often acts as resistance / a magnet into expiry)
  PUT WALL    strike with the largest put GEX (often support)
  ZERO GAMMA  the spot level where total GEX changes sign (recomputed across a +-15% grid of hypothetical prices)
  NET GEX     at today's price: positive = dealers hedge against moves (calmer, mean-reverting); negative = they hedge with moves (bigger, trending moves)
Limits: open interest is end-of-day (Yahoo), the dealer-side assumption is a convention not a fact, and 0DTE flows are not visible. Context, not a signal.
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

MAX_DAYS, MAX_EXP = 45, 8
_cache: dict[str, tuple[float, Any]] = {}


def _gamma(S: np.ndarray, K: np.ndarray, T: np.ndarray, iv: np.ndarray, r: float = 0.04) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * np.sqrt(T))
        g = np.exp(-0.5 * d1 ** 2) / (math.sqrt(2 * math.pi) * S * iv * np.sqrt(T))
    return np.nan_to_num(g)


def compute(symbol: str) -> dict[str, Any]:
    from app.api import invest_routes as ir

    hit = _cache.get(symbol)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    first = ir.api_options(symbol)
    S = first.get("price")
    exps = [e for e in (first.get("expirations") or []) if e - time.time() < MAX_DAYS * 86400][:MAX_EXP]
    if not S or not exps:
        return {"symbol": symbol, "ok": False, "note": "No listed options with open interest for this symbol."}
    rows = []  # (strike, T, iv, oi, sign)
    for e in exps:
        ch = first if e == first.get("date") else ir.api_options(symbol, e)
        T = max((e + 16 * 3600 - time.time()) / (365 * 86400), 1 / (365 * 24))  # expiry at the close
        for side, sign in (("calls", 1), ("puts", -1)):
            for o in ch.get(side, []):
                if o.get("strike") and o.get("oi") and o.get("iv") and 0.01 < o["iv"] < 5:
                    rows.append((o["strike"], T, o["iv"], o["oi"], sign))
    if not rows:
        return {"symbol": symbol, "ok": False, "note": "The option chain has no open interest yet."}
    a = np.array(rows, dtype=float)
    K, T, iv, oi, sg = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]

    def total(spot: float) -> float:
        return float(np.sum(_gamma(np.full_like(K, spot), K, T, iv) * oi * 100 * spot * spot * 0.01 * sg))
    g_now = _gamma(np.full_like(K, S), K, T, iv) * oi * 100 * S * S * 0.01
    by: dict[float, dict[str, float]] = {}
    for k, g, s in zip(K, g_now, sg):
        d = by.setdefault(float(k), {"call": 0.0, "put": 0.0})
        d["call" if s > 0 else "put"] += float(g)
    strikes = sorted(k for k in by if 0.7 * S <= k <= 1.3 * S)
    per = [{"strike": k, "call": round(by[k]["call"]), "put": round(-by[k]["put"]), "net": round(by[k]["call"] - by[k]["put"])} for k in strikes]
    # walls: hedging concentration if price got to each strike (gamma evaluated at S = K), expiries >= 2 days so a single 0DTE strike does not dominate,
    # call wall above the price and put wall below it
    m = T >= 2 / 365
    wall = _gamma(K[m], K[m], T[m], iv[m]) * oi[m] * 100 * K[m] ** 2 * 0.01
    cw: dict[float, float] = {}
    pw: dict[float, float] = {}
    for k, w, s_ in zip(K[m], wall, sg[m]):
        if s_ > 0 and k >= S:
            cw[float(k)] = cw.get(float(k), 0.0) + float(w)
        elif s_ < 0 and k <= S:
            pw[float(k)] = pw.get(float(k), 0.0) + float(w)
    allw: dict[float, list[float]] = {}
    for k, w, s_ in zip(K[m], wall, sg[m]):
        d = allw.setdefault(float(k), [0.0, 0.0])
        d[0 if s_ > 0 else 1] += float(w)
    for r in per:
        cwv, pwv = allw.get(r["strike"], [0.0, 0.0])
        r["wall_call"], r["wall_put"] = round(cwv), round(pwv)
    call_wall = max(cw, key=cw.get) if cw else None
    put_wall = max(pw, key=pw.get) if pw else None
    grid = np.linspace(S * 0.85, S * 1.15, 61)
    prof = [total(float(x)) for x in grid]
    flip = None
    for i in range(1, len(grid)):
        if np.sign(prof[i - 1]) != np.sign(prof[i]) and prof[i - 1] != 0:
            x0, x1, y0, y1 = grid[i - 1], grid[i], prof[i - 1], prof[i]
            cand = float(x0 - y0 * (x1 - x0) / (y1 - y0))
            if flip is None or abs(cand - S) < abs(flip - S):
                flip = cand
    net = total(S)
    regime = "POSITIVE" if net > 0 else "NEGATIVE"
    res = {"symbol": symbol, "ok": True, "spot": S, "expirations": len(exps), "n_options": len(rows), "net_gex": round(net), "regime": regime,
           "call_wall": call_wall, "put_wall": put_wall, "zero_gamma": round(flip, 2) if flip else None, "strikes": per,
           "profile": [{"spot": round(float(x), 2), "gex": round(y)} for x, y in zip(grid, prof)],
           "read": (f"Net gamma is {regime.lower()} (${net / 1e6:+,.1f}M per 1% move): "
                    + ("dealers sell rallies and buy dips, which tends to dampen moves" if net > 0 else "dealers chase moves, which tends to amplify them")
                    + (f". Above the zero-gamma level ({flip:,.2f}) moves are usually calmer; below it they get bigger." if flip else ".")
                    + (f" Call wall {call_wall:g} (resistance/magnet), put wall {put_wall:g} (support)." if call_wall and put_wall else "")),
           "note": "Open interest is end-of-day; dealer side is the standard convention (long calls, short puts), not a fact. Context, not a signal."}
    _cache[symbol] = (time.time(), res)
    return res


def surface(symbol: str, max_days: int = 120, max_exp: int = 14, band: float = 0.12, lo: float | None = None, hi: float | None = None) -> dict[str, Any]:
    """Net GEX per (expiration, strike) at today's price, for the 3D surface: x = strike, y = expiration, z = dollar gamma per 1% move."""
    from app.api import invest_routes as ir

    key = f"surf:{symbol}:{lo}:{hi}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    first = ir.api_options(symbol)
    S = first.get("price")
    exps = [e for e in (first.get("expirations") or []) if e - time.time() < max_days * 86400][:max_exp]
    if not S or not exps:
        return {"symbol": symbol, "ok": False, "note": "No listed options for this symbol."}
    grid: dict[int, dict[float, float]] = {}
    gc: dict[int, dict[float, float]] = {}
    gp: dict[int, dict[float, float]] = {}
    strikes: set[float] = set()
    for e in exps:
        ch = first if e == first.get("date") else ir.api_options(symbol, e)
        T = max((e + 16 * 3600 - time.time()) / (365 * 86400), 1 / (365 * 24))
        row: dict[float, float] = {}
        rc: dict[float, float] = {}
        rp: dict[float, float] = {}
        for side, sign in (("calls", 1), ("puts", -1)):
            for o in ch.get(side, []):
                k = o.get("strike")
                in_range = (lo <= k <= hi) if (lo is not None and hi is not None) else abs(k / S - 1) <= band  # a chosen price range, or +-band around the price
                if not k or not o.get("oi") or not o.get("iv") or not (0.01 < o["iv"] < 5) or not in_range:
                    continue
                g = float(_gamma(np.array([S]), np.array([k]), np.array([T]), np.array([o["iv"]]))[0])
                v = g * o["oi"] * 100 * S * S * 0.01
                row[float(k)] = row.get(float(k), 0.0) + sign * v
                (rc if sign > 0 else rp)[float(k)] = (rc if sign > 0 else rp).get(float(k), 0.0) + sign * v
                strikes.add(float(k))
        grid[e], gc[e], gp[e] = row, rc, rp
    raw_ks = sorted(strikes)
    if not raw_ks:
        return {"symbol": symbol, "ok": False, "note": "No strikes with open interest in that price range."}
    # Sum gamma into equal price bins: dense chains (SPY lists $1 strikes but most open interest sits on $5 ones) would otherwise draw a comb.
    # Bin width = a round number near (range / 70), never finer than the chain's typical strike spacing. Summing keeps the dollars exact.
    span = max(raw_ks[-1] - raw_ks[0], 1e-9)
    native = float(np.median(np.diff(raw_ks))) if len(raw_ks) > 2 else span
    want = max(span / 70, native)
    width = next((w for w in (0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 250, 500, 1000) if w >= want * 0.999), want)
    start = np.floor(raw_ks[0] / width) * width
    nb = int(np.floor((raw_ks[-1] - start) / width)) + 1
    ks = [round(start + i * width + width / 2, 4) for i in range(nb)]

    def binned(g: dict[int, dict[float, float]]) -> list[list[float]]:
        out = []
        for e in exps:
            row = [0.0] * nb
            for k, v in g[e].items():
                row[min(nb - 1, int((k - start) // width))] += v
            out.append([round(x) for x in row])
        return out
    z, zc, zp = binned(grid), binned(gc), binned(gp)
    dte = [round(max(0.0, (e - time.time()) / 86400), 2) for e in exps]
    if not ks:
        return {"symbol": symbol, "ok": False, "note": "No strikes with open interest in that price range."}
    res = {"symbol": symbol, "ok": True, "spot": S, "lo": lo, "hi": hi, "strikes": ks, "expirations": [time.strftime("%Y-%m-%d", time.gmtime(e)) for e in exps], "z": z, "zc": zc, "zp": zp, "dte": dte,
           "bin_width": width, "note": "Net dealer gamma per strike bin and expiry at today's price (calls +, puts -), $ per 1% move. End-of-day open interest; convention, not a fact."}
    _cache[key] = (time.time(), res)
    return res
