"""Gamma + VWAP strategy for NQ: backtest, live signals and paper auto-entries.

Rules (5-minute bars, New York regular session; entries 09:45-15:00 ET, flat at 15:55):
  WALL FADE   price runs into the call wall from above VWAP and the bar closes back under the wall -> short.
              Stop above the wall/bar high + buffer, target 2R (risk 1 : reward 2). Mirror at the put wall from below VWAP -> long.
  VWAP RECLAIM a bar closes back above VWAP and the call wall is at least 2R above the entry -> long, stop under VWAP - buffer, target 2R.
              Mirror below VWAP with room to the put wall -> short.
One position at a time, at most 2 trades per setup per day. Stop and target touched in the same bar count as a stop (conservative).
Costs: 1 tick worse on the entry, 1 tick worse on stops, $2 round turn per MNQ contract (reported in R and in $ per MNQ).

Data honesty
  * Price: Dukascopy 5m Nasdaq-100 (cash index CFD, 2018-09 ..), its volume is tick volume, so the backtest VWAP is a tick-volume VWAP.
  * Gamma history: there is no free archive of option open interest by strike, so the backtest CANNOT use real historical gamma walls.
    It uses a PROXY: QQQ round strikes ($5 / $10 grid) next to the previous close, mapped to Nasdaq-100 points by the NDX/QQQ ratio.
    Real QQQ walls usually sit on those round strikes, but which one is biggest changes day to day. A CONTROL run shifts the grid by half a
    step (levels nobody trades); if the control does as well as the "gamma" grid, the levels add nothing.
  * The real walls (app/invest/gamma.py, QQQ chain) are snapshotted every day into gamma_snaps from now on, and the live worker trades
    them on paper, so the real version builds its own forward record. Not investment advice; paper only, never the real account.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import math
import os
import time
from typing import Any

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DUKA = os.path.join(ROOT, "data_cache", "dukascopy")
CACHE = os.path.join(ROOT, "data_cache", "gamma_vwap")
os.makedirs(CACHE, exist_ok=True)

TICK = 0.25
MNQ_PT = 2.0          # $ per point, micro Nasdaq
COMM_RT = 2.0         # $ round turn per MNQ
RR = 2.0
ENTRY_FROM, ENTRY_TO, FLAT_AT = dt.time(9, 45), dt.time(15, 0), dt.time(15, 55)
MAX_PER_SETUP_DAY = 2
MIN_RISK_PTS = 8.0


# ------------------------------------------------------------------------------------------------ data
def load_ndx_5m() -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(DUKA, "USATECHIDXUSD_5m_*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in files])
    df = df[~df.index.duplicated()].sort_index()
    df.index = df.index.tz_convert("America/New_York")
    t = df.index.time
    return df[(t >= dt.time(9, 30)) & (t < dt.time(16, 0))]


def _yahoo_daily(sym: str) -> pd.Series:
    path = os.path.join(CACHE, f"{sym.replace('^', '')}_1d.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 12 * 3600:
        j = json.load(open(path))
    else:
        import requests
        j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "10y", "interval": "1d"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()
        json.dump(j, open(path, "w"))
    r = j["chart"]["result"][0]
    s = pd.Series(r["indicators"]["quote"][0]["close"], index=pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_convert("America/New_York").date)
    return s.dropna()


def daily_levels(days: list[dt.date], grid: float, shift: float = 0.0) -> dict[dt.date, tuple[float, float, float]]:
    """Per day: (put wall, call wall, ratio) in Nasdaq-100 points from the PREVIOUS day's QQQ close (no look-ahead).
    Walls = the grid strikes just below / above the previous close (at least 0.2% away). shift = fraction of a step (0.5 = control)."""
    qqq, ndx = _yahoo_daily("QQQ"), _yahoo_daily("^NDX")
    both = pd.concat([qqq.rename("q"), ndx.rename("n")], axis=1).dropna()
    out = {}
    prev = both.index[0]
    idx = list(both.index)
    pos = {d: i for i, d in enumerate(idx)}
    for d in days:
        # last QQQ/NDX close strictly before d
        j = None
        lo, hi = 0, len(idx) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if idx[mid] < d:
                j, lo = mid, mid + 1
            else:
                hi = mid - 1
        if j is None:
            continue
        q, n = both.iloc[j]["q"], both.iloc[j]["n"]
        ratio = n / q
        off = shift * grid
        above = math.floor((q * 1.002 - off) / grid) * grid + grid + off
        below = math.ceil((q * 0.998 - off) / grid) * grid - grid + off
        out[d] = (below * ratio, above * ratio, ratio)
    _ = prev, pos
    return out


# ------------------------------------------------------------------------------------------------ signals (shared by backtest and live)
def session_vwap(df: pd.DataFrame) -> np.ndarray:
    tp = (df["high"].values + df["low"].values + df["close"].values) / 3
    v = np.maximum(df["volume"].values.astype(float), 1e-9)
    return np.cumsum(tp * v) / np.cumsum(v)


def atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).rolling(n, min_periods=3).mean().bfill().values


def signal_at(i: int, o, h, l, c, vw, a, put_w: float, call_w: float, setups: tuple[str, ...]) -> dict | None:
    """Signal on the close of bar i (entry at the next bar's open). Returns {setup, side, stop} or None."""
    buf = max(4.0, 0.15 * a[i])
    if "fade" in setups:
        if h[i] >= call_w - buf and c[i] < call_w and c[i] > vw[i]:
            return {"setup": "WALL FADE", "side": "short", "stop": max(call_w, h[i]) + buf, "level": call_w}
        if l[i] <= put_w + buf and c[i] > put_w and c[i] < vw[i]:
            return {"setup": "WALL FADE", "side": "long", "stop": min(put_w, l[i]) - buf, "level": put_w}
    if "break" in setups and i >= 1:
        # acceleration through a wall: the previous close was inside, this bar closes beyond it on the VWAP side of the move
        if c[i - 1] <= call_w < c[i] and c[i] > vw[i]:
            return {"setup": "WALL BREAK", "side": "long", "stop": min(call_w, l[i]) - buf, "level": call_w}
        if c[i - 1] >= put_w > c[i] and c[i] < vw[i]:
            return {"setup": "WALL BREAK", "side": "short", "stop": max(put_w, h[i]) + buf, "level": put_w}
    if "reclaim" in setups and i >= 1:
        if c[i - 1] <= vw[i - 1] and c[i] > vw[i]:
            stop = min(vw[i], l[i]) - buf
            if call_w - c[i] >= RR * (c[i] - stop):
                return {"setup": "VWAP RECLAIM", "side": "long", "stop": stop, "level": call_w}
        if c[i - 1] >= vw[i - 1] and c[i] < vw[i]:
            stop = max(vw[i], h[i]) + buf
            if c[i] - put_w >= RR * (stop - c[i]):
                return {"setup": "VWAP RECLAIM", "side": "short", "stop": stop, "level": put_w}
    return None


# ------------------------------------------------------------------------------------------------ backtest
def backtest(grid: float = 5.0, shift: float = 0.0, setups: tuple[str, ...] = ("fade", "reclaim"), df: pd.DataFrame | None = None) -> pd.DataFrame:
    df = load_ndx_5m() if df is None else df
    days = sorted(set(df.index.date))
    lv = daily_levels(days, grid, shift)
    trades = []
    for d, g in df.groupby(df.index.date):
        if d not in lv or len(g) < 60:
            continue
        put_w, call_w, _ = lv[d]
        o, h, l, c = (g[k].values for k in ("open", "high", "low", "close"))
        vw, a = session_vwap(g), atr(g)
        tms = g.index.time
        n_set: dict[str, int] = {}
        pos = None
        for i in range(len(g) - 1):
            if pos is not None:
                j = i  # manage on bar i
                side, e, s, t = pos["side"], pos["entry"], pos["stop"], pos["target"]
                hit_s = l[j] <= s if side == "long" else h[j] >= s
                hit_t = h[j] >= t if side == "long" else l[j] <= t
                ex = None
                if hit_s:
                    ex, why = s - (TICK if side == "long" else -TICK), "stop"
                elif hit_t:
                    ex, why = t, "target"
                elif tms[j] >= FLAT_AT:
                    ex, why = c[j], "close"
                if ex is not None:
                    risk = abs(e - s)
                    pts = (ex - e) if side == "long" else (e - ex)
                    trades.append({**pos, "exit": ex, "why": why, "exit_ts": g.index[j], "pts": pts, "r": pts / risk,
                                   "usd": pts * MNQ_PT - COMM_RT})
                    pos = None
                continue
            if not (ENTRY_FROM <= tms[i] <= ENTRY_TO):
                continue
            sig = signal_at(i, o, h, l, c, vw, a, put_w, call_w, setups)
            if not sig or n_set.get(sig["setup"], 0) >= MAX_PER_SETUP_DAY:
                continue
            e = o[i + 1] + (TICK if sig["side"] == "long" else -TICK)
            risk = (e - sig["stop"]) if sig["side"] == "long" else (sig["stop"] - e)
            if risk < MIN_RISK_PTS:
                continue
            tgt = e + RR * risk if sig["side"] == "long" else e - RR * risk
            n_set[sig["setup"]] = n_set.get(sig["setup"], 0) + 1
            pos = {"day": d, "ts": g.index[i + 1], "setup": sig["setup"], "side": sig["side"], "entry": e, "stop": sig["stop"], "target": tgt,
                   "level": sig["level"], "put_wall": put_w, "call_wall": call_w}
    return pd.DataFrame(trades)


def stats(t: pd.DataFrame) -> dict[str, Any]:
    if t is None or not len(t):
        return {"n": 0}
    r = t["r"].values
    eq = np.cumsum(t["usd"].values)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    sd = r.std(ddof=1) if len(r) > 1 else 0
    days = t["day"].nunique()
    return {"n": int(len(t)), "win": round(float((r > 0).mean()) * 100, 1), "avg_r": round(float(r.mean()), 3),
            "t_stat": round(float(r.mean() / (sd / math.sqrt(len(r)))), 2) if sd else None,
            "pf": round(float(t.loc[t.usd > 0, "usd"].sum() / max(1e-9, -t.loc[t.usd < 0, "usd"].sum())), 2),
            "usd_mnq": round(float(eq[-1]), 0), "max_dd_mnq": round(dd, 0), "trades_per_week": round(len(t) / max(1, days) * 5 / 1.0, 2) if days else 0,
            "targets": int((t.why == "target").sum()), "stops": int((t.why == "stop").sum()), "eod": int((t.why == "close").sum())}


def report() -> dict[str, Any]:
    """Every variant, in-sample (to 2023) vs out-of-sample (2024+), gamma grid vs half-step control. Cached to data_cache/gamma_vwap/report.json."""
    df = load_ndx_5m()
    split = dt.date(2024, 1, 1)
    out: dict[str, Any] = {"generated": time.strftime("%Y-%m-%d %H:%M"), "from": str(df.index[0].date()), "to": str(df.index[-1].date()),
                           "rr": RR, "runs": []}
    for grid in (5.0, 10.0):
        for setups in (("fade",), ("break",), ("reclaim",), ("break", "reclaim")):
            for shift, label in ((0.0, "gamma proxy"), (0.5, "control (half step)")):
                t = backtest(grid, shift, setups, df)
                row = {"grid": grid, "setups": "+".join(setups), "levels": label, "all": stats(t)}
                if len(t):
                    row["in_sample"] = stats(t[t.day < split])
                    row["out_sample"] = stats(t[t.day >= split])
                    row["by_year"] = {str(y): stats(g) for y, g in t.groupby(pd.to_datetime(t.day).dt.year)}
                    for s_, g in t.groupby("setup"):
                        row.setdefault("by_setup", {})[s_] = stats(g)
                out["runs"].append(row)
    json.dump(out, open(os.path.join(CACHE, "report.json"), "w"), indent=1, default=str)
    return out


if __name__ == "__main__":
    rep = report()
    for r in rep["runs"]:
        a, i, o = r["all"], r.get("in_sample", {}), r.get("out_sample", {})
        print(f"${r['grid']:<4g} {r['setups']:<13} {r['levels']:<20} n={a.get('n'):>5} win={a.get('win')}% avgR={a.get('avg_r')} t={a.get('t_stat')} "
              f"PF={a.get('pf')} $MNQ={a.get('usd_mnq')} DD={a.get('max_dd_mnq')} | IS avgR={i.get('avg_r')} n={i.get('n')} | OOS avgR={o.get('avg_r')} n={o.get('n')} t={o.get('t_stat')}")
