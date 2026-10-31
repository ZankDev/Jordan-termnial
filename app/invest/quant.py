"""Quant lab for one security, built on the six standard libraries:
  pandas       price series, returns, alignment of two series
  NumPy        Monte Carlo (geometric Brownian motion) and array maths
  SciPy        distribution fits (normal vs Student-t), skew / kurtosis, Jarque-Bera normality test, parametric VaR
  statsmodels  OLS regression vs a benchmark (alpha, beta, R^2, t-stats), ADF stationarity, autocorrelation, Engle-Granger cointegration
  yfinance /   prices come from the terminal's Yahoo Finance history (same source yfinance wraps), cached
  matplotlib   (the browser draws the charts; the PDF report uses matplotlib)
Every number is computed from the downloaded prices; nothing is assumed except the Monte Carlo model, which is labelled as a model.
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
from statsmodels.tsa.stattools import acf, adfuller, coint

_cache: dict[str, tuple[float, Any]] = {}


def _series(symbol: str, years: int) -> pd.Series:
    from app.api import invest_routes as ir

    h = ir._history(symbol, f"{max(1, years)}y", "1d")
    c = [(pd.Timestamp(x[0], unit="s").normalize(), x[4]) for x in h["candles"] if x[4]]
    s = pd.Series([v for _, v in c], index=[d for d, _ in c], name=symbol, dtype=float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def _f(x: Any, d: int = 4) -> float | None:
    try:
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else round(v, d)
    except (TypeError, ValueError):
        return None


def analyse(symbol: str, bench: str = "SPY", pair: str | None = None, years: int = 3, sims: int = 2000, horizon: int = 252) -> dict[str, Any]:
    key = f"{symbol}:{bench}:{pair}:{years}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    px = _series(symbol, years)
    if len(px) < 120:
        return {"symbol": symbol, "ok": False, "note": "Not enough daily history (need at least ~6 months)."}
    r = px.pct_change().dropna()
    out: dict[str, Any] = {"symbol": symbol, "ok": True, "years": years, "n_days": int(len(r)), "start": str(px.index[0].date()), "end": str(px.index[-1].date()),
                           "last": _f(px.iloc[-1], 2)}

    # ---- distribution (SciPy)
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    t_df, t_loc, t_scale = stats.t.fit(r.values)
    jb = stats.jarque_bera(r.values)
    hist, edges = np.histogram(r.values, bins=60)
    xs = np.linspace(edges[0], edges[-1], 200)
    width = edges[1] - edges[0]
    out["dist"] = {
        "mean_daily": _f(mu, 6), "vol_annual": _f(sd * math.sqrt(252)), "ret_annual": _f((1 + mu) ** 252 - 1), "skew": _f(stats.skew(r.values)),
        "excess_kurtosis": _f(stats.kurtosis(r.values)), "jb_p": _f(jb.pvalue, 6), "normal": bool(jb.pvalue > 0.05),
        "t_df": _f(t_df, 2), "var95_hist": _f(-np.percentile(r.values, 5)), "var99_hist": _f(-np.percentile(r.values, 1)),
        "var95_normal": _f(-(mu + stats.norm.ppf(0.05) * sd)), "var99_normal": _f(-(mu + stats.norm.ppf(0.01) * sd)),
        "var99_t": _f(-stats.t.ppf(0.01, t_df, t_loc, t_scale)), "cvar95": _f(-r[r <= np.percentile(r.values, 5)].mean()),
        "worst_day": _f(r.min()), "best_day": _f(r.max()), "worst_date": str(r.idxmin().date()), "best_date": str(r.idxmax().date()),
        "hist": {"x": [_f((edges[i] + edges[i + 1]) / 2, 5) for i in range(len(hist))], "y": hist.tolist()},
        "pdf_normal": {"x": [_f(v, 5) for v in xs], "y": [_f(v, 2) for v in stats.norm.pdf(xs, mu, sd) * len(r) * width]},
        "pdf_t": {"x": [_f(v, 5) for v in xs], "y": [_f(v, 2) for v in stats.t.pdf(xs, t_df, t_loc, t_scale) * len(r) * width]},
    }

    # ---- regression vs benchmark (statsmodels OLS)
    try:
        b = _series(bench, years).pct_change().dropna()
        df = pd.concat([r.rename("y"), b.rename("x")], axis=1, join="inner").dropna()
        model = sm.OLS(df["y"], sm.add_constant(df["x"])).fit()
        ci = model.conf_int()
        idx = np.linspace(0, len(df) - 1, min(len(df), 600)).astype(int)
        out["regression"] = {
            "bench": bench, "n": int(model.nobs), "alpha_daily": _f(model.params["const"], 6), "alpha_annual": _f(model.params["const"] * 252),
            "alpha_t": _f(model.tvalues["const"], 2), "alpha_p": _f(model.pvalues["const"], 4), "beta": _f(model.params["x"]), "beta_t": _f(model.tvalues["x"], 2),
            "beta_ci": [_f(ci.loc["x", 0]), _f(ci.loc["x", 1])], "r2": _f(model.rsquared), "corr": _f(df["y"].corr(df["x"])),
            "resid_vol_annual": _f(model.resid.std() * math.sqrt(252)),
            "points": {"x": [_f(v, 5) for v in df["x"].values[idx]], "y": [_f(v, 5) for v in df["y"].values[idx]]},
        }
    except Exception as e:  # noqa: BLE001
        out["regression"] = {"error": type(e).__name__}

    # ---- stationarity + autocorrelation (statsmodels)
    lp = np.log(px)
    adf_p, adf_r = adfuller(lp.values, autolag="AIC", result_object=False)[1], adfuller(r.values, autolag="AIC", result_object=False)[1]
    ac = acf(r.values, nlags=10, fft=True)
    band = 1.96 / math.sqrt(len(r))
    out["timeseries"] = {"adf_price_p": _f(adf_p), "adf_return_p": _f(adf_r), "price_stationary": bool(adf_p < 0.05), "returns_stationary": bool(adf_r < 0.05),
                         "acf": [_f(v) for v in ac[1:]], "acf_band": _f(band), "significant_lags": [i + 1 for i, v in enumerate(ac[1:]) if abs(v) > band]}

    # ---- Monte Carlo (NumPy): GBM with the historical drift and volatility
    rng = np.random.default_rng(42)
    lr = np.log1p(r.values)
    m, s = float(lr.mean()), float(lr.std(ddof=1))
    # zero drift by default (a past trend is not a forecast); the historical-drift median is shown only for comparison
    shocks = rng.normal(-0.5 * s * s, s, size=(sims, horizon))
    paths = float(px.iloc[-1]) * np.exp(np.cumsum(shocks, axis=1))
    hist_median = float(px.iloc[-1]) * math.exp(m * horizon)
    pct = np.percentile(paths, [5, 25, 50, 75, 95], axis=0)
    end = paths[:, -1]
    out["montecarlo"] = {"sims": sims, "horizon_days": horizon, "bands": {k: [_f(v, 2) for v in pct[i][::5]] for i, k in enumerate(("p5", "p25", "p50", "p75", "p95"))},
                         "end_p5": _f(np.percentile(end, 5), 2), "end_p50": _f(np.percentile(end, 50), 2), "end_p95": _f(np.percentile(end, 95), 2),
                         "prob_up": _f((end > px.iloc[-1]).mean(), 3), "prob_up20": _f((end > px.iloc[-1] * 1.2).mean(), 3), "prob_dn20": _f((end < px.iloc[-1] * 0.8).mean(), 3), "hist_drift_median": _f(hist_median, 2), "hist_drift_annual": _f(math.exp(m * 252) - 1),
                         "note": "Model: geometric Brownian motion with zero drift and the historical volatility (a past trend is not a forecast). Real returns have fatter tails and changing volatility, so the outer bands are too narrow."}

    # ---- pairs / cointegration (statsmodels Engle-Granger)
    if pair:
        try:
            q = _series(pair, years)
            dfp = pd.concat([np.log(px).rename("a"), np.log(q).rename("b")], axis=1, join="inner").dropna()
            t_stat, p_val, _ = coint(dfp["a"], dfp["b"])
            hedge = sm.OLS(dfp["a"], sm.add_constant(dfp["b"])).fit()
            spread = dfp["a"] - hedge.params["b"] * dfp["b"] - hedge.params["const"]
            z = (spread - spread.mean()) / spread.std()
            lag = spread.shift(1).dropna()
            hl_model = sm.OLS(spread.diff().dropna(), sm.add_constant(lag)).fit()
            k = hl_model.params.iloc[1]
            half_life = -math.log(2) / k if k < 0 else None
            idx = np.linspace(0, len(z) - 1, min(len(z), 500)).astype(int)
            out["pairs"] = {"pair": pair, "coint_p": _f(p_val), "cointegrated": bool(p_val < 0.05), "hedge_ratio": _f(hedge.params["b"]), "z_now": _f(z.iloc[-1], 2),
                            "half_life_days": _f(half_life, 1), "z": {"t": [str(d.date()) for d in z.index[idx]], "v": [_f(v, 3) for v in z.values[idx]]}}
        except Exception as e:  # noqa: BLE001
            out["pairs"] = {"pair": pair, "error": type(e).__name__}

    out["libraries"] = {"pandas": pd.__version__, "numpy": np.__version__, "scipy": __import__("scipy").__version__, "statsmodels": sm.__version__ if hasattr(sm, "__version__") else __import__("statsmodels").__version__}
    _cache[key] = (time.time(), out)
    return out
