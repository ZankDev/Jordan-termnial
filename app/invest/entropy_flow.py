"""ENTROPY-PRODUCTION strategy backtest (user's formula, Oct 12 2026).

    Σ̇(t) = lim_{dt→0} (1/dt) · D_KL(P_fwd ‖ P_ref) = E_p[ (μ_fwd − μ_ref)² / (4D) ]

For a diffusion dx = μ dt + √(2D) dW the KL divergence rate between the forward path measure and a reference measure is (μ_fwd − μ_ref)²/(4D):
how far the market is from "equilibrium". Estimated on a rolling window of N bars of log returns r:
    μ_fwd = mean(r, N),  D = var(r, N) / 2
Reference drift (two readings):
    "rev"  — the time-reversed path, μ_ref = −μ_fwd  →  Σ̇ = μ_fwd² / D   (arrow of time: is the path visibly irreversible?)
    "slow" — the slow equilibrium drift, μ_ref = mean(r, 5N)              (is the fast drift far from the slow one?)
Σ̇·N = 2·t² (t = t-stat of the window drift), so thresholds are set on |t| with no look-ahead and no fitted percentiles.
Trade rules tested (all variants reported — no cherry-picking):
    mode "trend": when Σ̇ is high, go WITH sign(μ_fwd − μ_ref)  (non-equilibrium = directed flow)
    mode "fade" : the opposite (high irreversibility = overstretched, expect relaxation)
Intraday (NQ / NDX 5-min, RTH): enter at the next bar's open 09:45–15:00, exit when |t| falls below half the entry level, after H bars, or 15:55;
alternative exit = 1:2 bracket (stop 1×ATR14, target 2×ATR14). Costs per MNQ round turn: $2 commission + 0.5 pt slippage.
Daily (SPY, QQQ, GLD, TLT, BTC, oil, EURUSD): position = sign while |t| ≥ k, else flat; 2 bp cost per unit turnover.
In-sample = before 2023 (intraday) / before 2018 (daily); out-of-sample after. Results → data_cache/entropy_flow/report.json.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import numpy as np
import pandas as pd

from app.invest import gamma_vwap as gv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data_cache", "entropy_flow")
PT_USD = 2.0  # MNQ $ per NDX point
COMM_RT = 2.0
SLIP_PTS_RT = 0.5
IS_END_INTRA = pd.Timestamp("2023-01-01", tz="America/New_York")
IS_END_DAILY = pd.Timestamp("2018-01-01")


def sigma_dot(r: pd.Series, n: int, ref: str) -> tuple[pd.Series, pd.Series]:
    """(Σ̇·N as 2t², signed direction μ_fwd − μ_ref) on a rolling window; uses data up to and including each bar."""
    mu = r.rolling(n, min_periods=n - 2).mean()
    D = r.rolling(n, min_periods=n - 2).var() / 2
    mref = -mu if ref == "rev" else r.rolling(5 * n, min_periods=4 * n).mean()
    sd = (mu - mref) ** 2 / (4 * D)
    return sd * n, (mu - mref)


def _t_from(sdn: np.ndarray, ref: str) -> np.ndarray:
    # rev: Σ̇N = μ²N/D = 2t²  → |t| = sqrt(Σ̇N/2);  slow: Σ̇N = (Δμ)²N/(4D) → t-like = sqrt(2·Σ̇N)
    return np.sqrt(sdn / 2) if ref == "rev" else np.sqrt(2 * sdn)


# ------------------------------------------------------------------------------------------------ intraday NQ
def intraday(df: pd.DataFrame, n: int, ref: str, k: float, mode: str, exit_: str, hold: int = 12) -> pd.DataFrame:
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    day = df.index.date
    r = pd.Series(np.log(c), index=df.index).diff()
    first = np.r_[True, day[1:] != day[:-1]]
    r[first] = np.nan  # the overnight gap is not part of the intraday diffusion
    sdn, dirn = sigma_dot(r, n, ref)
    tt = _t_from(sdn.to_numpy(), ref)
    dv = np.sign(dirn.to_numpy())
    a = gv.atr(df)
    tm = df.index.time
    import datetime as dt
    t_in0, t_in1, t_flat = dt.time(9, 45), dt.time(15, 0), dt.time(15, 55)
    trades = []
    i, N = 0, len(df)
    while i < N - 1:
        if not (t_in0 <= tm[i] <= t_in1) or not np.isfinite(tt[i]) or tt[i] < k or dv[i] == 0:
            i += 1
            continue
        side = dv[i] if mode == "trend" else -dv[i]
        j = i + 1
        if day[j] != day[i]:
            i += 1
            continue
        entry = o[j]
        stop = entry - side * a[i]
        tgt = entry + side * 2 * a[i]
        exit_px, kk = None, j
        while kk < N and day[kk] == day[i]:
            if exit_ == "bracket":
                hit_s = (l[kk] <= stop) if side > 0 else (h[kk] >= stop)
                hit_t = (h[kk] >= tgt) if side > 0 else (l[kk] <= tgt)
                if hit_s:
                    exit_px = stop
                    break
                if hit_t:
                    exit_px = tgt
                    break
            else:
                if kk - j >= hold or (np.isfinite(tt[kk]) and tt[kk] < k / 2) or (dv[kk] != dv[i] and dv[kk] != 0):
                    exit_px = c[kk]
                    break
            if tm[kk] >= t_flat:
                exit_px = c[kk]
                break
            kk += 1
        if exit_px is None:
            kk = min(kk, N - 1)
            exit_px = c[kk - 1] if kk > j else c[j]
        pts = side * (exit_px - entry) - SLIP_PTS_RT
        trades.append((df.index[j], side, pts, pts * PT_USD - COMM_RT, a[i]))
        i = max(kk, j) + 1
    return pd.DataFrame(trades, columns=["ts", "side", "pts", "usd", "atr"])


def _stats(t: pd.DataFrame) -> dict[str, Any]:
    if t.empty:
        return {"n": 0}
    u = t["usd"]
    days = t["ts"].dt.date.nunique()
    daily = t.groupby(t["ts"].dt.date)["usd"].sum()
    return {"n": int(len(t)), "win": round(float((u > 0).mean()) * 100, 1), "avg_usd": round(float(u.mean()), 2),
            "avg_pts": round(float(t["pts"].mean()), 2), "total_usd": round(float(u.sum()), 0),
            "t": round(float(u.mean() / (u.std(ddof=1) / np.sqrt(len(u)))), 2) if len(u) > 2 and u.std() > 0 else 0.0,
            "pf": round(float(u[u > 0].sum() / max(1e-9, -u[u < 0].sum())), 2), "days": int(days),
            "sharpe_daily": round(float(daily.mean() / daily.std() * np.sqrt(252)), 2) if daily.std() > 0 else 0.0}


def run_intraday() -> list[dict[str, Any]]:
    df = gv.load_ndx_5m()
    rows = []
    for ref in ("rev", "slow"):
        for n in (12, 24, 48):
            for k in (1.5, 2.0, 2.5, 3.0):
                for mode in ("trend", "fade"):
                    for ex in ("signal", "bracket"):
                        t = intraday(df, n, ref, k, mode, ex)
                        ins, oos = t[t["ts"] < IS_END_INTRA], t[t["ts"] >= IS_END_INTRA]
                        rows.append({"ref": ref, "n": n, "k": k, "mode": mode, "exit": ex, "is": _stats(ins), "oos": _stats(oos)})
    return rows


# ------------------------------------------------------------------------------------------------ daily multi-asset
DAILY = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "GLD": "Gold", "TLT": "20y Treasuries", "BTC-USD": "Bitcoin", "CL=F": "WTI oil", "EURUSD=X": "EUR/USD"}


def _yahoo_long(sym: str) -> pd.Series:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{sym.replace('=', '_').replace('^', '')}_1d.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 12 * 3600:
        j = json.load(open(path))
    else:
        import requests
        j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "25y", "interval": "1d"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()
        json.dump(j, open(path, "w"))
    res = j["chart"]["result"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or res["indicators"]["quote"][0]["close"]
    s = pd.Series(adj, index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    return s.dropna()[lambda x: x > 0]


def daily_bt(px: pd.Series, n: int, ref: str, k: float, mode: str) -> pd.Series:
    r = np.log(px).diff()
    sdn, dirn = sigma_dot(r, n, ref)
    tt = pd.Series(_t_from(sdn.to_numpy(), ref), index=px.index)
    pos = np.where(tt >= k, np.sign(dirn), 0.0) * (1 if mode == "trend" else -1)
    pos = pd.Series(pos, index=px.index).shift(1).fillna(0)  # trade on the next day
    cost = pos.diff().abs().fillna(0) * 0.0002
    return pos * r.fillna(0) - cost


def _sr(x: pd.Series) -> dict[str, Any]:
    if len(x) < 50 or x.std() == 0:
        return {"sharpe": 0.0, "cagr": 0.0, "n": len(x)}
    eq = x.cumsum().apply(np.exp)
    yrs = len(x) / 252
    return {"sharpe": round(float(x.mean() / x.std() * np.sqrt(252)), 2), "cagr": round(float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), 1),
            "maxdd": round(float((eq / eq.cummax() - 1).min() * 100), 1), "exposure": None, "n": int(len(x))}


def run_daily() -> list[dict[str, Any]]:
    rows = []
    for sym, name in DAILY.items():
        try:
            px = _yahoo_long(sym)
        except Exception:  # noqa: BLE001
            continue
        r = np.log(px).diff().fillna(0)
        bh_is, bh_oos = _sr(r[r.index < IS_END_DAILY]), _sr(r[r.index >= IS_END_DAILY])
        for ref in ("rev", "slow"):
            for n in (10, 20, 60):
                for k in (1.0, 1.5, 2.0):
                    for mode in ("trend", "fade"):
                        x = daily_bt(px, n, ref, k, mode)
                        rows.append({"sym": sym, "name": name, "ref": ref, "n": n, "k": k, "mode": mode,
                                     "is": _sr(x[x.index < IS_END_DAILY]), "oos": _sr(x[x.index >= IS_END_DAILY]),
                                     "bh_is": bh_is, "bh_oos": bh_oos, "start": str(px.index[0].date())})
    return rows


def report() -> dict[str, Any]:
    os.makedirs(OUT, exist_ok=True)
    rep = {"intraday": run_intraday(), "daily": run_daily(), "ts": int(time.time())}
    json.dump(rep, open(os.path.join(OUT, "report.json"), "w"), indent=1)
    return rep


if __name__ == "__main__":
    report()
