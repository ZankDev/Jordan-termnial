"""Python port of PolyMarket/src/quant.js's flipRisk() — the honest "can this coin
up/down still reverse before the session closes?" Monte Carlo.

N random walks of the coin's remaining-time price path, starting from the
already-realized lead, per-second sigma from the coin's own recent 1-min
volatility (sqrt-time scaled), optional tiny momentum drift. "Up or Down" resolves
on the FINAL price vs. the session's opening strike, so only the endpoint matters:
flip_prob is the fraction of paths that end on the losing side (lead <= 0).

Vectorized with numpy (the JS original loops N x steps in plain JS) — this is
DISTRIBUTIONAL parity with the source model, not bit-for-bit RNG parity (the JS
Math.random() isn't seeded either, so there is no "exact" trace to match).
"""
from __future__ import annotations

import math

import numpy as np

DEFAULT_PATHS = 360
MAX_STEPS = 64
MIN_STEPS = 6


def flip_risk(
    lead_pct: float,
    sigma_per_min_pct: float | None,
    remaining_secs: float,
    drift_per_min_pct: float = 0.0,
    n_paths: int = DEFAULT_PATHS,
    rng: np.random.Generator | None = None,
) -> dict[str, float | int]:
    """Probability the coin ends the session on the losing side of the strike.

    The source bot's Monte Carlo (kept below as flip_risk_mc) sums independent
    Gaussian steps, so its terminal value is exactly Normal(start + drift*T,
    sigma*sqrt(T)) — the estimate it converges to is a closed form. Using it directly
    gives the identical answer with no sampling noise (a candidate scores the same
    every time it is run) and ~1000x less CPU, which is what lets the trainer test
    hundreds of parameter sets a minute. `n_paths`/`rng` are accepted and ignored,
    for call compatibility; se is 0 because there is no sampling error."""
    secs = max(1.0, remaining_secs or 1.0)
    sig = (sigma_per_min_pct if sigma_per_min_pct is not None else 5.0) / 100.0
    sd_total = sig / math.sqrt(60.0) * math.sqrt(secs)
    mean_total = (lead_pct or 0.0) / 100.0 + (drift_per_min_pct or 0.0) / 100.0 / 60.0 * secs
    if sd_total <= 1e-12:
        p = 1.0 if mean_total <= 0 else 0.0
    else:
        p = 0.5 * math.erfc(mean_total / (sd_total * math.sqrt(2.0)))  # P(N(mean, sd) <= 0)
    return {
        "flip_prob": round(p, 4),
        "se": 0.0,
        "end_mean_pct": round(mean_total * 100, 4),
        "end_sd_pct": round(sd_total * 100, 4),
        "n": 0,
        "steps": 0,
    }


def flip_risk_mc(
    lead_pct: float,
    sigma_per_min_pct: float | None,
    remaining_secs: float,
    drift_per_min_pct: float = 0.0,
    n_paths: int = DEFAULT_PATHS,
    rng: np.random.Generator | None = None,
) -> dict[str, float | int]:
    """lead_pct: signed realized move toward the side being evaluated (+ = winning).
    sigma_per_min_pct: the coin's stdev of 1-min returns, in PERCENT (e.g. 0.08).
    Returns {flip_prob, se, end_mean_pct, end_sd_pct, n, steps}."""
    secs = max(1.0, remaining_secs or 1.0)
    sig = (sigma_per_min_pct if sigma_per_min_pct is not None else 5.0) / 100.0
    sig_sec = sig / math.sqrt(60.0)
    drift_sec = (drift_per_min_pct or 0.0) / 100.0 / 60.0
    n = max(1, n_paths)
    steps = min(MAX_STEPS, max(MIN_STEPS, round(secs)))
    dt = secs / steps
    sd = sig_sec * math.sqrt(dt)
    mu = drift_sec * dt
    start = (lead_pct or 0.0) / 100.0

    gen = rng or np.random.default_rng()
    increments = gen.normal(loc=mu, scale=max(sd, 1e-9), size=(n, steps))
    terminal = start + np.cumsum(increments, axis=1)[:, -1]

    flips = int(np.sum(terminal <= 0))
    p = flips / n
    mean = float(terminal.mean())
    variance = max(0.0, float(terminal.var()))
    return {
        "flip_prob": round(p, 4),
        "se": round(math.sqrt(max(0.0, p * (1 - p) / n)), 4),
        "end_mean_pct": round(mean * 100, 4),
        "end_sd_pct": round(math.sqrt(variance) * 100, 4),
        "n": n,
        "steps": steps,
    }
