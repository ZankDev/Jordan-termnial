"""Common interface every rule-based strategy implements.

A strategy turns an OHLCV DataFrame + a parameter dict into a list of Trade objects.
Keeping this explicit (vs. a black-box model) is what makes the optimizer's output
explainable: every "trained" result is just a named strategy plus a parameter set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class ParamSpec:
    name: str
    default: float
    min: float
    max: float
    step: float
    kind: str = "float"  # "float" or "int"

    def cast(self, value: float):
        return int(round(value)) if self.kind == "int" else float(value)


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: str  # "long" or "short"
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    exit_reason: str  # "target", "stop", "session_close"
    # Best / worst price reached while the trade was open (long: high / low, short: low / high).
    # Optional - only the bar-by-bar strategies fill them - and used to make the Apex
    # trailing-drawdown replay pessimistic about intra-trade excursions.
    mfe_price: float | None = None
    mae_price: float | None = None


@dataclass
class Strategy:
    name: str
    description: str
    params: list[ParamSpec] = field(default_factory=list)

    def default_params(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def generate_trades(self, df: pd.DataFrame, params: dict[str, Any]) -> list[Trade]:
        raise NotImplementedError
