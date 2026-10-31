"""Meta-labeling (Jansen ch. 7; López de Prado): the strategy decides WHEN and WHICH WAY, the model decides whether to TAKE
the trade.

Labels are triple-barrier outcomes — the strategy's own stop / target / time exit — so label = 1 if the trade made money
net of costs. Features = the alpha factors of the signal bar + the trade's direction.

Honesty rules (ch. 6-7):
  * walk-forward: the searched period is cut into `folds` consecutive blocks; the model for block k is trained ONLY on
    trades that closed before block k starts, minus a 1-day EMBARGO (purging trades whose outcome overlaps the boundary)
  * the take/skip rule is fixed in advance: take a trade when the model's probability >= the win rate it was trained on
    (it must believe this trade is better than the strategy's average) — no threshold tuning on the test data
  * the holdout (never searched) is predicted by a model trained only on trades before it
So every filtered trade below is one the model never saw.
"""
from __future__ import annotations

from typing import Any

import lightgbm  # noqa: F401  — must load BEFORE scikit-learn: on Windows the reverse order crashes LightGBM (OpenMP DLL clash)
import numpy as np
import pandas as pd

from app.ntlab import metrics
from app.ntlab.ml import features

EMBARGO = pd.Timedelta(days=1)


def _model(n: int):
    """LightGBM for every fold; smaller samples get a smaller, more regularised model. (Mixing scikit-learn models into the
    same process crashed LightGBM's native code on Windows, so it is LightGBM only.)"""
    import lightgbm as lgb
    small = n < 200
    return lgb.LGBMClassifier(n_estimators=120 if small else 250, learning_rate=0.03, num_leaves=7 if small else 15,
                              min_child_samples=max(8, n // 25 if small else n // 40), subsample=0.8, subsample_freq=1, colsample_bytree=0.6,
                              reg_lambda=5.0 if small else 2.0, n_jobs=1, verbose=-1)


def _xy(df: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, np.ndarray]:
    X = features.at_signal(symbol, df["et"])
    X["side"] = np.where(df["side"].to_numpy() == "long", 1.0, -1.0)
    # directional factors seen from the trade's side (a +2 ATR move means "with me" for a long, "against me" for a short)
    for k in ("ret_1", "ret_3", "ret_6", "ret_12", "ret_24", "ret_78", "d_sma20", "d_sma50", "d_sma200", "d_vwap", "day_ret", "slope_12", "slope_48", "gap"):
        X[k + "_s"] = X[k] * X["side"]
    y = (df["net"].to_numpy() > 0).astype(int)
    return X, y


def filter_trades(trades: list[dict[str, Any]], symbol: str, start: str, holdout_from: str, end: str | None = None, folds: int = 5,
                  min_train: int = 60) -> dict[str, Any]:
    df = metrics.frame(trades)
    if df.empty or len(df) < min_train + 20:
        return {"ok": False, "reason": f"only {len(df)} trades — meta-labeling needs at least {min_train + 20}"}
    X, y = _xy(df, symbol)
    t0, th = pd.Timestamp(start), pd.Timestamp(holdout_from)
    search = df["et"] < th
    edges = [t0 + (th - t0) * (0.4 + 0.6 * i / folds) for i in range(folds + 1)]   # first 40% = initial training only
    proba = np.full(len(df), np.nan)
    take = np.zeros(len(df), dtype=bool)
    imp = np.zeros(X.shape[1])
    fitted = 0
    for k in range(folds + 1):
        lo = edges[k] if k < folds else th
        hi = edges[k + 1] if k < folds else (pd.Timestamp(end) if end else df["et"].max() + pd.Timedelta(days=1))
        test = (df["et"] >= lo) & (df["et"] < hi)
        train = (df["xt"] < lo - EMBARGO)                             # purge: outcome known before the block (minus embargo)
        if test.sum() == 0 or train.sum() < min_train or y[train.values].min() == y[train.values].max():
            continue
        Xtr = np.ascontiguousarray(X[train.values].to_numpy(np.float64))
        ytr = np.ascontiguousarray(y[train.values].astype(np.float64))
        Xte = np.ascontiguousarray(X[test.values].to_numpy(np.float64))
        p = np.zeros(int(test.sum()))
        for seed in (0, 1, 2):   # 3-seed ensemble: the verdict must not depend on one random seed
            m = _model(int(train.sum())).set_params(random_state=seed)
            m.fit(Xtr, ytr)
            p += m.predict_proba(Xte)[:, 1] / 3
        base = y[train.values].mean()
        proba[test.values] = p
        take[test.values] = p >= base
        fitted += 1
        if hasattr(m, "feature_importances_"):
            imp += m.feature_importances_
    wf = (~np.isnan(proba)) & search.values
    hold = (~np.isnan(proba)) & ~search.values
    if wf.sum() < 20:
        return {"ok": False, "reason": "not enough walk-forward predictions"}

    def stats(mask: np.ndarray) -> dict[str, Any]:
        return metrics._stats(df[mask])

    keep = df[wf & take]
    wf_start = edges[0]
    filt_metrics = metrics.evaluate(keep.assign(pnl=keep["pnl"], comm=keep.get("comm", 0)).to_dict("records"), wf_start.isoformat()[:10], holdout_from, split=0.5, folds=4) if len(keep) else None
    out = {
        "ok": True, "model": "lightgbm", "folds_fitted": fitted, "trades": int(len(df)),
        "walk_forward": {"from": wf_start.date().isoformat(), "all": stats(wf), "taken": stats(wf & take), "skipped": stats(wf & ~take),
                         "take_rate": round(float(take[wf].mean()), 3)},
        "holdout": {"all": stats(hold), "taken": stats(hold & take), "skipped": stats(hold & ~take),
                    "take_rate": round(float(take[hold].mean()), 3) if hold.sum() else None},
        "filtered_metrics": filt_metrics,
        "auc_wf": _auc(y[wf], proba[wf]),
        "importance": sorted(zip(X.columns, (imp / max(1, imp.sum())).round(4).tolist()), key=lambda z: -z[1])[:12] if imp.sum() else [],
    }
    taken = df[(wf | hold) & take]
    out["taken_trades"] = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in taken[["et", "xt", "side", "pnl", "comm"]].to_dict("records")]
    return out


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    try:
        from sklearn.metrics import roc_auc_score
        return round(float(roc_auc_score(y, p)), 3) if len(set(y)) > 1 else None
    except Exception:  # noqa: BLE001
        return None
