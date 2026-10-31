"""Deflated Sharpe Ratio (Bailey & López de Prado 2014; Jansen ch. 7 / 16).

Testing many strategies and keeping the best inflates its Sharpe: with N tries some will look great by luck. The DSR is
the probability that the strategy's true Sharpe is above the Sharpe you would EXPECT from the best of N lucky ones,
correcting also for short samples, skew and fat tails. DSR >= 0.95 ~ "real at 95% confidence".
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import kurtosis, norm, skew

EULER = 0.5772156649015329


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """Expected maximum of N per-period Sharpe ratios drawn with variance var_sr around zero."""
    if n_trials < 2 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER) * norm.ppf(1 - 1 / n_trials) + EULER * norm.ppf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(daily_pnl: np.ndarray, n_trials: int, var_sr: float) -> dict[str, float]:
    r = np.asarray(daily_pnl, dtype=float)
    t = len(r)
    if t < 30 or r.std() == 0:
        return {"dsr": 0.0, "sr": 0.0, "sr0": 0.0, "t": t}
    sr = r.mean() / r.std(ddof=1)                     # per-period (daily) Sharpe
    sr0 = expected_max_sharpe(n_trials, var_sr)
    g3, g4 = skew(r), kurtosis(r, fisher=False)
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    z = (sr - sr0) * math.sqrt(t - 1) / denom
    return {"dsr": round(float(norm.cdf(z)), 4), "sr": round(float(sr * math.sqrt(252)), 3), "sr0": round(float(sr0 * math.sqrt(252)), 3), "t": t}


def effective_trials(series: list[np.ndarray], n_total: int, sample: int = 150, seed: int = 0) -> tuple[float, float]:
    """Effective number of independent trials (López de Prado): N_eff = rho + (1 - rho) * N, with rho the average pairwise
    correlation of the strategies' daily PnL. A search that mostly tries near-copies has far fewer real 'tries'."""
    import pandas as pd
    rng = np.random.default_rng(seed)
    pick = [series[i] for i in rng.choice(len(series), size=min(sample, len(series)), replace=False)] if series else []
    df = pd.concat([s for s in pick if len(s) > 30], axis=1) if pick else pd.DataFrame()
    if df.shape[1] < 3:
        return float(n_total), 0.0
    c = df.fillna(0.0).corr().to_numpy()
    rho = float(np.nanmean(c[np.triu_indices_from(c, k=1)]))
    rho = min(max(rho, 0.0), 1.0)
    return rho + (1 - rho) * n_total, rho


def daily_frame(equity: list[list]):
    import pandas as pd
    if len(equity) < 2:
        return pd.Series(dtype=float)
    s = pd.Series([e[1] for e in equity], index=pd.to_datetime([e[0] for e in equity]))
    d = s.diff().fillna(s.iloc[0])
    return d.reindex(pd.bdate_range(d.index.min(), d.index.max()), fill_value=0.0)


def daily_series(equity: list[list]) -> np.ndarray:
    """Per-trading-day PnL from the stored cumulative equity points (days without trades are 0 — included via the calendar)."""
    if len(equity) < 2:
        return np.zeros(0)
    import pandas as pd
    s = pd.Series([e[1] for e in equity], index=pd.to_datetime([e[0] for e in equity]))
    d = s.diff().fillna(s.iloc[0])
    full = d.reindex(pd.bdate_range(d.index.min(), d.index.max()), fill_value=0.0)
    return full.to_numpy()
