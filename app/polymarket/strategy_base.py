"""Common shapes every Polymarket strategy uses — deliberately NOT the futures
Strategy.generate_trades(df, params) interface (see the plan's decision #3): a
momo entry decision needs the share price, the underlying spot price, and session
timing simultaneously, which a single OHLCV df doesn't hold cleanly. ParamSpec
itself IS reused as-is from app/strategies/base.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BuzzerSession:
    """One 5-minute Up-or-Down session's state at a moment in time (`now_ts`) — used
    both for live gate/exit checks (a fresh snapshot each poll) and for backtest
    replay (the caller walks now_ts forward tick by tick through spot_trace)."""
    slug: str
    asset: str
    start_ts: float
    end_ts: float
    now_ts: float
    strike: float                              # spot price at session start
    spot_now: float                            # spot price at now_ts
    up_price: float | None                     # current price of the "Up" outcome token
    down_price: float | None                   # current price of the "Down" outcome token
    sigma_per_min_pct: float | None             # realized vol, see spot.realized_vol_per_min
    spot_trace: list[tuple[float, float]] = field(default_factory=list)  # ascending (t, spot) up to now_ts

    @property
    def remaining_secs(self) -> float:
        return max(0.0, self.end_ts - self.now_ts)

    @property
    def move_pct(self) -> float:
        """Signed % move of the underlying spot from the session's strike."""
        return ((self.spot_now - self.strike) / self.strike) * 100.0 if self.strike else 0.0


@dataclass
class PolymarketTrade:
    session_slug: str
    asset: str
    side: str                # "up" or "down" — which outcome token was bought
    entry_time: float        # unix ts
    exit_time: float
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    exit_reason: str         # "take-profit" | "stop-loss" | "settlement"
    entry_mc_flip: float | None = None


@dataclass
class MomoSignal:
    """momo_gate_check()'s result — an entry decision plus, whether it fired or
    not, the full feature read (for the momo radar UI and the trainer's field/
    candidate log)."""
    enter: bool
    side: str | None = None
    price: float | None = None
    skip_reason: str | None = None
    features: dict[str, Any] = field(default_factory=dict)
