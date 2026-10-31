"""Polymarket strategy registry — a small flat dict, deliberately separate from
app/strategies (the futures registry): PolymarketStrategy uses a different
interface (evaluate_session, not generate_trades(df, params)), so merging the two
registries would just force ugly isinstance branching everywhere they're read."""
from __future__ import annotations

from typing import Any

from app.polymarket import momo_strategy
from app.strategies.base import ParamSpec

REGISTRY: dict[str, dict[str, Any]] = {
    "momo": {
        "name": "momo",
        "label": "BUZZER MOMO",
        "description": "Momentum scalp on 5-minute crypto Up-or-Down sessions, gated by a Monte-Carlo flip-risk model.",
        "params": momo_strategy.MOMO_PARAMS,
        "default_params": momo_strategy.default_params,
        "gate_check": momo_strategy.momo_gate_check,
        "exit_check": momo_strategy.momo_exit_check,
    },
}


def list_strategies() -> list[str]:
    return list(REGISTRY.keys())


def get(name: str) -> dict[str, Any]:
    if name not in REGISTRY:
        raise KeyError(f"unknown polymarket strategy: {name}")
    return REGISTRY[name]


def param_specs(name: str) -> list[ParamSpec]:
    return get(name)["params"]
