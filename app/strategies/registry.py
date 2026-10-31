from app.strategies.base import Strategy
from app.strategies.fair_value_gap import FairValueGap
from app.strategies.ny_open_breakout import NyOpenBreakout
from app.strategies.orb_fade import OrbFade
from app.strategies.orb_retest import OrbRetest
from app.strategies.smt_divergence import SmtDivergence
from app.strategies.order_block import OrderBlock
from app.strategies.signal_strategies import (
    BollingerBreakout,
    DonchianBreakout,
    EmaCrossover,
    InsideBarBreakout,
    LiquiditySweep,
    MacdCross,
    PrevDayRangeBreakout,
    RsiReversal,
    VwapReversion,
)

_STRATEGIES: dict[str, Strategy] = {
    s.name: s
    for s in [
        NyOpenBreakout(),
        OrbFade(),
        VwapReversion(),
        EmaCrossover(),
        DonchianBreakout(),
        RsiReversal(),
        BollingerBreakout(),
        PrevDayRangeBreakout(),
        MacdCross(),
        InsideBarBreakout(),
        OrbRetest(),
        FairValueGap(),
        OrderBlock(),
        LiquiditySweep(),
        SmtDivergence(),
    ]
}


# generated strategies: the three opening-range strategies with searchable entry filters (see composed.py)
from app.strategies.composed import FilteredStrategy  # noqa: E402

for _base in ("orb_retest", "ny_open_breakout", "orb_fade"):
    _f = FilteredStrategy(_STRATEGIES[_base])
    _STRATEGIES[_f.name] = _f

# "flip": every strategy also exists traded in reverse, so a reliably-losing one can be tested as the opposite bet
from app.strategies.composed import FlippedStrategy  # noqa: E402

# combined strategies: members traded as one strategy, merged in time order, one position at a time (see composed.py)
from app.strategies.composed import CombinedStrategy  # noqa: E402

for _cn, _members in (("combo_orb_nyob", ("orb_retest", "ny_open_breakout")), ("combo_orb_nyob_fvg", ("orb_retest", "ny_open_breakout", "fair_value_gap"))):
    _STRATEGIES[_cn] = CombinedStrategy(_cn, [_STRATEGIES[m] for m in _members])

# all-day versions of the bar-signal strategies: trade 18:00 ET -> 16:30 ET, flat at 16:55 ET (00:00 Israel)
from app.strategies.composed import AllDayStrategy  # noqa: E402

for _base in ("vwap_reversion", "rsi_reversal", "ema_crossover", "donchian_breakout", "bollinger_breakout", "macd_cross", "inside_bar_breakout",
              "prev_day_range_breakout", "liquidity_sweep", "fair_value_gap", "order_block"):
    _d = AllDayStrategy(_STRATEGIES[_base])
    _STRATEGIES[_d.name] = _d

for _name in list(_STRATEGIES):
    if not _name.endswith("_f") and not _name.endswith("_day"):
        _fl = FlippedStrategy(_STRATEGIES[_name])
        _STRATEGIES[_fl.name] = _fl


def get(name: str) -> Strategy:
    if name not in _STRATEGIES:
        raise KeyError(f"Unknown strategy '{name}'. Available: {list(_STRATEGIES)}")
    return _STRATEGIES[name]


def list_strategies() -> list[Strategy]:
    return list(_STRATEGIES.values())
