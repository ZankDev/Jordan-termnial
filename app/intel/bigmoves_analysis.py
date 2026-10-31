"""BIG MOVERS analysis: turns the event / control / distribution tables into the numbers and charts of the research report.

Sections (each returns plain dicts, saved to data_cache/bigmoves/results.json; charts are rendered by report_bigmoves.py):
  overview       counts per year, size bucket, sector, weekday; the calendar days with the most +-10% movers
  distributions  daily returns scaled by each stock's own 20-day volatility (z): normal vs Student-t, Laplace, logistic, NIG, Cauchy, stable,
                 a 2-regime normal mixture (= jump-diffusion); Hill power-law tail index of raw returns; how often a +-10% day "should" happen
                 under a normal model vs how often it did; earnings days vs ordinary days; volatility clustering
  causes         share of each cause (SEC filings + market/sector breadth), size, gap vs intraday, volume
  after          what happened next (1/5/20/60 sessions, minus SPY) by cause, direction, close location and gap behaviour
  before         event rate by decile of every day-before feature (lift vs the base rate), rules, and a logistic model trained 2016-2021 and
                 tested 2022+ (AUC, top-decile hit rate)
Research only, not investment advice.
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

import numpy as np
import pandas as pd

from app.intel.bigmoves import OUT, TH
from app.intel.bigmoves_study import FEATS, load_events

log = logging.getLogger(__name__)
FRAC = 0.06  # control sampling fraction used in build()
SPLIT = pd.Timestamp("2022-01-01")


def _r(x: Any, d: int = 4) -> Any:
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else round(float(x), d)
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def _tstat(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else 0.0


# ------------------------------------------------------------------------------------------------ overview
def overview(E: pd.DataFrame, C: pd.DataFrame) -> dict[str, Any]:
    stock_days = len(C) / FRAC + len(E)
    E = E.copy()
    E["year"] = E["d"].dt.year
    by_year = E.groupby(["year", "dir"]).size().unstack(fill_value=0)
    cb = pd.cut(E["cap"], [0, 2e9, 10e9, 50e9, 1e15], labels=["<$2B", "$2-10B", "$10-50B", ">$50B"])
    Cb = pd.cut(C["cap"], [0, 2e9, 10e9, 50e9, 1e15], labels=["<$2B", "$2-10B", "$10-50B", ">$50B"])
    cap = {str(k): {"events": int((cb == k).sum()), "rate": _r((cb == k).sum() / ((Cb == k).sum() / FRAC + (cb == k).sum()) * 252, 2)} for k in cb.cat.categories}
    sec = E.groupby("sector").size()
    secC = C.groupby("sector").size() / FRAC
    sectors = sorted([{"sector": s or "n/a", "events": int(sec[s]), "per_stock_year": _r(sec[s] / (secC.get(s, 0) + sec[s]) * 252, 2)} for s in sec.index],
                     key=lambda x: -x["per_stock_year"])
    top_days = E.groupby("d").agg(n=("sym", "size"), up=("r", lambda x: int((x > 0).sum())), spy=("spy_r", "first")).sort_values("n", ascending=False).head(12)
    wd = E.groupby(E["d"].dt.dayofweek).size()
    wdC = C.groupby(C["d"].dt.dayofweek).size() / FRAC
    top_stocks = E.groupby("sym").agg(n=("r", "size"), up=("r", lambda x: int((x > 0).sum())), mx=("r", "max"), mn=("r", "min"), name=("name", "first")).sort_values("n", ascending=False).head(20)
    return {"n_events": int(len(E)), "n_up": int((E["r"] > 0).sum()), "n_down": int((E["r"] < 0).sum()), "n_stocks": int(E["sym"].nunique()),
            "stock_days": int(stock_days), "base_rate": _r(len(E) / stock_days, 5), "base_up": _r((E["r"] > 0).sum() / stock_days, 5),
            "base_dn": _r((E["r"] < 0).sum() / stock_days, 5), "first": str(E["d"].min().date()), "last": str(E["d"].max().date()),
            "by_year": {int(y): {"UP": int(r.get("UP", 0)), "DOWN": int(r.get("DOWN", 0))} for y, r in by_year.iterrows()},
            "cap": cap, "sectors": sectors,
            "weekday": {["Mon", "Tue", "Wed", "Thu", "Fri"][int(k)]: _r(wd[k] / (wdC.get(k, 0) + wd[k]) * 1000, 2) for k in wd.index if k < 5},
            "top_days": [{"d": str(d.date()), "n": int(r["n"]), "up": int(r["up"]), "spy": _r(r["spy"])} for d, r in top_days.iterrows()],
            "top_stocks": [{"sym": s, "n": int(r["n"]), "up": int(r["up"]), "max": _r(r["mx"]), "min": _r(r["mn"]), "name": r["name"]} for s, r in top_stocks.iterrows()],
            "median_abs": _r(E["r"].abs().median()), "p90_abs": _r(E["r"].abs().quantile(0.9)), "max_up": _r(E["r"].max()), "max_dn": _r(E["r"].min())}


# ------------------------------------------------------------------------------------------------ distributions
def _mixture2(z: np.ndarray, it: int = 200) -> dict[str, float]:
    """EM for a zero-mean-ish 2-component normal mixture: a calm regime and a jump regime (Merton jump-diffusion in discrete form)."""
    w, m1, m2, s1, s2 = 0.9, 0.0, 0.0, 0.8, 2.5
    for _ in range(it):
        p1 = w * np.exp(-0.5 * ((z - m1) / s1) ** 2) / s1
        p2 = (1 - w) * np.exp(-0.5 * ((z - m2) / s2) ** 2) / s2
        g = p1 / (p1 + p2 + 1e-300)
        w = g.mean()
        m1, m2 = (g * z).sum() / g.sum(), ((1 - g) * z).sum() / (1 - g).sum()
        s1 = math.sqrt((g * (z - m1) ** 2).sum() / g.sum())
        s2 = math.sqrt(((1 - g) * (z - m2) ** 2).sum() / (1 - g).sum())
    ll = np.log(w * np.exp(-0.5 * ((z - m1) / s1) ** 2) / (s1 * math.sqrt(2 * math.pi)) + (1 - w) * np.exp(-0.5 * ((z - m2) / s2) ** 2) / (s2 * math.sqrt(2 * math.pi))).sum()
    return {"w_calm": w, "m1": m1, "s1": s1, "m2": m2, "s2": s2, "ll": float(ll), "k": 5}


def _mix_sf(x: float, p: dict[str, float]) -> float:
    from scipy.stats import norm
    return p["w_calm"] * (norm.sf(x, p["m1"], p["s1"]) + norm.cdf(-x, p["m1"], p["s1"])) + (1 - p["w_calm"]) * (norm.sf(x, p["m2"], p["s2"]) + norm.cdf(-x, p["m2"], p["s2"]))


def _hill(x: np.ndarray, top: float = 0.01) -> tuple[float, float, int]:
    x = np.sort(x[x > 0])[::-1]
    k = max(50, int(len(x) * top))
    a = 1.0 / np.mean(np.log(x[:k] / x[k]))
    return float(a), float(x[k]), k


def distributions(seed: int = 3) -> dict[str, Any]:
    from scipy import stats
    D = pd.read_parquet(os.path.join(OUT, "dist.parquet"))
    D = D[np.isfinite(D["z"]) & np.isfinite(D["r"])]
    D = D[(D["r"].abs() / D["z"].abs().clip(lower=1e-9) >= 0.003) | (D["r"] == 0)]  # drop stale-price windows (20d vol < 0.3%) that blow z up
    rng = np.random.default_rng(seed)
    zall = D["z"].to_numpy(dtype=float)
    z = zall[rng.random(len(zall)) < min(1.0, 400_000 / len(zall))]
    zs = z[rng.random(len(z)) < 60_000 / len(z)]
    fits: dict[str, dict[str, Any]] = {}
    mu, sd = float(np.mean(z)), float(np.std(z))
    fits["Normal"] = {"params": {"mu": mu, "sigma": sd}, "ll": float(stats.norm.logpdf(z, mu, sd).sum()), "k": 2, "sf": lambda x: 2 * stats.norm.sf(x, 0, sd)}
    df_, loc, sc = stats.t.fit(zs)
    fits["Student-t"] = {"params": {"df": df_, "loc": loc, "scale": sc}, "ll": float(stats.t.logpdf(z, df_, loc, sc).sum()), "k": 3,
                         "sf": lambda x: stats.t.sf(x, df_, loc, sc) + stats.t.cdf(-x, df_, loc, sc)}
    lloc, lsc = stats.laplace.fit(z)
    fits["Laplace"] = {"params": {"loc": lloc, "scale": lsc}, "ll": float(stats.laplace.logpdf(z, lloc, lsc).sum()), "k": 2,
                       "sf": lambda x: stats.laplace.sf(x, lloc, lsc) + stats.laplace.cdf(-x, lloc, lsc)}
    gl, gs = stats.logistic.fit(zs)
    fits["Logistic"] = {"params": {"loc": gl, "scale": gs}, "ll": float(stats.logistic.logpdf(z, gl, gs).sum()), "k": 2,
                        "sf": lambda x: stats.logistic.sf(x, gl, gs) + stats.logistic.cdf(-x, gl, gs)}
    cl, cs = stats.cauchy.fit(zs)
    fits["Cauchy"] = {"params": {"loc": cl, "scale": cs}, "ll": float(stats.cauchy.logpdf(z, cl, cs).sum()), "k": 2,
                      "sf": lambda x: stats.cauchy.sf(x, cl, cs) + stats.cauchy.cdf(-x, cl, cs)}
    try:
        na, nb, nl, ns = stats.norminvgauss.fit(zs[:30_000])
        fits["NIG"] = {"params": {"a": na, "b": nb, "loc": nl, "scale": ns}, "ll": float(stats.norminvgauss.logpdf(z, na, nb, nl, ns).sum()), "k": 4,
                       "sf": lambda x: stats.norminvgauss.sf(x, na, nb, nl, ns) + stats.norminvgauss.cdf(-x, na, nb, nl, ns)}
    except Exception as e:  # noqa: BLE001
        log.warning("nig: %s", e)
    try:
        sa, sb, sl, ss = stats.levy_stable._fitstart(zs)  # McCulloch quantile estimator
        sub = zs[:4000]
        fits["Stable (Levy)"] = {"params": {"alpha": sa, "beta": sb, "loc": sl, "scale": ss},
                                 "ll": float(stats.levy_stable.logpdf(sub, sa, sb, sl, ss).sum() * len(z) / len(sub)), "k": 4,
                                 "sf": lambda x: stats.levy_stable.sf(x, sa, sb, sl, ss) + stats.levy_stable.cdf(-x, sa, sb, sl, ss)}
    except Exception as e:  # noqa: BLE001
        log.warning("stable: %s", e)
    mix = _mixture2(z)
    fits["Normal mixture (jump)"] = {"params": {k: v for k, v in mix.items() if k not in ("ll", "k")}, "ll": mix["ll"], "k": 5, "sf": lambda x: _mix_sf(x, mix)}
    # comparable log-likelihood: every model scored on the same 20,000-day sample (the stable pdf is slow), AIC from that
    actual = {k: float((np.abs(zall) > k).mean()) for k in [2, 3, 4, 5, 6, 8, 10]}
    zc = zs[:20_000]
    llc = {"Normal": stats.norm.logpdf(zc, mu, sd).sum(), "Student-t": stats.t.logpdf(zc, df_, loc, sc).sum(), "Laplace": stats.laplace.logpdf(zc, lloc, lsc).sum(),
           "Logistic": stats.logistic.logpdf(zc, gl, gs).sum(), "Cauchy": stats.cauchy.logpdf(zc, cl, cs).sum()}
    if "NIG" in fits:
        llc["NIG"] = stats.norminvgauss.logpdf(zc, na, nb, nl, ns).sum()
    if "Stable (Levy)" in fits:
        llc["Stable (Levy)"] = stats.levy_stable.logpdf(zc, sa, sb, sl, ss).sum()
    w_, m1_, s1_, m2_, s2_ = mix["w_calm"], mix["m1"], mix["s1"], mix["m2"], mix["s2"]
    llc["Normal mixture (jump)"] = np.log(w_ * stats.norm.pdf(zc, m1_, s1_) + (1 - w_) * stats.norm.pdf(zc, m2_, s2_)).sum()
    for k_, v_ in llc.items():
        fits[k_]["ll"] = float(v_)
    tail_ks = [2, 3, 4, 5, 6, 8, 10]
    table = []
    for name, f in fits.items():
        row = {"name": name, "aic": 2 * f["k"] - 2 * f["ll"], "params": {a: _r(b, 3) for a, b in f["params"].items()}}
        # tail error: mean |log10(model / actual)| over 3..10 sigma (how well the model gets the frequency of big days)
        errs = []
        for k in tail_ks[1:]:
            try:
                pm = float(f["sf"](k))
                errs.append(abs(math.log10(max(pm, 1e-300) / actual[k])))
            except Exception:  # noqa: BLE001
                pass
        row["tail_err"] = float(np.mean(errs)) if errs else None
        for k in tail_ks:
            try:
                row[f"p{k}"] = float(f["sf"](k))
            except Exception:  # noqa: BLE001
                row[f"p{k}"] = None
        table.append(row)
    best = min(t["aic"] for t in table)
    for t in table:
        t["daic"] = t["aic"] - best
    table.sort(key=lambda t: t["aic"])
    # power-law tail on raw returns
    r = D["r"].to_numpy(dtype=float)
    a_up, x_up, k_up = _hill(r)
    a_dn, x_dn, k_dn = _hill(-r)
    # +-10% days expected under each stock's own normal vs actual
    sig = (D["r"].abs() / np.where(D["z"].abs() > 1e-9, D["z"].abs(), np.nan))  # = rv20 on that day (approx., log scale)
    from scipy.stats import norm
    sig = sig.replace([np.inf, -np.inf], np.nan).dropna()
    pn = 2 * norm.sf(math.log(1.1) / sig.clip(lower=1e-4))
    exp_norm = float(pn.sum())
    act = int((D["r"].abs() >= TH).sum())
    big = (D.loc[sig.index, "r"].abs() >= TH)
    sb = pd.cut(sig * math.sqrt(252), [0, 0.2, 0.3, 0.45, 0.65, 1.0, 1.5, 99], labels=["<20%", "20-30%", "30-45%", "45-65%", "65-100%", "100-150%", ">150%"])
    by_vol = []
    for b in sb.cat.categories:
        m = sb == b
        if m.sum() < 1000:
            continue
        by_vol.append({"vol": str(b), "days": int(m.sum()), "actual": int(big[m].sum()), "normal": _r(float(pn[m].sum()), 1),
                       "ratio": _r(float(big[m].sum()) / max(float(pn[m].sum()), 1e-9), 1), "rate": _r(float(big[m].mean()), 5)})
    # earnings vs ordinary days
    ze = D.loc[D["earn"], "z"].to_numpy(dtype=float)
    zn = D.loc[~D["earn"], "z"].to_numpy(dtype=float)
    # clustering: autocorrelation of |r| and r at lag 1..20 for a pooled sample of names
    ac = {"abs": [], "raw": []}
    for sym, g in list(D.groupby("sym"))[:600]:
        x = g["r"].to_numpy(dtype=float)
        if len(x) < 500:
            continue
        ax = np.abs(x) - np.abs(x).mean()
        xr = x - x.mean()
        ac["abs"].append([float((ax[l:] * ax[:-l]).mean() / ax.var()) for l in range(1, 21)])
        ac["raw"].append([float((xr[l:] * xr[:-l]).mean() / xr.var()) for l in range(1, 21)])
    return {"n": int(len(zall)), "fits": table, "actual": actual, "kurtosis_z": _r(float(stats.kurtosis(z)), 2), "skew_z": _r(float(stats.skew(z)), 3),
            "kurtosis_r": _r(float(stats.kurtosis(r[np.abs(r) < 1])), 2),
            "hill": {"up": {"alpha": _r(a_up, 2), "x_min": _r(x_up), "k": k_up}, "down": {"alpha": _r(a_dn, 2), "x_min": _r(x_dn), "k": k_dn}},
            "ten_pct": {"actual": act, "normal_expected": _r(exp_norm, 1), "ratio": _r(act / max(exp_norm, 1e-9), 1), "by_vol": by_vol},
            "earn": {"n": int(len(ze)), "sd": _r(float(np.std(ze)), 2), "p10": _r(float((D.loc[D["earn"], "r"].abs() >= TH).mean()), 4),
                     "kurt": _r(float(stats.kurtosis(ze)), 2)},
            "ordinary": {"n": int(len(zn)), "sd": _r(float(np.std(zn)), 2), "p10": _r(float((D.loc[~D["earn"], "r"].abs() >= TH).mean()), 5),
                         "kurt": _r(float(stats.kurtosis(zn)), 2)},
            "acf_abs": [_r(x, 4) for x in np.mean(ac["abs"], axis=0)], "acf_raw": [_r(x, 4) for x in np.mean(ac["raw"], axis=0)]}


# ------------------------------------------------------------------------------------------------ causes & after
def _ab_stats(g: pd.DataFrame) -> dict[str, Any]:
    out = {"n": int(len(g)), "move": _r(g["r"].mean()), "gap_share": _r(g["gap_share"].median(), 2), "vrel": _r(g["vrel"].median(), 1)}
    for k in (1, 5, 20, 60):
        x = g[f"ab{k}"].dropna()
        out[f"ab{k}"] = _r(x.mean())
        out[f"med{k}"] = _r(x.median())
        out[f"t{k}"] = _r(_tstat(x), 2)
        out[f"cont{k}"] = _r(float((np.sign(x) == np.sign(g.loc[x.index, "r"])).mean()), 3)
    return out


def causes(E: pd.DataFrame) -> dict[str, Any]:
    rows = []
    for (c, d), g in E.groupby(["cause", "dir"]):
        rows.append({"cause": c, "dir": d, **_ab_stats(g)})
    share = (E.groupby("cause").size() / len(E)).sort_values(ascending=False)
    share_dir = E.groupby(["dir", "cause"]).size().groupby(level=0).apply(lambda s: s / s.sum())
    return {"rows": rows, "share": {k: _r(v, 4) for k, v in share.items()},
            "share_dir": {f"{a}|{b}": _r(v, 4) for (a, b), v in share_dir.droplevel(0).items()} if share_dir.index.nlevels == 3 else
            {f"{a}|{b}": _r(v, 4) for (a, b), v in share_dir.items()}}


def after(E: pd.DataFrame) -> dict[str, Any]:
    E = E.copy()
    E["close_q"] = np.where(E["dir"] == "UP", np.where(E["clv"] >= 0.75, "closed near HIGH", np.where(E["clv"] <= 0.4, "faded (closed low)", "mid")),
                            np.where(E["clv"] <= 0.25, "closed near LOW", np.where(E["clv"] >= 0.6, "bounced (closed high)", "mid")))
    E["shape"] = np.where(E["gap_share"] >= 0.8, "gap (overnight news)", np.where(E["gap_share"] <= 0.3, "intraday run", "gap + follow-through"))
    E["idio"] = np.where(E["cause"] == "Market-wide day", "market", "stock-specific")
    E["size"] = pd.cut(E["r"].abs(), [0.1, 0.15, 0.25, 0.5, 5], labels=["10-15%", "15-25%", "25-50%", ">50%"], right=False).astype(str)
    E["vol_q"] = pd.cut(E["vrel"], [0, 2, 4, 8, 1e9], labels=["<2x vol", "2-4x", "4-8x", ">8x"]).astype(str)
    out: dict[str, Any] = {"all": {d: _ab_stats(g) for d, g in E.groupby("dir")}}
    for col in ("close_q", "shape", "idio", "size", "vol_q"):
        out[col] = [{"dir": d, "k": k, **_ab_stats(g)} for (d, k), g in E.groupby(["dir", col]) if len(g) >= 30 and k != "nan"]
    eu = E[(E["cause"] == "Earnings")]
    out["earn_close"] = [{"dir": d, "k": k, **_ab_stats(g)} for (d, k), g in eu.groupby(["dir", "close_q"]) if len(g) >= 30]
    ne = E[E["cause"] == "No filing (news / analyst / flow)"]
    out["nofiling_close"] = [{"dir": d, "k": k, **_ab_stats(g)} for (d, k), g in ne.groupby(["dir", "close_q"]) if len(g) >= 30]
    # repeat risk: another +-10% within 20 sessions after an event (vs base)
    return out


# ------------------------------------------------------------------------------------------------ before: lift + model
def _prep(df: pd.DataFrame) -> pd.DataFrame:
    X = df[FEATS].astype(float).copy()
    for c in ("rv20", "atr_pct", "vol_ratio", "rv_ratio", "rng_ratio"):
        X[c] = np.log(X[c].clip(lower=1e-4))
    for c in ("n_big", "n_big_up", "n_big_dn"):
        X[c] = np.sqrt(X[c].clip(lower=0))
    X["days_since_earn"] = np.log1p(X["days_since_earn"].clip(lower=0))
    return X


def lift(E: pd.DataFrame, C: pd.DataFrame) -> dict[str, Any]:
    out = {}
    both = pd.concat([E.assign(y=1, w=1.0), C.assign(y=0, w=1 / FRAC)])
    both["yu"] = ((both["y"] == 1) & (both["r"] > 0)).astype(int)
    both["yd"] = ((both["y"] == 1) & (both["r"] < 0)).astype(int)
    base = both["y"].sum() / both["w"].sum()
    for f in FEATS:
        x = both[f]
        if x.nunique() <= 3:
            bins = x.round(0)
            labs = sorted(bins.dropna().unique())
        else:
            try:
                bins = pd.qcut(x.where(both["y"] == 0), 10, retbins=True, duplicates="drop")[1]
                bins[0], bins[-1] = -np.inf, np.inf
                bins = pd.cut(x, bins, labels=False)
            except Exception:  # noqa: BLE001
                continue
            labs = sorted(bins.dropna().unique())
        rows = []
        for b in labs:
            m = bins == b
            wsum = both.loc[m, "w"].sum()
            if wsum <= 0:
                continue
            rows.append({"bin": _r(float(b), 2), "lo": _r(float(x[m].min())), "hi": _r(float(x[m].max())), "rate": _r(both.loc[m, "y"].sum() / wsum, 5),
                         "up": _r(both.loc[m, "yu"].sum() / wsum, 5), "dn": _r(both.loc[m, "yd"].sum() / wsum, 5), "n_ev": int(both.loc[m, "y"].sum())})
        if rows:
            rates = [r["rate"] for r in rows]
            out[f] = {"rows": rows, "spread": _r(max(rates) / max(min(rates), 1e-9), 1)}
    return {"base": _r(base, 5), "feats": out}


def _logit(X: np.ndarray, y: np.ndarray, w: np.ndarray, l2: float = 1.0, it: int = 30) -> np.ndarray:
    Xb = np.c_[np.ones(len(X)), X]
    b = np.zeros(Xb.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-np.clip(Xb @ b, -30, 30)))
        W = w * p * (1 - p)
        H = Xb.T @ (Xb * W[:, None]) + l2 * np.eye(len(b))
        g = Xb.T @ (w * (y - p)) - l2 * b
        step = np.linalg.solve(H, g)
        b += step
        if np.abs(step).max() < 1e-6:
            break
    return b


def _auc(s: np.ndarray, y: np.ndarray) -> float:
    o = np.argsort(s)
    rk = np.empty(len(s))
    rk[o] = np.arange(1, len(s) + 1)
    n1 = float(y.sum())
    n0 = float(len(y)) - n1
    return float((rk[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def model(E: pd.DataFrame, C: pd.DataFrame) -> dict[str, Any]:
    res = {}
    for name, filt in (("all days", None), ("no earnings tomorrow", 0.0)):
        both = pd.concat([E.assign(y=1, w=1.0), C.assign(y=0, w=1 / FRAC)])
        if filt is not None:
            both = both[both["earn_next"] == filt]
            feats = [f for f in FEATS if f != "earn_next"]
        else:
            feats = FEATS
        X = _prep(both)[feats]
        lo, hi = X[both["d"] < SPLIT].quantile(0.01), X[both["d"] < SPLIT].quantile(0.99)
        X = X.clip(lo, hi, axis=1)
        mu, sd = X[both["d"] < SPLIT].mean(), X[both["d"] < SPLIT].std().replace(0, 1)
        X = ((X - mu) / sd).fillna(0)
        for target in ("y", "up", "dn"):
            yy = both["y"].to_numpy() if target == "y" else ((both["y"] == 1) & ((both["r"] > 0) if target == "up" else (both["r"] < 0))).astype(int).to_numpy()
            tr = (both["d"] < SPLIT).to_numpy()
            b = _logit(X.to_numpy()[tr], yy[tr], both["w"].to_numpy()[tr])
            s = np.c_[np.ones(len(X)), X.to_numpy()] @ b
            te = ~tr
            auc_tr, auc_te = _auc(s[tr], yy[tr]), _auc(s[te], yy[te])
            # weighted hit rate by score decile on the test period (deciles of the population, using control weights)
            st, yt, wt = s[te], yy[te], both["w"].to_numpy()[te]
            o = np.argsort(-st)
            cw = np.cumsum(wt[o]) / wt.sum()
            dec = []
            for q0, q1 in ((0, 0.01), (0.01, 0.05), (0.05, 0.1), (0.1, 0.2), (0.2, 0.5), (0.5, 1.0)):
                m = (cw > q0) & (cw <= q1)
                dec.append({"bucket": f"top {q0 * 100:g}-{q1 * 100:g}%", "rate": _r(yt[o][m].sum() / max(wt[o][m].sum(), 1e-9), 4), "events": int(yt[o][m].sum())})
            res[f"{name}|{target}"] = {"auc_train": _r(auc_tr, 3), "auc_test": _r(auc_te, 3), "base_test": _r(yt.sum() / wt.sum(), 5),
                                       "coef": sorted([{"f": f, "b": _r(float(c), 3)} for f, c in zip(feats, b[1:])], key=lambda x: -abs(x["b"])),
                                       "deciles": dec}
            if name == "all days" and target == "y":
                res["_scaler"] = {"feats": feats, "lo": lo.to_dict(), "hi": hi.to_dict(), "mu": mu.to_dict(), "sd": sd.to_dict(), "b": b.tolist()}
    return res


def rules(E: pd.DataFrame, C: pd.DataFrame) -> list[dict[str, Any]]:
    """Hand-readable combinations: rate of a +-10% day next session when the condition holds, vs the base rate."""
    both = pd.concat([E.assign(y=1, w=1.0), C.assign(y=0, w=1 / FRAC)])
    base = both["y"].sum() / both["w"].sum()
    q = lambda f, p: both.loc[both["y"] == 0, f].quantile(p)  # noqa: E731
    conds = {
        "Earnings tomorrow": both["earn_next"] == 1,
        "Earnings tomorrow + had 3+ big days in the past year": (both["earn_next"] == 1) & (both["n_big"] >= 3),
        "Earnings tomorrow + 20d volatility in top 20%": (both["earn_next"] == 1) & (both["rv20"] >= q("rv20", 0.8)),
        "Earnings tomorrow + small cap (<$2B)": (both["earn_next"] == 1) & (both["cap"] < 2e9),
        "Earnings tomorrow + mega cap (>$200B)": (both["earn_next"] == 1) & (both["cap"] > 2e11),
        "No earnings, 20d volatility top 10%": (both["earn_next"] == 0) & (both["rv20"] >= q("rv20", 0.9)),
        "No earnings, 6+ big days in the past year": (both["earn_next"] == 0) & (both["n_big"] >= 6),
        "Volatility squeeze (BB width bottom 10% of year)": both["bbw_pct"] <= 0.1,
        "Squeeze + earnings tomorrow": (both["bbw_pct"] <= 0.2) & (both["earn_next"] == 1),
        "Volume dry-up (5d vol < 0.6x 50d)": both["vol_ratio"] < 0.6,
        "Volume surge (5d vol > 2x 50d)": both["vol_ratio"] > 2,
        "Down 50%+ from 52w high": both["from_hi"] <= -0.5,
        "Within 2% of 52w high": both["from_hi"] >= -0.02,
        "Ran +30% in 5 days": both["ret5"] >= 0.3,
        "Fell -25% in 5 days": both["ret5"] <= -0.25,
        "VIX above 30": both["vix"] >= 30,
        "VIX below 15": both["vix"] < 15,
        "Wyckoff accumulation proxy": both["wy_acc"] == 1,
        "Wyckoff distribution proxy": both["wy_dist"] == 1,
        "Spring (undercut 40d low, closed back above) last 3d": both["spring"] == 1,
        "Upthrust (poked 40d high, closed back below) last 3d": both["upthrust"] == 1,
        "S-1/S-3 shelf filed in last 30d": both["shelf30"] == 1,
        "Beta > 2.5": both["beta"] > 2.5,
        "Price < $10": both["price"] < 1,
    }
    out = []
    for name, m in conds.items():
        m = m.fillna(False)
        wsum = both.loc[m, "w"].sum()
        if wsum <= 0:
            continue
        ev = both.loc[m]
        rate = ev["y"].sum() / wsum
        up = ((ev["y"] == 1) & (ev["r"] > 0)).sum() / wsum
        out.append({"rule": name, "rate": _r(rate, 4), "lift": _r(rate / base, 1), "up": _r(up, 4), "dn": _r(rate - up, 4), "events": int(ev["y"].sum()),
                    "share_days": _r(wsum / both["w"].sum(), 4)})
    return sorted(out, key=lambda x: -x["lift"])


def repeat_risk(E: pd.DataFrame, C: pd.DataFrame) -> dict[str, Any]:
    """After a +-10% day, how likely is another one in the next 1, 5, 20 sessions? (Volatility clustering in event form.)"""
    E = E.sort_values(["sym", "d"])
    out = {}
    for d in ("UP", "DOWN"):
        g = E[E["dir"] == d]
        nxt = []
        for sym, x in E.groupby("sym"):
            ds = x["d"].to_numpy()
            for t in g[g["sym"] == sym]["d"].to_numpy():
                later = ds[ds > t]
                nxt.append(np.busday_count(t.astype("datetime64[D]"), later[0].astype("datetime64[D]")) if len(later) else 10 ** 6)
        nxt = np.array(nxt)
        out[d] = {f"within{k}": _r(float((nxt <= k).mean()), 3) for k in (1, 5, 20, 60)}
    return out


def run() -> dict[str, Any]:
    E = load_events()
    C = pd.read_parquet(os.path.join(OUT, "control.parquet"))
    R = {"overview": overview(E, C)}
    log.info("overview done")
    R["causes"] = causes(E)
    R["after"] = after(E)
    R["rules"] = rules(E, C)
    R["lift"] = lift(E, C)
    log.info("lift done")
    R["model"] = model(E, C)
    log.info("model done")
    R["repeat"] = repeat_risk(E, C)
    R["dist"] = distributions()
    log.info("dist done")
    json.dump(R, open(os.path.join(OUT, "results.json"), "w"), indent=1, default=lambda o: _r(o) if not isinstance(o, (pd.Timestamp,)) else str(o))
    return R


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
