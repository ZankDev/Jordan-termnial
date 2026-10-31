"""Reference engine: NinjaTrader's NostroLab rules, bar by bar, in Python.

It mirrors ninjatrader/Strategies/NostroLab.cs exactly (same indicator formulas, same session logic, same order of
events) with NinjaTrader's default historical fill model:
  * signals are evaluated on the bar's close; market entries/exits fill at the NEXT bar's open (+ slippage)
  * stop and target are placed in ticks from the fill price (ATR at signal time); they are checked from the fill bar on,
    filling at the level or at the open when the bar gaps through it (stops get slippage, targets don't)
  * if a bar touches both stop and target, the stop is assumed first (conservative)
  * a trailing stop moves with the bar's best price
Used to cross-check NinjaTrader's results and as the local provider when NinjaTrader is not running.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _m(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def run(spec: dict[str, Any], b: dict, commission_rt: float = 4.5, slippage_ticks: float = 1.0) -> list[dict[str, Any]]:
    o, h, l, c, v, minute, day, times = b["o"], b["h"], b["l"], b["c"], b["v"], b["minute"], b["day"], b["time"]
    tick, pv = b["tick"], b["point_value"]
    fam, dr, p, se, ex = spec["family"], spec["dir"], spec["p"], spec["session"], spec["exit"]
    open_m, ef, et, flat = _m(se["open"]), _m(se["entry_from"]), _m(se["entry_to"]), _m(se["flat"])
    rng_min = int(p.get("range_min", 15)); buf = p.get("buffer_ticks", 0) * tick
    fast, slow, trend_len, ln = p.get("fast", 9), p.get("slow", 21), int(p.get("trend_len", 0) or 0), int(p.get("len", 20))
    lo_t, hi_t, kk, dev = p.get("lo", 30), p.get("hi", 70), p.get("k", 2.0), p.get("dev_atr", 1.5)
    stop_atr, tgt_atr, trail_atr, max_bars = ex["stop_atr"], ex["target_atr"], ex["trail_atr"], int(ex.get("max_bars", 0) or 0)
    atr_len, max_day, qty = int(spec.get("atr_len", 14)), int(spec.get("max_trades_day", 2)), max(1, int(spec.get("qty", 1)))
    slip = slippage_ticks * tick
    bars_required = 20

    closes: list[float] = []
    atr = emaF = emaS = prevEmaF = prevEmaS = rsi = prevRsi = avgG = avgL = 0.0
    prevClose = math.nan
    atrN = rsiN = 0
    cur_day = -1
    trades_today = 0
    orb_hi, orb_lo, orb_started, orb_done = -1e18, 1e18, False, False
    vpv = vv = 0.0
    prevUpper = prevLower = math.nan
    rsi_len = max(2, ln)
    af, asl = 2.0 / (fast + 1), 2.0 / (slow + 1)

    pos = 0            # +1 long, -1 short, 0 flat
    pending = 0        # +1/-1 entry at next open; 2 = exit at next open
    pend_stop_t = pend_tgt_t = pend_trail_t = 0
    entry_px = stop = target = trail = 0.0
    entry_bar = -1
    entry_i = -1
    out: list[dict[str, Any]] = []
    sum_c = 0.0

    def close_trade(i: int, px: float, why: str) -> None:
        nonlocal pos, entry_bar
        out.append({"et": times[entry_i].isoformat()[:19], "xt": times[i].isoformat()[:19], "side": "long" if pos > 0 else "short",
                    "ep": round(entry_px, 4), "xp": round(px, 4), "qty": qty, "pnl": round((px - entry_px) * pos * pv * qty, 2),
                    "comm": round(commission_rt * qty, 2), "exit": why})
        pos = 0
        entry_bar = -1

    n = len(c)
    for i in range(n):
        # 1) orders waiting from the previous bar fill at this bar's open
        if pending == 2 and pos != 0:
            close_trade(i, o[i] - slip * pos, "TimeExit")
            pending = 0
        elif pending in (1, -1) and pos == 0:
            pos = pending
            entry_px = o[i] + slip * pos
            entry_i = i
            stop = entry_px - pos * pend_stop_t * tick if not pend_trail_t else entry_px - pos * pend_trail_t * tick
            trail = pend_trail_t * tick
            target = entry_px + pos * pend_tgt_t * tick if pend_tgt_t else 0.0
            pending = 0
        else:
            pending = 0
        # 2) stop / target during this bar (stop first if both)
        if pos != 0:
            if pos > 0:
                if l[i] <= stop:
                    close_trade(i, min(o[i], stop) - slip, "Stop loss" if not trail else "Trail stop")
                elif target and h[i] >= target:
                    close_trade(i, max(o[i], target), "Profit target")
                elif trail:
                    stop = max(stop, h[i] - trail)
            else:
                if h[i] >= stop:
                    close_trade(i, max(o[i], stop) + slip, "Stop loss" if not trail else "Trail stop")
                elif target and l[i] <= target:
                    close_trade(i, min(o[i], target), "Profit target")
                elif trail:
                    stop = min(stop, l[i] + trail)

        # 3) OnBarUpdate at the close (same order as NostroLab.cs)
        ci, hi_, li, mi = c[i], h[i], l[i], int(minute[i])
        if day[i] != cur_day:
            cur_day = day[i]; trades_today = 0; orb_done = False; orb_started = False; orb_hi, orb_lo = -1e18, 1e18; vpv = vv = 0.0
        closes.append(ci)
        sum_c += ci
        tr = hi_ - li if math.isnan(prevClose) else max(hi_ - li, abs(hi_ - prevClose), abs(li - prevClose))
        atrN += 1
        atr = atr + (tr - atr) / atrN if atrN <= atr_len else (atr * (atr_len - 1) + tr) / atr_len
        emaF = ci if len(closes) == 1 else emaF + af * (ci - emaF)
        emaS = ci if len(closes) == 1 else emaS + asl * (ci - emaS)
        if not math.isnan(prevClose):
            g, lo_ = max(0.0, ci - prevClose), max(0.0, prevClose - ci)
            rsiN += 1
            if rsiN <= rsi_len:
                avgG += (g - avgG) / rsiN; avgL += (lo_ - avgL) / rsiN
            else:
                avgG = (avgG * (rsi_len - 1) + g) / rsi_len; avgL = (avgL * (rsi_len - 1) + lo_) / rsi_len
            rsi = 100.0 if avgL == 0 else 100 - 100 / (1 + avgG / avgL)
        if mi > open_m:
            vpv += (hi_ + li + ci) / 3.0 * v[i]; vv += v[i]
        if open_m < mi <= open_m + rng_min:
            orb_started = True; orb_hi = max(orb_hi, hi_); orb_lo = min(orb_lo, li)
        elif orb_started and mi > open_m + rng_min:
            orb_done = True

        def remember() -> None:
            nonlocal prevClose, prevEmaF, prevEmaS, prevRsi
            prevClose, prevEmaF, prevEmaS, prevRsi = ci, emaF, emaS, rsi

        if pos != 0:
            if entry_bar < 0:
                entry_bar = i
            if mi >= flat or (max_bars > 0 and i - entry_bar >= max_bars):
                pending = 2
            remember()
            continue
        entry_bar = -1
        if i < bars_required or atrN < atr_len or mi < ef or mi > et or mi >= flat or trades_today >= max_day:
            remember()
            continue
        nn = len(closes)
        up_ok = dn_ok = True
        if trend_len > 1 and nn >= trend_len:
            sma = sum(closes[nn - trend_len:nn]) / trend_len
            up_ok, dn_ok = ci > sma, ci < sma
        sig = 0
        if fam == "orb":
            if orb_done:
                up, dn = orb_hi + buf, orb_lo - buf
                if ci > up and not prevClose > up:
                    sig = 1
                elif ci < dn and not prevClose < dn:
                    sig = -1
        elif fam == "ema_cross":
            if nn >= slow + 2:
                if prevEmaF <= prevEmaS and emaF > emaS and up_ok:
                    sig = 1
                elif prevEmaF >= prevEmaS and emaF < emaS and dn_ok:
                    sig = -1
        elif fam == "donchian":
            if nn >= ln + 2:
                hh = max(h[max(0, i - ln):i]); ll = min(l[max(0, i - ln):i])
                if ci > hh and up_ok:
                    sig = 1
                elif ci < ll and dn_ok:
                    sig = -1
        elif fam == "rsi_revert":
            if rsiN >= ln + 1:
                if prevRsi < lo_t and rsi >= lo_t and up_ok:
                    sig = 1
                elif prevRsi > hi_t and rsi <= hi_t and dn_ok:
                    sig = -1
        elif fam == "bb_revert":
            if nn >= ln + 2:
                win = closes[nn - ln:nn]
                m = sum(win) / ln
                sd = math.sqrt(sum((x - m) ** 2 for x in win) / ln)
                upper, lower = m + kk * sd, m - kk * sd
                if not math.isnan(prevLower) and prevClose < prevLower and ci > lower:
                    sig = 1
                elif not math.isnan(prevUpper) and prevClose > prevUpper and ci < upper:
                    sig = -1
                prevUpper, prevLower = upper, lower
        elif fam == "vwap_revert":
            if vv > 0:
                vw = vpv / vv
                if ci < vw - dev * atr and ci > o[i]:
                    sig = 1
                elif ci > vw + dev * atr and ci < o[i]:
                    sig = -1
        if (sig > 0 and dr != "short") or (sig < 0 and dr != "long"):
            pend_stop_t = max(1, round(stop_atr * atr / tick))
            pend_tgt_t = max(1, round(tgt_atr * atr / tick)) if tgt_atr > 0 else 0
            pend_trail_t = max(1, round(trail_atr * atr / tick)) if trail_atr > 0 else 0
            pending = sig
            trades_today += 1
        remember()
    if pos != 0:  # end of data: close at the last close, like Strategy Analyzer
        close_trade(n - 1, c[n - 1], "Exit on session close")
    return out


def to_frame(trades: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(trades)
    if not df.empty:
        df["et"] = pd.to_datetime(df["et"])
        df["xt"] = pd.to_datetime(df["xt"])
    return df
