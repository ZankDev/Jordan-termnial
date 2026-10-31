"""Statistics and the robust score.

A candidate is judged only on what it would have earned net of commission and slippage:
  * IS / OOS: the test period is split by time — the first 70% is in-sample, the last 30% out-of-sample. The search
    only ever SEES the score, but the score leans on the out-of-sample half and punishes in-sample/out-of-sample decay.
  * walk-forward: four consecutive folds; a robust strategy makes money in most of them, not just one lucky stretch.
  * Apex 50K: repeated evaluations on the trade sequence ($3,000 target, $2,500 trailing drawdown that stops trailing at
    the start balance) -> pass rate.
  * champion gates (`is_champion`) define "found a strategy": strict, out-of-sample, multi-fold, with enough trades.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

GATES = {"min_trades_is": 60, "min_trades_oos": 25, "pf_oos": 1.30, "sharpe_oos": 0.8, "folds_positive": 3, "months_positive_oos": 0.55,
         "max_dd": 6000.0, "stability": 0.6, "dsr": 0.95,
         "holdout_stability": 0.6, "holdout_pf": 1.2}


def _stats(df: pd.DataFrame, days: int | None = None) -> dict[str, Any]:
    if df.empty:
        return {"n": 0, "net": 0.0, "pf": 0.0, "win": 0.0, "avg": 0.0, "max_dd": 0.0, "sharpe": 0.0, "best_day_share": 0.0}
    net = df["net"].to_numpy()
    gp, gl = net[net > 0].sum(), -net[net < 0].sum()
    eq = np.cumsum(net)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq])) - np.concatenate([[0.0], eq])))
    daily = df.groupby(df["xt"].dt.normalize())["net"].sum()
    span = days or max(1, (df["xt"].max() - df["et"].min()).days)
    # annualised Sharpe on calendar trading days (days without a trade count as 0)
    tdays = max(len(daily), int(span * 252 / 365))
    mean = daily.sum() / tdays
    var = (np.sum((daily.to_numpy() - mean) ** 2) + (tdays - len(daily)) * mean ** 2) / max(1, tdays - 1)
    sharpe = float(mean / math.sqrt(var) * math.sqrt(252)) if var > 0 else 0.0
    total = float(net.sum())
    return {"n": int(len(net)), "net": round(total, 2), "pf": round(float(gp / gl), 3) if gl > 0 else (99.0 if gp > 0 else 0.0),
            "win": round(float((net > 0).mean()), 3), "avg": round(total / len(net), 2), "max_dd": round(dd, 2), "sharpe": round(sharpe, 3),
            "best_day_share": round(float(daily.max() / total), 3) if total > 0 else 1.0}


def frame(trades: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(trades)
    if df.empty:
        return pd.DataFrame(columns=["et", "xt", "net"])
    df["et"] = pd.to_datetime(df["et"])
    df["xt"] = pd.to_datetime(df["xt"])
    df["net"] = df["pnl"] - df.get("comm", 0.0)
    return df.sort_values("xt").reset_index(drop=True)


def apex(df: pd.DataFrame, target: float = 3000.0, trail: float = 2500.0) -> dict[str, Any]:
    passes = fails = 0
    trades_to_pass: list[int] = []
    bal = peak = 0.0
    k = 0
    for x in df["net"].to_numpy() if not df.empty else []:
        bal += x; k += 1
        peak = max(peak, bal)
        floor = min(peak - trail, 100.0)  # the trailing threshold stops trailing at start balance + $100
        if bal >= target:
            passes += 1; trades_to_pass.append(k); bal = peak = 0.0; k = 0
        elif bal <= floor:
            fails += 1; bal = peak = 0.0; k = 0
    tries = passes + fails
    return {"passes": passes, "fails": fails, "pass_rate": round(passes / tries, 3) if tries else 0.0,
            "avg_trades_to_pass": round(float(np.mean(trades_to_pass)), 1) if trades_to_pass else None}


def evaluate(trades: list[dict[str, Any]], start: str, end: str, split: float = 0.7, folds: int = 4) -> dict[str, Any]:
    df = frame(trades)
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
    cut = t0 + (t1 - t0) * split
    ins, oos = df[df["et"] < cut], df[df["et"] >= cut]
    out: dict[str, Any] = {"all": _stats(df, (t1 - t0).days), "is": _stats(ins, (cut - t0).days), "oos": _stats(oos, (t1 - cut).days),
                           "split": cut.date().isoformat()}
    edges = [t0 + (t1 - t0) * i / folds for i in range(folds + 1)]
    out["folds"] = [round(float(df[(df["et"] >= edges[i]) & (df["et"] < edges[i + 1])]["net"].sum()), 2) for i in range(folds)]
    if not oos.empty:
        m = oos.groupby(oos["xt"].dt.to_period("M"))["net"].sum()
        out["months_positive_oos"] = round(float((m > 0).mean()), 3)
    else:
        out["months_positive_oos"] = 0.0
    out["apex"] = apex(df)
    out["equity"] = [[d.date().isoformat(), round(float(v), 2)] for d, v in df.groupby(df["xt"].dt.normalize())["net"].sum().cumsum().items()][-1500:] if not df.empty else []
    out["score"] = score(out)
    return out


def score(m: dict[str, Any]) -> float:
    """Higher is better. Out-of-sample first; consistency across folds; decay and drawdown punished; few trades punished."""
    i, o = m["is"], m["oos"]
    if i["n"] < 20 or o["n"] < 8:
        return round(-2 + min(i["n"], 60) / 60, 3)
    s = 0.6 * o["sharpe"] + 0.25 * i["sharpe"] + 0.6 * (min(o["pf"], 3) - 1) + 0.25 * (min(i["pf"], 3) - 1)
    s -= 0.25 * max(0.0, i["sharpe"] - o["sharpe"])                   # decay from in- to out-of-sample
    s += 0.3 * (sum(1 for f in m["folds"] if f > 0) / max(1, len(m["folds"])) - 0.5)
    s += 0.3 * (m.get("months_positive_oos", 0) - 0.5)
    s -= 0.15 * max(0.0, m["all"]["max_dd"] / 5000 - 1)               # drawdown beyond $5k (1 ES contract)
    s += 0.05 * math.log10(max(10, i["n"] + o["n"]))
    if m["all"]["best_day_share"] > 0.35:                             # one lucky day is not a strategy
        s -= 0.3
    return round(s, 4)


def is_champion(m: dict[str, Any], stability: float | None = None) -> tuple[bool, list[str]]:
    g, why = GATES, []
    i, o = m["is"], m["oos"]
    if i["n"] < g["min_trades_is"]: why.append(f"in-sample trades {i['n']} < {g['min_trades_is']}")
    if o["n"] < g["min_trades_oos"]: why.append(f"out-of-sample trades {o['n']} < {g['min_trades_oos']}")
    if o["pf"] < g["pf_oos"]: why.append(f"OOS profit factor {o['pf']} < {g['pf_oos']}")
    if o["sharpe"] < g["sharpe_oos"]: why.append(f"OOS Sharpe {o['sharpe']} < {g['sharpe_oos']}")
    if i["net"] <= 0: why.append("in-sample loses money")
    if sum(1 for f in m["folds"] if f > 0) < g["folds_positive"]: why.append(f"profitable in only {sum(1 for f in m['folds'] if f > 0)}/4 walk-forward folds")
    if m.get("months_positive_oos", 0) < g["months_positive_oos"]: why.append(f"only {m.get('months_positive_oos', 0):.0%} of OOS months positive")
    if m["all"]["max_dd"] > g["max_dd"]: why.append(f"max drawdown ${m['all']['max_dd']:,.0f} > ${g['max_dd']:,.0f}")
    if stability is not None and stability < g["stability"]: why.append(f"neighbours only {stability:.0%} profitable OOS (overfit)")
    return (not why), why
