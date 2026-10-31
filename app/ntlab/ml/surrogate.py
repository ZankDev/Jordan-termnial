"""ML-guided search: learn spec -> score from every backtest so far, then spend the next batch where the model expects good
scores — or where it is unsure (exploration). An ensemble of 5 LightGBM regressors on bootstrap samples gives a mean and a
spread; candidates are ranked by mean + kappa * spread (upper confidence bound), like Bayesian optimisation but simple.
"""
from __future__ import annotations

import random
from typing import Any

import numpy as np

from app.ntlab import spec as sp

DIRS = ["both", "long", "short"]
PKEYS = ["range_min", "buffer_ticks", "fast", "slow", "trend_len", "len", "lo", "hi", "k", "dev_atr"]


def encode(s: dict[str, Any]) -> list[float]:
    m = lambda x: int(x[:2]) * 60 + int(x[3:])  # noqa: E731
    p, se, ex = s["p"], s["session"], s["exit"]
    return ([1.0 if s["family"] == f else 0.0 for f in sp.FAMILIES] + [1.0 if s["dir"] == d else 0.0 for d in DIRS]
            + [float(p.get(k, -1)) for k in PKEYS]
            + [m(se["entry_from"]), m(se["entry_to"]), m(se["flat"]), ex["stop_atr"], ex["target_atr"], ex["trail_atr"], ex.get("max_bars", 0),
               s.get("atr_len", 14), s.get("max_trades_day", 2)])


class Surrogate:
    def __init__(self, n_models: int = 5, seed: int = 0):
        self.n, self.seed = n_models, seed
        self.models: list = []

    def fit(self, specs: list[dict[str, Any]], scores: list[float]) -> "Surrogate":
        import lightgbm as lgb
        X = np.asarray([encode(s) for s in specs], dtype=np.float64)
        y = np.clip(np.asarray(scores, dtype=np.float64), -3, 4)
        rng = np.random.default_rng(self.seed)
        self.models = []
        for i in range(self.n):
            idx = rng.integers(0, len(X), len(X))
            m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=8, subsample=0.8, subsample_freq=1,
                                  colsample_bytree=0.8, random_state=i, n_jobs=1, verbose=-1)
            m.fit(np.ascontiguousarray(X[idx]), np.ascontiguousarray(y[idx]))
            self.models.append(m)
        return self

    def predict(self, specs: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
        X = np.ascontiguousarray(np.asarray([encode(s) for s in specs], dtype=np.float64))
        P = np.stack([m.predict(X) for m in self.models])
        return P.mean(axis=0), P.std(axis=0)


def propose(evaluated: list[tuple[dict[str, Any], float]], k: int, rng: random.Random, seen: set[str], pool_size: int = 4000, kappa: float = 0.7) -> list[dict[str, Any]]:
    """k new specs: generate a large pool around the best known ones (mutations, crossovers, randoms), keep the top UCB."""
    if len(evaluated) < 80:
        return []
    specs, scores = [e[0] for e in evaluated], [e[1] for e in evaluated]
    sur = Surrogate(seed=rng.randint(0, 10_000)).fit(specs, scores)
    order = np.argsort(scores)[::-1]
    top = [specs[i] for i in order[:40]]
    pool: dict[str, dict[str, Any]] = {}
    while len(pool) < pool_size:
        r = rng.random()
        if r < 0.6:
            x = sp.mutate(rng.choice(top), rng, rng.choice([0.1, 0.25, 0.5]))
        elif r < 0.8:
            a, b = rng.sample(top, 2)
            x = sp.crossover(a, b, rng)
        else:
            x = sp.random_spec(rng)
        if x["id"] not in seen:
            pool[x["id"]] = x
    cand = list(pool.values())
    mu, sd = sur.predict(cand)
    ucb = mu + kappa * sd
    picked = [cand[i] for i in np.argsort(ucb)[::-1][:k]]
    for x, i in zip(picked, np.argsort(ucb)[::-1][:k]):
        x["role"] = "ml"
        x["pred"] = round(float(mu[i]), 3)
    return picked
