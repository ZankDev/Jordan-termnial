"""Faithful port of the PolyMarket bot's BUZZER_MOMO strategy — the momentum scalp
that buys a mid-priced leading side late in a 5-minute Up-or-Down session and
rides it, gated by src/quant.js's flip-risk Monte Carlo.

Ported from PolyMarket/src/buzzer.js's tryMomoScalp() (entry gate ladder, lines
~1075-1183) and PolyMarket/src/bot.js's decide() momo branch (exit ladder, lines
~1026-1075). Deliberately DROPPED (see plan decision #3 / open risks): the JS
bot's own logistic-regression "trust model" (momoModel/momoTrustworthy — trained
by a separate Node.js pipeline we don't run), Kelly/CVaR position sizing, and the
cross-asset "group correlation" multifactor — those are real refinements but
belong to a v2 once this port has its own real training history (Phase 4).
Portfolio-level caps (max concurrent, max same-direction, daily cap) are
account-level concerns enforced by ledger.py/live_engine.py, not this pure
function — they aren't ParamSpec entries here.

Units note: PolyMarket's own config mixes fractions (0.40) with a `pct` value
that's already ×100. To avoid that ambiguity, every *_pct param and every pct
value this module computes is a plain percentage NUMBER (40.0 means +40%), never
a 0-1 fraction.
"""
from __future__ import annotations

from typing import Any

from app.polymarket import flip_risk as fr
from app.polymarket.strategy_base import BuzzerSession, MomoSignal
from app.strategies.base import ParamSpec

# ---------------------------------------------------------------------------
# Tunable parameters — defaults mirror PolyMarket/src/config.js's buzzerMomo*
# keys exactly (see that file for the trainer-derived comments on each).
# ---------------------------------------------------------------------------
MOMO_PARAMS: list[ParamSpec] = [
    ParamSpec("momo_secs", 150, 60, 240, 10, "int"),           # act inside the last N secs of a session
    ParamSpec("momo_min_secs", 25, 10, 60, 5, "int"),          # …but not under this (need room to exit)
    ParamSpec("momo_min_px", 0.52, 0.50, 0.65, 0.01),          # leading side's price must be in [min,max] —
    ParamSpec("momo_max_px", 0.74, 0.65, 0.90, 0.01),          # real upside, real conviction
    ParamSpec("drift_window_secs", 20, 10, 40, 5, "int"),      # momentum lookback
    ParamSpec("momo_pct", 0.020, 0.005, 0.08, 0.005),          # required drift toward the lead over that window, %
    ParamSpec("max_flip", 0.25, 0.10, 0.40, 0.01),             # MC flip prob for the lead must be <= this
    ParamSpec("min_margin", 0.5, 0.2, 1.5, 0.1),               # lead >= this x the coin's "required move"
    ParamSpec("min_lead", 0.04, 0.01, 0.20, 0.01),             # fav's realized lead (% from strike)
    ParamSpec("tp_pct", 40.0, 15.0, 80.0, 5.0),                # bank at +tp_pct% …
    ParamSpec("tp_px", 0.90, 0.80, 0.98, 0.01),                # …or when offered at >= this price
    ParamSpec("trail_trigger_pct", 20.0, 5.0, 50.0, 5.0),      # arm the trail once up at least this %
    ParamSpec("trail_giveback_pct", 12.0, 5.0, 30.0, 1.0),     # bank if it pulls back this many points from peak
    ParamSpec("sl_pct", 40.0, 15.0, 70.0, 5.0),                # cut at -sl_pct%
    ParamSpec("hold_max_flip", 0.20, 0.05, 0.40, 0.05),        # live flip prob <= this at the SL trigger -> hold
    ParamSpec("hard_sl_pct", 65.0, 40.0, 90.0, 5.0),           # always cuts here regardless of live odds
    ParamSpec("bank_flip_min", 0.35, 0.10, 0.60, 0.05),        # while ahead, live flip >= this -> bank now
    ParamSpec("exit_secs", 15, 5, 30, 5, "int"),               # near expiry, exit ONLY if losing by more than…
    ParamSpec("exit_loss_pct", 15.0, 5.0, 40.0, 5.0),          # …this %, else hold (settles at $1)
    ParamSpec("lock_sigma", 2.5, 1.5, 4.0, 0.25),              # required-move sigma multiplier
    ParamSpec("min_move", 0.05, 0.02, 0.15, 0.01),             # absolute floor % for the required move
    ParamSpec("guard_z", 2.4, 1.5, 3.5, 0.25),                 # sigma multiplier for the worst-plausible-adverse band
    ParamSpec("mc_paths", 360, 120, 600, 60, "int"),
]


_DEFAULTS: dict[str, Any] = {p.name: p.default for p in MOMO_PARAMS}


def default_params() -> dict[str, Any]:
    return dict(_DEFAULTS)


def _full(params: dict[str, Any] | None) -> dict[str, Any]:
    """Params with every key present. The trainer replays tens of thousands of
    sessions per candidate and always passes a complete set, so that path is a no-op
    (rebuilding the defaults on every gate call was ~10% of a training cycle)."""
    if not params:
        return _DEFAULTS
    if len(params) >= len(_DEFAULTS):
        return params
    return {**_DEFAULTS, **params}


def repair_params(p: dict[str, Any]) -> dict[str, Any]:
    """Make a sampled/mutated parameter set internally coherent. Independent random
    draws routinely produce nonsense the strategy could never mean — e.g. a take-profit
    price BELOW the entry band (it would "bank" on the first tick, for a tick of
    profit), a stop tighter than the trailing trigger, a window whose lower bound
    exceeds its upper one. Repairing those keeps the search inside sensible strategies
    instead of spending its budget on (and occasionally promoting) degenerate ones."""
    q = dict(p)
    q["momo_max_px"] = max(q["momo_max_px"], q["momo_min_px"] + 0.06)
    q["momo_secs"] = max(q["momo_secs"], q["momo_min_secs"] + 30)
    q["tp_px"] = min(0.98, max(q["tp_px"], q["momo_max_px"] + 0.05))
    q["hard_sl_pct"] = max(q["hard_sl_pct"], q["sl_pct"] + 10)
    q["trail_trigger_pct"] = min(q["trail_trigger_pct"], q["tp_pct"] - 5)
    return q


# ---------------------------------------------------------------------------
# Feature helpers — ported from buzzer.js's recentDrift / requiredMoveFor /
# maxAdverseMovePct / leadIsReverting. `trace` is an ascending list of
# (t, spot_price) samples (BuzzerSession.spot_trace) — the JS original reads the
# same shape from its in-memory moveHist ring per asset+session.
# ---------------------------------------------------------------------------

def _move_pct_at(trace: list[tuple[float, float]], strike: float, t: float) -> float | None:
    if not strike:
        return None
    # nearest sample at-or-before t
    best = None
    for ts, price in trace:
        if ts <= t:
            best = (ts, price)
        else:
            break
    if best is None:
        return None
    return ((best[1] - strike) / strike) * 100.0


def recent_drift(trace: list[tuple[float, float]], strike: float, now_ts: float, window_secs: float) -> dict[str, float] | None:
    """Signed % change in spot's move-from-strike over the last window_secs, plus
    per-second rate. None if there's not enough trace depth yet."""
    if len(trace) < 2 or not strike:
        return None
    newest_t, newest_price = trace[-1]
    newest_move = ((newest_price - strike) / strike) * 100.0
    past = None
    for ts, price in reversed(trace):
        if newest_t - ts >= window_secs:
            past = (ts, price)
            break
    if past is None:
        past = trace[0]
    past_move = ((past[1] - strike) / strike) * 100.0
    dt_sec = max(1.0, newest_t - past[0])
    pct = newest_move - past_move
    return {"pct": pct, "per_sec": pct / dt_sec, "dt_sec": dt_sec}


def required_move_for(sigma_per_min_pct: float | None, remaining_secs: float, params: dict[str, Any]) -> float:
    """The "real lock" range: how far spot must have moved from the strike to be
    statistically confident (lock_sigma std-devs) it can't reverse in the time
    left, using the coin's own recent volatility (random-walk / sqrt-time
    scaling). Falls back to the flat min_move floor with no volatility read."""
    sig_mult = params["lock_sigma"]
    if not sigma_per_min_pct:
        return params["min_move"]
    remain_min = max(remaining_secs, 1.0) / 60.0
    stat_threshold = sig_mult * sigma_per_min_pct * (remain_min ** 0.5)
    adv = max_adverse_move_pct(sigma_per_min_pct, remaining_secs, params) or 0.0
    floor = min(params["min_move"], max(2.5 * adv, 0.02))
    return max(floor, stat_threshold)


def max_adverse_move_pct(sigma_per_min_pct: float | None, remaining_secs: float, params: dict[str, Any]) -> float | None:
    """Worst plausible adverse move (%) still possible in the seconds remaining —
    a guard_z-sigma random-walk step over remaining_secs."""
    if not sigma_per_min_pct:
        return None
    sigma_sec = sigma_per_min_pct / (60.0 ** 0.5)
    return params["guard_z"] * sigma_sec * (max(1.0, remaining_secs) ** 0.5)


def lead_is_reverting(trace: list[tuple[float, float]], strike: float, now_ts: float, now_move_pct: float, max_adv: float | None) -> bool:
    """Was the lead shrinking toward the flip point over the last ~4-14s? A
    shrinking lead in the final seconds is the single most dangerous signal to
    enter into."""
    if len(trace) < 2:
        return False
    prev = None
    for ts, price in reversed(trace):
        age = now_ts - ts
        if 4 <= age <= 14:
            prev = ((price - strike) / strike) * 100.0 if strike else None
            break
    if prev is None:
        return False
    tol = max_adv * 0.2 if max_adv else 0.02
    return abs(now_move_pct) < abs(prev) - tol


# ---------------------------------------------------------------------------
# Entry gate — ports tryMomoScalp()'s gate ladder verbatim, first failure wins.
# ---------------------------------------------------------------------------

def momo_gate_check(session: BuzzerSession, params: dict[str, Any] | None = None, rng=None) -> MomoSignal:
    p = _full(params)
    remaining = session.remaining_secs
    feat: dict[str, Any] = {"remaining": round(remaining, 1)}

    if remaining > p["momo_secs"] or remaining < p["momo_min_secs"]:
        return MomoSignal(False, skip_reason="out-of-window", features=feat)
    if session.up_price is None or session.down_price is None:
        return MomoSignal(False, skip_reason="no-price", features=feat)

    fav_is_up = session.up_price >= session.down_price
    fav_price = session.up_price if fav_is_up else session.down_price
    fav_side = "up" if fav_is_up else "down"
    move_pct = session.move_pct
    fav_lead = move_pct if fav_is_up else -move_pct
    feat.update(fav_side=fav_side, fav_price=round(fav_price, 4), fav_lead=round(fav_lead, 4))

    if fav_price < p["momo_min_px"] or fav_price > p["momo_max_px"]:
        return MomoSignal(False, skip_reason="price-band", features=feat)
    if fav_lead <= 0:
        return MomoSignal(False, skip_reason="fav-behind", features=feat)

    drift20 = recent_drift(session.spot_trace, session.strike, session.now_ts, p["drift_window_secs"])
    if drift20 is None:
        return MomoSignal(False, skip_reason="no-drift", features=feat)
    if fav_lead < p["min_lead"]:
        return MomoSignal(False, skip_reason="lead-too-small", features=feat)

    sgn = 1 if fav_is_up else -1
    drift_fav = sgn * drift20["pct"]
    per_sec_fav = sgn * drift20["per_sec"]
    feat.update(drift_fav=round(drift_fav, 4), per_sec_fav=round(per_sec_fav, 6))
    if drift_fav < p["momo_pct"] or per_sec_fav <= 0:
        return MomoSignal(False, skip_reason="weak-momentum", features=feat)

    max_adv = max_adverse_move_pct(session.sigma_per_min_pct, remaining, p)
    if lead_is_reverting(session.spot_trace, session.strike, session.now_ts, move_pct, max_adv):
        return MomoSignal(False, skip_reason="reverting", features=feat)

    required = required_move_for(session.sigma_per_min_pct, remaining, p)
    margin = fav_lead / required if required > 0 else None
    feat.update(required=round(required, 4), margin=round(margin, 3) if margin is not None else None)
    if required > 0 and fav_lead < required * p["min_margin"]:
        return MomoSignal(False, skip_reason="thin-margin", features=feat)

    mc = fr.flip_risk(fav_lead, session.sigma_per_min_pct, remaining, n_paths=p["mc_paths"], rng=rng)
    feat["mc_flip"] = mc["flip_prob"]
    if mc["flip_prob"] > p["max_flip"]:
        return MomoSignal(False, skip_reason="mc-flip-high", features=feat)

    return MomoSignal(True, side=fav_side, price=fav_price, features=feat)


# ---------------------------------------------------------------------------
# Exit ladder — ports bot.js decide()'s momo branch. `position` is caller-owned
# mutable state (open_position_json in the ledger / an in-memory dict in the
# backtest loop) with at least: side, entry_price. "peak_pct" is written back
# here for the trailing-exit ladder — callers must persist it between calls.
# ---------------------------------------------------------------------------

def momo_exit_check(position: dict[str, Any], session: BuzzerSession, params: dict[str, Any] | None = None, rng=None) -> str | None:
    p = _full(params)
    side = position["side"]
    entry_price = position["entry_price"]
    cur_price = session.up_price if side == "up" else session.down_price
    if cur_price is None:
        return None

    pct = (cur_price / entry_price - 1.0) * 100.0

    if p["tp_px"] > 0 and cur_price >= p["tp_px"]:
        return "take-profit"
    if p["tp_pct"] > 0 and pct >= p["tp_pct"]:
        return "take-profit"

    def live_flip() -> float | None:
        fav_lead = session.move_pct if side == "up" else -session.move_pct
        mc = fr.flip_risk(fav_lead, session.sigma_per_min_pct, session.remaining_secs, n_paths=p["mc_paths"], rng=rng)
        return mc["flip_prob"]

    if p.get("trail_enabled", True):
        prev_peak = position.get("peak_pct")
        peak = pct if prev_peak is None else max(prev_peak, pct)
        position["peak_pct"] = peak
        if peak >= p["trail_trigger_pct"] and (peak - pct) >= p["trail_giveback_pct"]:
            position["peak_pct"] = None
            return "take-profit"
        if pct >= p["trail_trigger_pct"]:
            flip = live_flip()
            if flip is not None and flip >= p["bank_flip_min"]:
                position["peak_pct"] = None
                return "take-profit"

    if p["sl_pct"] > 0 and pct <= -p["sl_pct"]:
        hard_floor = pct <= -p["hard_sl_pct"]
        flip = None if hard_floor else live_flip()
        odds_still_favor_winning = (not hard_floor) and flip is not None and flip <= p["hold_max_flip"]
        if not odds_still_favor_winning:
            return "stop-loss"

    to_expiry_s = session.remaining_secs
    if to_expiry_s <= p["exit_secs"] and pct <= -p["exit_loss_pct"]:
        return "stop-loss"

    return None
