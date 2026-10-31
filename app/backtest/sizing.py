"""Risk-based position sizing.

Until now every trade was exactly 1 full E-mini contract (NQ $20/pt, ES $50/pt) whatever
its stop distance - so a 60-point NQ stop risked $1,200 on a $50k account, and 97.7% of NQ
candidates blew the 5% ($2,500) drawdown gate. Real prop traders size to a fixed dollar
risk using micro contracts (MNQ $2/pt, MES $5/pt); this module does the same.

Two modes:
  fixed_1     legacy: 1 mini, no sizing decision. Existing live accounts stay on this so their
              behaviour does not change underneath them.
  risk_micro  qty micros = floor(risk$ / risk-per-micro), where risk-per-micro includes one
              tick of adverse stop slippage so a stop-out costs ~1R. Capped at the Apex
              contract limit; a trade whose ONE-micro risk exceeds the hard cap is skipped.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

POINT_VALUE = {("NQ", "mini"): 20.0, ("NQ", "micro"): 2.0, ("ES", "mini"): 50.0, ("ES", "micro"): 5.0}
MICRO_ROOT = {"NQ": "MNQ", "ES": "MES"}
COMMISSION_PER_CONTRACT = {"mini": 5.0, "micro": 1.25}  # round turn, per contract

RISK_PCT_OF_STARTING = 0.01  # target risk per trade: $500 on a $50k account
MAX_RISK_PCT_OF_STARTING = 0.02  # never open a trade whose minimum size still risks more than this
DEFAULT_MAX_MICROS = 60  # Apex 50k tier: 6 minis = 60 micros

LEGACY_MODE = "fixed_1"
RISK_MODE = "risk_micro"


@dataclass(frozen=True)
class SizingConfig:
    mode: str = RISK_MODE
    risk_dollars: float = 500.0
    max_risk_dollars: float = 1000.0
    max_micros: int = DEFAULT_MAX_MICROS


@dataclass(frozen=True)
class SizedPosition:
    qty: float
    contract: str  # "mini" | "micro"
    point_value: float  # dollars per point per contract
    risk_dollars: float  # planned risk at the stop (stop slippage tick included for micro sizing)
    commission: float  # round-turn commission for the whole position


LEGACY = SizingConfig(mode=LEGACY_MODE, risk_dollars=0.0, max_risk_dollars=0.0, max_micros=0)


def default_config(starting_balance: float = 50_000.0, risk_dollars: float | None = None, max_contracts_minis: int | None = None) -> SizingConfig:
    """The global risk-sized config. The optimizer always scores on this off a fixed $50k
    (non-compounding) so fold scores stay comparable run to run. max_contracts_minis, when
    given, is the account's own Apex-tier contract cap (e.g. 4 for a 25k account) - converted
    to micros at the standard 10-micro-per-mini ratio - rather than the 50k-tier default."""
    return SizingConfig(
        mode=RISK_MODE,
        risk_dollars=risk_dollars if risk_dollars else starting_balance * RISK_PCT_OF_STARTING,
        max_risk_dollars=starting_balance * MAX_RISK_PCT_OF_STARTING,
        max_micros=max_contracts_minis * 10 if max_contracts_minis else DEFAULT_MAX_MICROS,
    )


def config_for(mode: str | None, starting_balance: float = 50_000.0, risk_dollars: float | None = None, max_contracts_minis: int | None = None) -> SizingConfig:
    if mode == LEGACY_MODE:
        return LEGACY
    return default_config(starting_balance, risk_dollars, max_contracts_minis)


def point_value(symbol: str, contract: str = "mini") -> float:
    return POINT_VALUE.get((symbol.upper(), contract), POINT_VALUE[("NQ", contract)] if contract in ("mini", "micro") else 20.0)


def size_position(symbol: str, entry: float, stop: float, cfg: SizingConfig, tick: float = 0.25) -> SizedPosition | None:
    """Size one trade, or None to skip it. entry/stop are the (tick-rounded) planned prices."""
    stop_dist = abs(entry - stop)
    if cfg.mode == LEGACY_MODE:
        pv = point_value(symbol, "mini")
        return SizedPosition(1.0, "mini", pv, stop_dist * pv, COMMISSION_PER_CONTRACT["mini"])

    pv = point_value(symbol, "micro")
    risk_per_micro = (stop_dist + tick) * pv
    if risk_per_micro <= 0:
        return None
    qty = math.floor(cfg.risk_dollars / risk_per_micro)
    qty = min(qty, cfg.max_micros)
    if qty < 1:
        if risk_per_micro > cfg.max_risk_dollars:
            return None
        qty = 1
    return SizedPosition(float(qty), "micro", pv, qty * risk_per_micro, qty * COMMISSION_PER_CONTRACT["micro"])
