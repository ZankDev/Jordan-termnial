"""Turns every evaluated candidate into one compact row for The Field page.

The optimizer evaluates ~1,100 parameter sets per cycle but only ever stored the top 5 per
strategy (~5%), so the bulk of the "field" - the mediocre and losing majority that makes the
survivors meaningful - was thrown away. CandidateCollector is handed to the search as a
sink and sees every one of them, including candidates rejected by the trade-count gate.

Everything is in R (multiples of the dollars a trade risked at its stop) so candidates of
any strategy/size are comparable:
    RR    = average win in R / average loss in R
    E_net = mean net P&L per trade in R;  E_gross = the same before commission
    costR = commission per trade in R
"""
from __future__ import annotations

import statistics
import time
from typing import Any

from app import db
from app.optimizer.epoch import SCORING_EPOCH
from app.optimizer.search import is_eligible, params_hash

FLAG_DIVERGED, FLAG_SMOOTH, FLAG_BURN, FLAG_REGIME, FLAG_NEG = 1, 2, 4, 8, 16
SAMPLE_MODULUS = 4  # non-survivors: keep 1 in 4 (by params hash), weighted x4 so counts stay honest


def pool_test_metrics(metric_dicts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pool several folds' test metrics exactly from their additive sums. Returns None if
    there are no trades or the metrics predate the R-multiple fields."""
    n = sum(m.get("total_trades", 0) for m in metric_dicts)
    if n <= 0 or any("sum_r" not in m for m in metric_dicts):
        return None
    g = lambda k: sum(m.get(k, 0.0) for m in metric_dicts)  # noqa: E731
    n_wins, n_wins_g = g("n_wins"), g("n_wins_gross")
    losses, losses_g = n - n_wins, n - n_wins_g

    def rr(sum_win: float, wins: float, sum_loss: float, loss_n: float) -> float | None:
        if wins <= 0 or loss_n <= 0 or sum_loss <= 0:
            return None
        return (sum_win / wins) / (sum_loss / loss_n)

    return {
        "n": int(n),
        "wr_net": n_wins / n * 100,
        "wr_gross": n_wins_g / n * 100,
        "rr_net": rr(g("sum_win_r"), n_wins, g("sum_loss_r"), losses),
        "rr_gross": rr(g("sum_win_r_gross"), n_wins_g, g("sum_loss_r_gross"), losses_g),
        "exp_net": g("sum_r") / n,
        "exp_gross": g("sum_r_gross") / n,
        "cost_r": g("sum_cost_r") / n,
        "risk_usd": sum(m.get("avg_risk_dollars", 0.0) * m.get("total_trades", 0) for m in metric_dicts) / n,
    }


class CandidateCollector:
    """Sink for search._search_given_folds. Cheap per call; one batched insert per cycle."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.rows: list[tuple] = []
        self.counts = {"total": 0, "t0": 0, "t1": 0, "t2": 0, "gp": 0, "np": 0, "gp_nn": 0}
        self.cost_r: list[float] = []
        self.risk: list[float] = []
        self._ts = int(time.time())

    def add(self, strategy: str, params: dict[str, Any], result: dict[str, Any] | None, reject: dict[str, Any] | None) -> None:
        if result is not None:
            pooled = pool_test_metrics([fm["test_metrics"] for fm in result.get("fold_metrics", [])])
            survivor = is_eligible(result) and result["score"] > 0
            tier = 2 if survivor else 1
            flags = (
                (FLAG_DIVERGED if result.get("diverged") else 0) | (FLAG_SMOOTH if result.get("too_smooth") else 0)
                | (FLAG_BURN if result.get("exceeds_burn_limit") else 0)
                | (FLAG_REGIME if result.get("regime_inconsistent") else 0) | (FLAG_NEG if result["score"] <= 0 else 0)
            )
            score = result["score"]
        else:
            tm = (reject or {}).get("test_metrics")
            pooled = pool_test_metrics([tm]) if tm else None
            tier, flags, score = 0, 0, None
        if pooled is None:
            return
        c = self.counts
        c["total"] += 1
        c[f"t{tier}"] += 1
        gp, npos = pooled["exp_gross"] > 0, pooled["exp_net"] > 0
        c["gp"] += gp
        c["np"] += npos
        c["gp_nn"] += gp and not npos
        self.cost_r.append(pooled["cost_r"])
        self.risk.append(pooled["risk_usd"])

        h = params_hash(params)
        if tier < 2 and int(h, 16) % SAMPLE_MODULUS != 0:
            return
        import json

        self.rows.append((
            self._ts, None, SCORING_EPOCH, self.symbol, strategy, tier, flags, pooled["n"],
            pooled["wr_net"], pooled["wr_gross"], pooled["rr_net"], pooled["rr_gross"],
            pooled["exp_net"], pooled["exp_gross"], pooled["cost_r"], pooled["risk_usd"], score, None,
            1 if tier == 2 else SAMPLE_MODULUS, json.dumps(params) if tier == 2 else None,
        ))

    def flush(self, run_id: int | None) -> None:
        if not self.counts["total"]:
            return
        rows = [(r[0], run_id, *r[2:]) for r in self.rows]
        stats = (
            self._ts, run_id, SCORING_EPOCH, self.symbol, self.counts["total"], self.counts["t0"], self.counts["t1"],
            self.counts["t2"], self.counts["gp"], self.counts["np"], self.counts["gp_nn"], None,
            statistics.median(self.cost_r) if self.cost_r else None,
            statistics.median(self.risk) if self.risk else None,
        )
        db.insert_candidates(rows, stats)
