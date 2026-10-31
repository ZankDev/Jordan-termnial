"""Candidate strategies: the JSON spec shared with NinjaTrader's NostroLab (see ninjatrader/SPEC.md).

The search space is deliberately small and readable: six entry families, a session window, ATR-based exits. A spec's
id is a hash of its content, so the same strategy always has the same id across batches and providers.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from typing import Any

FAMILIES = ["orb", "ema_cross", "donchian", "rsi_revert", "bb_revert", "vwap_revert"]
TREND = [0, 0, 50, 100, 200]


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _m(s: str) -> int:
    h, mm = s.split(":")
    return int(h) * 60 + int(mm)


def spec_id(s: dict[str, Any]) -> str:
    body = {k: v for k, v in s.items() if k not in ("id", "gen", "parent")}
    return "c_" + hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:10]


def finalize(s: dict[str, Any]) -> dict[str, Any]:
    """Fix invariants (slow > fast, windows in order, rounding) and stamp the id."""
    p, se, ex = s["p"], s["session"], s["exit"]
    if s["family"] == "ema_cross" and p["slow"] <= p["fast"] + 3:
        p["slow"] = p["fast"] + 4
    if s["family"] == "rsi_revert":
        p["lo"], p["hi"] = min(p["lo"], 45), max(p["hi"], 55)
    ef, et, fl = _m(se["entry_from"]), _m(se["entry_to"]), _m(se["flat"])
    ef = max(ef, _m(se["open"]) + 5)
    et = max(et, ef + 15)
    fl = max(fl, et + 10)
    se["entry_from"], se["entry_to"], se["flat"] = _hm(ef), _hm(min(et, 15 * 60 + 30)), _hm(min(fl, 15 * 60 + 55))
    for k in ("stop_atr", "target_atr", "trail_atr"):
        ex[k] = round(float(ex[k]), 2)
    for k, v in list(p.items()):
        p[k] = round(float(v), 2) if isinstance(v, float) else v
    s["id"] = spec_id(s)
    return s


def random_spec(rng: random.Random, family: str | None = None) -> dict[str, Any]:
    f = family or rng.choice(FAMILIES)
    if f == "orb":
        p = {"range_min": rng.choice([5, 10, 15, 20, 30, 45, 60]), "buffer_ticks": rng.choice([0, 0, 1, 2, 4, 8])}
    elif f == "ema_cross":
        fast = rng.randint(5, 30)
        p = {"fast": fast, "slow": rng.randint(fast + 4, 120), "trend_len": rng.choice(TREND)}
    elif f == "donchian":
        p = {"len": rng.randint(10, 120), "trend_len": rng.choice(TREND)}
    elif f == "rsi_revert":
        p = {"len": rng.randint(2, 21), "lo": rng.randint(10, 40), "hi": rng.randint(60, 90), "trend_len": rng.choice(TREND)}
    elif f == "bb_revert":
        p = {"len": rng.randint(10, 60), "k": round(rng.uniform(1.5, 3.0), 2)}
    else:
        p = {"dev_atr": round(rng.uniform(0.5, 3.0), 2)}
    ef = rng.choice([575, 580, 585, 600, 615, 630])
    s = {
        "family": f, "dir": rng.choice(["both", "both", "long", "short"]), "p": p,
        "session": {"open": "09:30", "entry_from": _hm(ef), "entry_to": _hm(rng.choice([ef + 30, ef + 60, ef + 120, ef + 240, 900])),
                    "flat": rng.choice(["15:00", "15:30", "15:55"])},
        "exit": {"stop_atr": round(rng.uniform(0.5, 4.0), 2), "target_atr": round(rng.choice([0.0, rng.uniform(0.5, 8.0)]) if rng.random() < 0.15 else rng.uniform(0.5, 8.0), 2),
                 "trail_atr": round(rng.uniform(0.5, 4.0), 2) if rng.random() < 0.3 else 0.0, "max_bars": rng.choice([0, 0, 0, 6, 12, 24, 48])},
        "atr_len": rng.choice([10, 14, 20]), "max_trades_day": rng.choice([1, 1, 2, 2, 3, 4]), "qty": 1,
    }
    return finalize(s)


def _nudge(v: float, rng: random.Random, rel: float, lo: float, hi: float, integer: bool) -> float | int:
    x = v * (1 + rng.uniform(-rel, rel)) + rng.uniform(-1, 1) * (1 if integer else 0.05)
    x = min(hi, max(lo, x))
    return int(round(x)) if integer else round(x, 2)


RANGES = {"range_min": (5, 60, True), "buffer_ticks": (0, 12, True), "fast": (3, 40, True), "slow": (8, 200, True), "len": (2, 150, True),
          "lo": (5, 45, True), "hi": (55, 95, True), "k": (1.0, 3.5, False), "dev_atr": (0.3, 4.0, False)}
EX_RANGES = {"stop_atr": (0.3, 6.0), "target_atr": (0.0, 10.0), "trail_atr": (0.0, 6.0)}


def mutate(s: dict[str, Any], rng: random.Random, strength: float = 0.2) -> dict[str, Any]:
    """1-3 small changes: a parameter, an exit, the session window, the direction or the trade cap."""
    c = copy.deepcopy(s)
    c["parent"] = s["id"]
    for _ in range(rng.choice([1, 1, 2, 3])):
        what = rng.random()
        if what < 0.4 and c["p"]:
            k = rng.choice(list(c["p"]))
            if k == "trend_len":
                c["p"][k] = rng.choice(TREND)
            else:
                lo, hi, integer = RANGES[k]
                c["p"][k] = _nudge(c["p"][k], rng, strength, lo, hi, integer)
        elif what < 0.7:
            k = rng.choice(list(EX_RANGES))
            lo, hi = EX_RANGES[k]
            base = c["exit"][k] or (1.0 if k != "target_atr" else 2.0)
            c["exit"][k] = round(min(hi, max(lo, base * (1 + rng.uniform(-strength * 1.5, strength * 1.5)))), 2) if not (k == "trail_atr" and rng.random() < 0.3) else 0.0
        elif what < 0.85:
            k = rng.choice(["entry_from", "entry_to", "flat"])
            c["session"][k] = _hm(_m(c["session"][k]) + rng.choice([-30, -15, -5, 5, 15, 30]))
        elif what < 0.93:
            c["dir"] = rng.choice(["both", "long", "short"])
        else:
            c["max_trades_day"] = max(1, min(6, c["max_trades_day"] + rng.choice([-1, 1])))
            c["exit"]["max_bars"] = rng.choice([0, 6, 12, 24, 48]) if rng.random() < 0.3 else c["exit"]["max_bars"]
    return finalize(c)


def crossover(a: dict[str, Any], b: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Same family: mix parameters; different families: a's entry with b's exits / session."""
    c = copy.deepcopy(a)
    c["parent"] = f"{a['id']}x{b['id']}"
    if a["family"] == b["family"]:
        for k in c["p"]:
            if rng.random() < 0.5:
                c["p"][k] = b["p"][k]
    for part in ("exit", "session"):
        if rng.random() < 0.5:
            c[part] = copy.deepcopy(b[part])
    if rng.random() < 0.3:
        c["max_trades_day"] = b["max_trades_day"]
    return finalize(c)


def describe(s: dict[str, Any]) -> str:
    p, ex, se = s["p"], s["exit"], s["session"]
    rules = {"orb": f"ORB {p.get('range_min')}m +{p.get('buffer_ticks')}t", "ema_cross": f"EMA {p.get('fast')}/{p.get('slow')}",
             "donchian": f"Donchian {p.get('len')}", "rsi_revert": f"RSI{p.get('len')} {p.get('lo')}/{p.get('hi')}",
             "bb_revert": f"BB {p.get('len')}x{p.get('k')}", "vwap_revert": f"VWAP ±{p.get('dev_atr')} ATR"}[s["family"]]
    if p.get("trend_len"):
        rules += f" · trend SMA{p['trend_len']}"
    ex_t = (f"trail {ex['trail_atr']}" if ex["trail_atr"] else f"stop {ex['stop_atr']}") + (f" / tgt {ex['target_atr']}" if ex["target_atr"] else "") + " ATR"
    if ex.get("max_bars"):
        ex_t += f" · max {ex['max_bars']} bars"
    return f"{rules} · {s['dir']} · {se['entry_from']}-{se['entry_to']} · {ex_t} · ≤{s['max_trades_day']}/day"


def to_ninjatrader_params(s: dict[str, Any]) -> dict[str, Any]:
    """NostroLab's direct-mode properties for this spec (to run it, or optimize around it, natively in NinjaTrader)."""
    hm = lambda x: int(x.replace(":", ""))  # noqa: E731
    p, ex, se = s["p"], s["exit"], s["session"]
    return {"BatchId": "", "Family": s["family"], "Direction": s["dir"], "RangeMin": p.get("range_min", 15), "BufferTicks": p.get("buffer_ticks", 0),
            "Fast": p.get("fast", 9), "Slow": p.get("slow", 21), "TrendLen": p.get("trend_len", 0), "Len": p.get("len", 20), "Lo": p.get("lo", 30),
            "Hi": p.get("hi", 70), "BandK": p.get("k", 2), "DevAtr": p.get("dev_atr", 1.5), "SessionOpen": hm(se["open"]), "EntryFrom": hm(se["entry_from"]),
            "EntryTo": hm(se["entry_to"]), "FlatTime": hm(se["flat"]), "StopAtr": ex["stop_atr"], "TargetAtr": ex["target_atr"], "TrailAtr": ex["trail_atr"],
            "MaxBars": ex["max_bars"], "AtrLen": s["atr_len"], "MaxTradesDay": s["max_trades_day"], "Qty": s["qty"]}
