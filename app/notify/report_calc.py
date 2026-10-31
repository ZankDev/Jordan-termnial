"""Pure calculations for the v3 /report PDF: multi-window stats, multi-timeframe technicals, tails, correlations, ratios, reactions.

Everything takes plain lists/dicts and returns plain lists/dicts (None = not computable); no network, no rendering.
Candles are Yahoo rows [ts, open, high, low, close, volume].
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any

Candles = list[list[float]]


# ------------------------------------------------------------------ basics
def day(ts: float) -> dt.date:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


def clean(c: Candles | None) -> Candles:
    return [[r[0], r[1], r[2], r[3], r[4], r[5] or 0] for r in (c or []) if None not in r[:5]]


def sma(c: list[float], n: int) -> float | None:
    return sum(c[-n:]) / n if len(c) >= n else None


def rsi(c: list[float], n: int = 14) -> float | None:
    if len(c) <= n:
        return None
    g = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    l = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    for a, b in zip(g[n:], l[n:]):
        ag, al = (ag * (n - 1) + a) / n, (al * (n - 1) + b) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def rets(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


def stdev(v: list[float]) -> float | None:
    return statistics.stdev(v) if len(v) > 1 else None


def max_dd(closes: list[float]) -> float | None:
    if not closes:
        return None
    peak, dd = closes[0], 0.0
    for x in closes:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    return dd * 100


def dret(c: Candles) -> dict[dt.date, float]:
    return {day(b[0]): b[4] / a[4] - 1 for a, b in zip(c, c[1:]) if a[4]}


# ------------------------------------------------------------------ multi-window price stats
WINDOWS = [("1D", 1), ("1W", 7), ("1M", 30), ("3M", 91), ("6M", 182), ("YTD", -1), ("1Y", 365), ("3Y", 1095), ("5Y", 1830)]


def perf_windows(c: Candles, coin: bool = False) -> list[dict[str, Any]]:
    if len(c) < 3:
        return []
    ann = 365 if coin else 252
    last = c[-1]
    year_start = dt.datetime(day(last[0]).year, 1, 1, tzinfo=dt.timezone.utc).timestamp()
    out = []
    for label, days in WINDOWS:
        start = year_start if days == -1 else last[0] - days * 86400
        idx = next((i for i, r in enumerate(c) if r[0] >= start), None)
        if idx is None:
            continue
        if label == "1D":
            idx = len(c) - 1
        base_i = max(0, idx - 1)
        if label in ("3Y", "5Y") and c[0][0] > start + 25 * 86400:
            continue  # not enough history for that window
        seg = c[idx:]
        base = c[base_i][4]
        if not base or len(seg) < 1:
            continue
        cl = [r[4] for r in seg]
        wr = rets([base] + cl)
        sd = stdev(wr) if len(wr) >= 5 else None
        hi, lo = max(r[2] for r in seg), min(r[3] for r in seg)
        out.append({"label": label, "ret": (cl[-1] / base - 1) * 100, "vol": sd * math.sqrt(ann) * 100 if sd is not None else None,
                    "mdd": max_dd([base] + cl) if len(cl) >= 3 else None, "hi": hi, "lo": lo, "from_hi": (cl[-1] / hi - 1) * 100,
                    "from_lo": (cl[-1] / lo - 1) * 100 if lo else None, "avgvol": sum(r[5] for r in seg) / len(seg), "n": len(seg)})
    return out


# ------------------------------------------------------------------ resampling + multi-timeframe technicals
def resample(c: Candles, kind: str) -> Candles:
    groups: dict[Any, list[list[float]]] = {}
    order: list[Any] = []
    for r in c:
        d = dt.datetime.fromtimestamp(r[0], dt.timezone.utc)
        k = d.isocalendar()[:2] if kind == "W" else (d.year, d.month)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    out = []
    for k in order:
        g = groups[k]
        out.append([g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4], sum(x[5] for x in g)])
    return out


def ema(c: list[float], n: int) -> list[float]:
    if not c:
        return []
    k, o = 2 / (n + 1), [c[0]]
    for x in c[1:]:
        o.append(x * k + o[-1] * (1 - k))
    return o


def adx(c: Candles, n: int = 14) -> tuple[float | None, float | None, float | None]:
    if len(c) < 2 * n + 2:
        return None, None, None
    tr, pdm, mdm = [], [], []
    for i in range(1, len(c)):
        h, l, pc = c[i][2], c[i][3], c[i - 1][4]
        up, dn = h - c[i - 1][2], c[i - 1][3] - l
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
    atr_, p_, m_ = sum(tr[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs: list[float] = []
    pdi = mdi = None
    for i in range(n, len(tr) + 1):
        if i > n:
            atr_ = atr_ - atr_ / n + tr[i - 1]
            p_ = p_ - p_ / n + pdm[i - 1]
            m_ = m_ - m_ / n + mdm[i - 1]
        if atr_ == 0:
            continue
        pdi, mdi = 100 * p_ / atr_, 100 * m_ / atr_
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    if len(dxs) < n:
        return None, pdi, mdi
    a = sum(dxs[:n]) / n
    for x in dxs[n:]:
        a = (a * (n - 1) + x) / n
    return a, pdi, mdi


def _atr(c: Candles, n: int = 14) -> float | None:
    if len(c) <= n:
        return None
    tr = [max(c[i][2] - c[i][3], abs(c[i][2] - c[i - 1][4]), abs(c[i][3] - c[i - 1][4])) for i in range(1, len(c))]
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def _stoch_k(c: Candles, n: int = 14) -> float | None:
    if len(c) < n:
        return None
    w = c[-n:]
    hh, ll = max(x[2] for x in w), min(x[3] for x in w)
    return 100 * (c[-1][4] - ll) / (hh - ll) if hh > ll else 50.0


def tf_tech(c: Candles) -> dict[str, Any]:
    cl = [r[4] for r in c]
    out: dict[str, Any] = {"bars": len(c)}
    if len(cl) < 15:
        return out
    p = cl[-1]
    out["rsi"] = rsi(cl)
    if len(cl) >= 35:
        e12, e26 = ema(cl, 12), ema(cl, 26)
        line = [a - b for a, b in zip(e12, e26)]
        sig = ema(line, 9)
        out["macd_hist"] = line[-1] - sig[-1]
        out["macd_up"] = (line[-1] - sig[-1]) > (line[-2] - sig[-2])
        out["macd_above"] = line[-1] > sig[-1]
    out["stoch"] = _stoch_k(c)
    a, pdi, mdi = adx(c)
    out["adx"], out["pdi"], out["mdi"] = a, pdi, mdi
    at = _atr(c)
    out["atr_pct"] = at / p * 100 if at and p else None
    if len(cl) >= 20:
        w = cl[-20:]
        m = sum(w) / 20
        sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
        out["bb_width"] = 4 * sd / m * 100 if m else None
        out["bb_pctb"] = (p - (m - 2 * sd)) / (4 * sd) * 100 if sd else None
    out["ma"] = {n: (sma(cl, n), (p / sma(cl, n) - 1) * 100) for n in (10, 20, 50, 100, 200) if len(cl) >= n}
    bull = sum(1 for v in out["ma"].values() if v[1] > 0)
    out["ma_bull"], out["ma_n"] = bull, len(out["ma"])
    r = out.get("rsi")
    score = 0
    score += 1 if out["ma_n"] and bull / out["ma_n"] > 0.5 else -1
    score += 1 if out.get("macd_above") else -1
    score += 1 if r is not None and r > 50 else -1
    out["bias"] = "bullish" if score >= 2 else "bearish" if score <= -2 else "mixed"
    return out


# ------------------------------------------------------------------ candles
def candle_pattern(r: list[float], prev: list[float] | None) -> str:
    o, h, l, c = r[1], r[2], r[3], r[4]
    rng = h - l
    if rng <= 0:
        return "flat"
    body = abs(c - o)
    up, lo = h - max(o, c), min(o, c) - l
    bull = c >= o
    if prev:
        po, pc = prev[1], prev[4]
        if bull and pc < po and c >= po and o <= pc and body > abs(pc - po):
            return "bullish engulfing"
        if (not bull) and pc > po and o >= pc and c <= po and body > abs(pc - po):
            return "bearish engulfing"
        if h < prev[2] and l > prev[3]:
            return "inside bar"
    if body / rng < 0.1:
        return "doji"
    if lo >= 0.6 * rng and up <= 0.1 * rng:
        return "hammer"
    if up >= 0.6 * rng and lo <= 0.1 * rng:
        return "shooting star"
    if body / rng > 0.85:
        return "strong " + ("bull" if bull else "bear")
    return "bull candle" if bull else "bear candle"


def candle_rows(c: Candles, n: int = 10) -> list[dict[str, Any]]:
    out = []
    avgv = sum(r[5] for r in c[-31:-1]) / 30 if len(c) > 31 else None
    for i in range(len(c) - n, len(c)):
        if i < 1:
            continue
        r, p = c[i], c[i - 1]
        out.append({"date": day(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "chg": (r[4] / p[4] - 1) * 100 if p[4] else None,
                    "rng": (r[2] - r[3]) / p[4] * 100 if p[4] else None, "vol": r[5], "rvol": r[5] / avgv if avgv else None, "pattern": candle_pattern(r, p)})
    return out


# ------------------------------------------------------------------ daily behaviour stats
def day_stats(c: Candles, n: int = 504) -> dict[str, Any]:
    seg = c[-(n + 1):]
    if len(seg) < 30:
        return {}
    gu = gd = gu_fill = gd_fill = 0
    gaps, ranges, moves = [], [], []
    up_days = dn_days = 0
    ups, dns = [], []
    longest_up = longest_dn = cur = 0
    for a, b in zip(seg, seg[1:]):
        if not a[4]:
            continue
        gap = b[1] / a[4] - 1
        gaps.append(gap)
        if gap >= 0.01:
            gu += 1
            gu_fill += b[3] <= a[4]
        elif gap <= -0.01:
            gd += 1
            gd_fill += b[2] >= a[4]
        ranges.append((b[2] - b[3]) / a[4])
        mv = b[4] / a[4] - 1
        moves.append(mv)
        if mv > 0:
            up_days += 1
            ups.append(mv)
            cur = cur + 1 if cur > 0 else 1
            longest_up = max(longest_up, cur)
        elif mv < 0:
            dn_days += 1
            dns.append(mv)
            cur = cur - 1 if cur < 0 else -1
            longest_dn = max(longest_dn, -cur)
        else:
            cur = 0
    m = len(moves)
    return {"n": m, "gap_up_pct": 100 * gu / m, "gap_dn_pct": 100 * gd / m, "gap_up_fill": 100 * gu_fill / gu if gu else None, "gap_dn_fill": 100 * gd_fill / gd if gd else None,
            "avg_gap": statistics.mean(abs(g) for g in gaps) * 100, "avg_range": statistics.mean(ranges) * 100, "avg_abs_move": statistics.mean(abs(x) for x in moves) * 100,
            "up_pct": 100 * up_days / m, "avg_up": statistics.mean(ups) * 100 if ups else None, "avg_dn": statistics.mean(dns) * 100 if dns else None,
            "days_5": sum(1 for x in moves if abs(x) >= 0.05), "days_10": sum(1 for x in moves if abs(x) >= 0.10),
            "longest_up": longest_up, "longest_dn": longest_dn, "streak": cur}


# ------------------------------------------------------------------ rolling series
def rolling_vol(c: Candles, n: int = 30, coin: bool = False) -> tuple[list[dt.date], list[float]]:
    r = [(day(b[0]), b[4] / a[4] - 1) for a, b in zip(c, c[1:]) if a[4]]
    k = math.sqrt(365 if coin else 252) * 100
    ds, vs = [], []
    for i in range(n - 1, len(r)):
        w = [x for _, x in r[i - n + 1:i + 1]]
        ds.append(r[i][0])
        vs.append(statistics.stdev(w) * k)
    return ds, vs


def _align(a: dict[dt.date, float], b: dict[dt.date, float]) -> list[dt.date]:
    return sorted(set(a) & set(b))


def cov_beta_corr(x: list[float], y: list[float]) -> tuple[float | None, float | None]:
    """(beta of y on x, correlation)."""
    if len(x) < 20 or len(x) != len(y):
        return None, None
    mx, my = sum(x) / len(x), sum(y) / len(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) - 1)
    vx = sum((a - mx) ** 2 for a in x) / (len(x) - 1)
    vy = sum((b - my) ** 2 for b in y) / (len(y) - 1)
    if not vx or not vy:
        return None, None
    return cov / vx, cov / math.sqrt(vx * vy)


def rolling_beta(c: Candles, bench: Candles, n: int = 60) -> tuple[list[dt.date], list[float]]:
    r, b = dret(c), dret(bench)
    days = _align(r, b)
    ds, bs = [], []
    for i in range(n - 1, len(days)):
        w = days[i - n + 1:i + 1]
        beta, _ = cov_beta_corr([b[d] for d in w], [r[d] for d in w])
        if beta is not None:
            ds.append(days[i])
            bs.append(beta)
    return ds, bs


def correlations(target: Candles, others: dict[str, Candles], windows: tuple[int, ...] = (60, 252)) -> list[dict[str, Any]]:
    r = dret(target)
    out = []
    for name, oc in others.items():
        o = dret(oc)
        days = _align(r, o)
        row: dict[str, Any] = {"name": name}
        for w in windows:
            sel = days[-w:]
            beta, corr = cov_beta_corr([o[d] for d in sel], [r[d] for d in sel]) if len(sel) >= 30 else (None, None)
            row[f"beta{w}"], row[f"corr{w}"] = beta, corr
        out.append(row)
    return out


def rel_strength(target: Candles, other: Candles, days: int = 250) -> tuple[list[dt.date], list[float]]:
    a = {day(r[0]): r[4] for r in target}
    b = {day(r[0]): r[4] for r in other}
    ds = _align(a, b)[-days:]
    if len(ds) < 20:
        return [], []
    base = a[ds[0]] / b[ds[0]]
    return ds, [(a[d] / b[d]) / base * 100 for d in ds]


def beta_to_yield(c: Candles, tnx: Candles, days: int = 252) -> tuple[float | None, float | None]:
    """Regression of daily stock return (%) on the daily change in the 10Y yield (percentage points)."""
    r = dret(c)
    y = {day(b[0]): (b[4] - a[4]) for a, b in zip(tnx, tnx[1:])}
    ds = _align(r, y)[-days:]
    if len(ds) < 60:
        return None, None
    beta, corr = cov_beta_corr([y[d] for d in ds], [r[d] * 100 for d in ds])
    return beta, corr


# ------------------------------------------------------------------ risk v2
def drawdown_table(c: Candles, top: int = 5) -> list[dict[str, Any]]:
    eps, peak_i, trough_i, peak = [], 0, 0, c[0][4]
    in_dd = False
    for i, r in enumerate(c):
        if r[4] >= peak:
            if in_dd:
                eps.append({"peak": peak_i, "trough": trough_i, "rec": i})
                in_dd = False
            peak, peak_i, trough_i = r[4], i, i
        else:
            in_dd = True
            if r[4] < c[trough_i][4]:
                trough_i = i
    if in_dd:
        eps.append({"peak": peak_i, "trough": trough_i, "rec": None})
    out = []
    for e in eps:
        pk, tr = c[e["peak"]], c[e["trough"]]
        depth = (tr[4] / pk[4] - 1) * 100
        if depth > -3:
            continue
        end_ts = c[e["rec"]][0] if e["rec"] is not None else c[-1][0]
        out.append({"peak": day(pk[0]), "trough": day(tr[0]), "depth": depth, "down_days": int((tr[0] - pk[0]) / 86400), "recovered": day(c[e["rec"]][0]) if e["rec"] is not None else None,
                    "total_days": int((end_ts - pk[0]) / 86400)})
    out.sort(key=lambda x: x["depth"])
    return out[:top]


def tail_stats(c: Candles, coin: bool = False) -> dict[str, Any]:
    cl = [r[4] for r in c]
    r = rets(cl)
    if len(r) < 60:
        return {}
    m = statistics.mean(r)
    sd = statistics.stdev(r)
    n = len(r)
    skew = sum(((x - m) / sd) ** 3 for x in r) / n if sd else None
    kurt = sum(((x - m) / sd) ** 4 for x in r) / n - 3 if sd else None
    s = sorted(r)
    k99, k95 = max(1, int(n * 0.01)), max(1, int(n * 0.05))
    down = [x for x in r if x < 0]
    ann = 365 if coin else 252
    last1y = cl[-(ann + 1):]
    dd_1y = max_dd(last1y)
    ret_1y = (last1y[-1] / last1y[0] - 1) * 100 if len(last1y) > 10 else None
    peak, sq = last1y[0], 0.0
    for x in last1y:
        peak = max(peak, x)
        sq += ((x / peak - 1) * 100) ** 2
    ulcer = math.sqrt(sq / len(last1y)) if last1y else None
    w20 = [cl[i] / cl[i - 20] - 1 for i in range(20, len(cl))]
    out = {"n": n, "skew": skew, "kurt": kurt, "var99": s[k99 - 1] * 100, "cvar99": statistics.mean(s[:k99]) * 100, "var95": s[k95 - 1] * 100, "cvar95": statistics.mean(s[:k95]) * 100,
           "downside_dev": math.sqrt(sum(x * x for x in down) / n) * math.sqrt(ann) * 100 if down else None, "ulcer": ulcer,
           "calmar": ret_1y / abs(dd_1y) if ret_1y is not None and dd_1y else None}
    if w20:
        out.update({"p_up10": 100 * sum(1 for x in w20 if x >= 0.10) / len(w20), "p_dn10": 100 * sum(1 for x in w20 if x <= -0.10) / len(w20),
                    "p_up20": 100 * sum(1 for x in w20 if x >= 0.20) / len(w20), "p_dn20": 100 * sum(1 for x in w20 if x <= -0.20) / len(w20),
                    "med20": statistics.median(w20) * 100, "w20_n": len(w20)})
    return out


def top_days(c: Candles, k: int = 10) -> tuple[list[tuple[dt.date, float]], list[tuple[dt.date, float]]]:
    rows = [(day(b[0]), (b[4] / a[4] - 1) * 100) for a, b in zip(c, c[1:]) if a[4]]
    rows.sort(key=lambda x: x[1])
    return rows[::-1][:k], rows[:k]


# ------------------------------------------------------------------ volume profile + liquidity
def volume_profile(c: Candles, bins: int = 36) -> dict[str, Any] | None:
    if len(c) < 30:
        return None
    lo, hi = min(r[3] for r in c), max(r[2] for r in c)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    vols = [0.0] * bins
    for r in c:
        a, b = r[3], r[2]
        i0, i1 = min(bins - 1, int((a - lo) / step)), min(bins - 1, int((b - lo) / step))
        share = r[5] / (i1 - i0 + 1)
        for i in range(i0, i1 + 1):
            vols[i] += share
    poc = max(range(bins), key=lambda i: vols[i])
    total, acc, lo_i, hi_i = sum(vols), vols[poc], poc, poc
    while acc < 0.7 * total and (lo_i > 0 or hi_i < bins - 1):
        dn = vols[lo_i - 1] if lo_i > 0 else -1
        upv = vols[hi_i + 1] if hi_i < bins - 1 else -1
        if upv >= dn:
            hi_i += 1
            acc += vols[hi_i]
        else:
            lo_i -= 1
            acc += vols[lo_i]
    return {"lo": lo, "step": step, "vols": vols, "poc": lo + (poc + 0.5) * step, "va_low": lo + lo_i * step, "va_high": lo + (hi_i + 1) * step}


def liquidity(c: Candles, shares_out: float | None, float_sh: float | None) -> dict[str, Any]:
    if len(c) < 25:
        return {}
    v20, v60 = sum(r[5] for r in c[-20:]) / 20, sum(r[5] for r in c[-60:]) / min(60, len(c))
    d20 = sum(r[5] * r[4] for r in c[-20:]) / 20
    d60 = sum(r[5] * r[4] for r in c[-60:]) / min(60, len(c))
    out: dict[str, Any] = {"vol20": v20, "vol60": v60, "dollar20": d20, "dollar60": d60}
    if shares_out and v20:
        out["days_1pct"] = shares_out * 0.01 / (0.2 * v20)
    fl = float_sh or shares_out
    if fl and v20:
        out["turnover_d"] = v20 / fl * 100
        out["turnover_y"] = v20 / fl * 252 * 100
    return out


# ------------------------------------------------------------------ fundamentals from SEC annual/quarterly dicts
def ends(store: dict[str, dict[str, float]], keys: tuple[str, ...] = ("revenue", "netinc", "assets", "ocf"), n: int = 8) -> list[str]:
    return sorted({d for k in keys for d in store.get(k, {})})[-n:]


def _v(store: dict[str, dict[str, float]], k: str, e: str) -> float | None:
    return (store.get(k) or {}).get(e)


def _div(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def ratio_table(ann: dict[str, dict[str, float]], n: int = 6) -> tuple[list[str], list[tuple[str, list[float | None], str]]]:
    """Per fiscal year: margins, DuPont, ROIC, coverage, cost ratios, cash conversion, working capital, leverage. fmt in {pct, x, usd, num}."""
    es = ends(ann, n=n)
    if not es:
        return [], []
    rows: dict[str, list[float | None]] = {k: [] for k in
                                           ("gm", "om", "nm", "at", "em", "roe", "roa", "roic", "cov", "sga", "rnd", "capex", "fcfm", "fcfc", "wc", "nd", "nde", "da", "sbc", "tax", "cr", "de", "ccc")}
    for i, e in enumerate(es):
        g = lambda k: _v(ann, k, e)  # noqa: E731
        rev, ni, ta, eq = g("revenue"), g("netinc"), g("assets"), g("equity")
        prev_e = es[i - 1] if i else None
        pta = _v(ann, "assets", prev_e) if prev_e else None
        peq = _v(ann, "equity", prev_e) if prev_e else None
        avg_ta = (ta + pta) / 2 if ta and pta else ta
        avg_eq = (eq + peq) / 2 if eq and peq else eq
        rows["gm"].append(_pct(_div(g("gross"), rev)))
        rows["om"].append(_pct(_div(g("opinc"), rev)))
        rows["nm"].append(_pct(_div(ni, rev)))
        rows["at"].append(_div(rev, avg_ta))
        rows["em"].append(_div(avg_ta, avg_eq) if avg_eq and avg_eq > 0 else None)
        rows["roe"].append(_pct(_div(ni, avg_eq)) if avg_eq and avg_eq > 0 else None)
        rows["roa"].append(_pct(_div(ni, avg_ta)))
        opinc, tax, pretax = g("opinc"), g("tax"), g("pretax")
        tr = tax / pretax if tax is not None and pretax and pretax > 0 and 0 <= tax / pretax <= 0.4 else 0.21
        debt = (g("debt") or 0)
        ic = debt + (eq or 0) - (g("cash") or 0)
        rows["roic"].append(_pct(_div(opinc * (1 - tr), ic)) if opinc is not None and ic and ic > 0 else None)
        ie = g("intexp")
        rows["cov"].append(_div(opinc, abs(ie)) if ie and opinc is not None else None)
        rows["sga"].append(_pct(_div(g("sga"), rev)))
        rows["rnd"].append(_pct(_div(g("rnd"), rev)))
        rows["capex"].append(_pct(_div(abs(g("capex")) if g("capex") is not None else None, rev)))
        rows["fcfm"].append(_pct(_div(g("fcf"), rev)))
        rows["fcfc"].append(_pct(_div(g("fcf"), ni)) if ni and ni > 0 else None)
        ca, cl = g("curassets"), g("curliab")
        rows["wc"].append(ca - cl if ca is not None and cl is not None else None)
        nd = debt - (g("cash") or 0) if g("debt") is not None or g("cash") is not None else None
        rows["nd"].append(nd)
        ebitda = (opinc + (g("da") or 0)) if opinc is not None else None
        rows["nde"].append(_div(nd, ebitda) if nd is not None and ebitda and ebitda > 0 else None)
        rows["da"].append(g("da"))
        rows["sbc"].append(_pct(_div(g("sbc"), rev)))
        rows["tax"].append(_pct(tax / pretax) if tax is not None and pretax and pretax > 0 else None)
        rows["cr"].append(_div(ca, cl))
        rows["de"].append(_div(g("liab"), eq) if eq and eq > 0 else None)
        ar, inv, ap, cogs = g("ar"), g("inv"), g("ap"), g("cogs")
        if rev and ar is not None:
            dso = ar / rev * 365
            dio = inv / cogs * 365 if inv is not None and cogs else 0
            dpo = ap / cogs * 365 if ap is not None and cogs else 0
            rows["ccc"].append(dso + dio - dpo)
        else:
            rows["ccc"].append(None)
    spec = [("Gross margin", "gm", "pct"), ("Operating margin", "om", "pct"), ("Net margin", "nm", "pct"), ("Asset turnover (x)", "at", "x"), ("Equity multiplier (x)", "em", "x"),
            ("ROE (DuPont: margin x turnover x leverage)", "roe", "pct"), ("ROA", "roa", "pct"), ("ROIC (NOPAT / invested capital)", "roic", "pct"),
            ("Interest coverage (EBIT / interest, x)", "cov", "x"), ("SG&A % of revenue", "sga", "pct"), ("R&D % of revenue", "rnd", "pct"), ("Stock comp % of revenue", "sbc", "pct"),
            ("Capex % of revenue", "capex", "pct"), ("FCF margin", "fcfm", "pct"), ("FCF conversion (FCF / net income)", "fcfc", "pct"), ("Effective tax rate", "tax", "pct"),
            ("Working capital", "wc", "usd"), ("Net debt (debt - cash)", "nd", "usd"), ("Net debt / EBITDA (x)", "nde", "x"), ("Current ratio (x)", "cr", "x"),
            ("Liabilities / equity (x)", "de", "x"), ("Cash conversion cycle (days)", "ccc", "num")]
    return es, [(lab, rows[k], f) for lab, k, f in spec if any(v is not None for v in rows[k])]


def _pct(v: float | None) -> float | None:
    return None if v is None else v * 100


def altman_z(ann: dict[str, dict[str, float]], mcap: float | None) -> dict[str, Any] | None:
    es = ends(ann, n=1)
    if not es or not mcap:
        return None
    e = es[-1]
    g = lambda k: _v(ann, k, e)  # noqa: E731
    ta, tl, rev = g("assets"), g("liab"), g("revenue")
    ca, cl, re_, ebit = g("curassets"), g("curliab"), g("retained"), g("opinc")
    if None in (ta, tl, rev, ca, cl, ebit) or not ta or not tl:
        return None
    wc, re_ = ca - cl, re_ or 0.0
    parts = {"Working capital / assets": 1.2 * wc / ta, "Retained earnings / assets": 1.4 * re_ / ta, "EBIT / assets": 3.3 * ebit / ta,
             "Market cap / liabilities": 0.6 * mcap / tl, "Sales / assets": 1.0 * rev / ta}
    z = sum(parts.values())
    return {"z": z, "zone": "safe (>2.99)" if z > 2.99 else "distress (<1.81)" if z < 1.81 else "grey zone (1.81-2.99)", "parts": parts, "fy": e}


def growth_pair(store: dict[str, dict[str, float]], key: str, e: str, es: list[str]) -> tuple[float | None, float | None]:
    """(YoY, QoQ) % for the quarter ending e; es is the sorted list of quarter ends."""
    v = _v(store, key, e)
    i = es.index(e)
    qoq = _div(v, _v(store, key, es[i - 1])) if i and v is not None else None
    yoy_e = next((x for x in es if 350 <= (_d(e) - _d(x)).days <= 380), None)
    yoy = _div(v, _v(store, key, yoy_e)) if yoy_e and v is not None else None
    sgn = lambda ratio, base: None if ratio is None else (ratio - 1) * 100 if base and base > 0 else None  # noqa: E731
    pq = _v(store, key, es[i - 1]) if i else None
    py = _v(store, key, yoy_e) if yoy_e else None
    return sgn(yoy, py), sgn(qoq, pq)


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def quarter_margins(qtr: dict[str, dict[str, float]], es: list[str]) -> dict[str, list[float | None]]:
    out: dict[str, list[float | None]] = {"gm": [], "om": [], "nm": [], "fcfm": []}
    for e in es:
        rev = _v(qtr, "revenue", e)
        out["gm"].append(_pct(_div(_v(qtr, "gross", e), rev)))
        out["om"].append(_pct(_div(_v(qtr, "opinc", e), rev)))
        out["nm"].append(_pct(_div(_v(qtr, "netinc", e), rev)))
        out["fcfm"].append(_pct(_div(_v(qtr, "fcf", e), rev)))
    return out


def reverse_dcf(price: float | None, fcf_ps: float | None, r: float = 0.10, g_term: float = 0.03, years: int = 10) -> float | None:
    """Growth rate (per year for `years`) that equates the discounted cash flows to today's price. Illustrative only."""
    if not price or not fcf_ps or fcf_ps <= 0:
        return None

    def value(g: float) -> float:
        cf, tot = fcf_ps, 0.0
        for t in range(1, years + 1):
            cf *= 1 + g
            tot += cf / (1 + r) ** t
        return tot + cf * (1 + g_term) / (r - g_term) / (1 + r) ** years
    lo, hi = -0.5, 1.0
    if value(hi) < price:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if value(mid) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2 * 100


# ------------------------------------------------------------------ earnings reactions
def earnings_reactions(c: Candles, dates: list[tuple[dt.date, bool]], n: int = 12) -> dict[str, Any]:
    idx = {day(r[0]): i for i, r in enumerate(c)}
    days = sorted(idx)
    rows = []
    for d, after in dates:
        if len(rows) >= n:
            break
        if after:
            nxt = next((x for x in days if x > d), None)
            react = nxt
        else:
            react = d if d in idx else next((x for x in days if x > d), None)
        if react is None or react not in idx:
            continue
        i = idx[react]
        if i < 1:
            continue
        p, r = c[i - 1], c[i]
        drift = (c[min(i + 5, len(c) - 1)][4] / r[4] - 1) * 100 if i + 5 < len(c) else None
        v20 = sum(x[5] for x in c[max(0, i - 21):i - 1]) / max(1, len(c[max(0, i - 21):i - 1]))
        rows.append({"date": d, "react_day": react, "timing": "after close" if after else "before/during", "move": (r[4] / p[4] - 1) * 100, "gap": (r[1] / p[4] - 1) * 100,
                     "range": (r[2] - r[3]) / p[4] * 100, "drift5": drift, "rvol": r[5] / v20 if v20 else None})
    out: dict[str, Any] = {"rows": rows}
    if rows:
        mv = [x["move"] for x in rows]
        out.update({"avg_abs": statistics.mean(abs(x) for x in mv), "avg": statistics.mean(mv), "median_abs": statistics.median(abs(x) for x in mv), "up_pct": 100 * sum(1 for x in mv if x > 0) / len(mv),
                    "best": max(mv), "worst": min(mv), "n": len(mv)})
    return out


# ------------------------------------------------------------------ peer valuation
VAL_KEYS = [("pe", "P/E (trailing)", "x"), ("fpe", "P/E (forward)", "x"), ("ps", "Price / sales", "x"), ("pb", "Price / book", "x"), ("evrev", "EV / revenue", "x"), ("eveb", "EV / EBITDA", "x"),
            ("gm", "Gross margin", "pct"), ("pm", "Net margin", "pct"), ("rg", "Revenue growth", "pct"), ("roe", "Return on equity", "pct"), ("mcap", "Market cap", "usd")]


def val_row(qs: dict[str, Any] | None) -> dict[str, float | None]:
    if not qs:
        return {}
    sd, ks, fd = qs.get("summaryDetail") or {}, qs.get("defaultKeyStatistics") or {}, qs.get("financialData") or {}
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
    pct = lambda v: None if num(v) is None else v * 100  # noqa: E731
    return {"pe": num(sd.get("trailingPE")), "fpe": num(sd.get("forwardPE")), "ps": num(sd.get("priceToSalesTrailing12Months")), "pb": num(ks.get("priceToBook")),
            "evrev": num(ks.get("enterpriseToRevenue")), "eveb": num(ks.get("enterpriseToEbitda")), "gm": pct(fd.get("grossMargins")), "pm": pct(fd.get("profitMargins")),
            "rg": pct(fd.get("revenueGrowth")), "roe": pct(fd.get("returnOnEquity")), "mcap": num(sd.get("marketCap"))}


def valuation_compare(subject: dict[str, float | None], peers: dict[str, dict[str, float | None]]) -> list[dict[str, Any]]:
    rows = []
    for key, label, fmt in VAL_KEYS:
        vals = [p[key] for p in peers.values() if p.get(key) is not None and (key in ("gm", "pm", "rg", "roe", "mcap") or p[key] > 0)]
        s = subject.get(key)
        if s is None and not vals:
            continue
        med = statistics.median(vals) if vals else None
        rows.append({"key": key, "label": label, "fmt": fmt, "subject": s, "median": med, "min": min(vals) if vals else None, "max": max(vals) if vals else None, "n": len(vals),
                     "pctile": 100 * sum(1 for v in vals if v < s) / len(vals) if s is not None and vals else None,
                     "prem": (s / med - 1) * 100 if s is not None and med and med > 0 and fmt == "x" and s > 0 else None})
    return rows


# ------------------------------------------------------------------ options
def chain_rows(o: dict[str, Any] | None, price: float | None, n: int = 26) -> list[list[Any]]:
    """Strikes nearest the price, calls and puts side by side: strike, C bid/ask/vol/oi/iv, P bid/ask/vol/oi/iv."""
    if not o or not price:
        return []
    calls = {x["strike"]: x for x in o.get("calls") or [] if x.get("strike") is not None}
    puts = {x["strike"]: x for x in o.get("puts") or [] if x.get("strike") is not None}
    ks = sorted(set(calls) | set(puts), key=lambda k: abs(k - price))[:n]
    return [[k, calls.get(k), puts.get(k)] for k in sorted(ks)]


def oi_profile(o: dict[str, Any] | None, price: float | None, span: float = 0.35) -> tuple[list[float], list[float], list[float]]:
    if not o or not price:
        return [], [], []
    calls = {x["strike"]: x.get("oi") or 0 for x in o.get("calls") or [] if x.get("strike") is not None}
    puts = {x["strike"]: x.get("oi") or 0 for x in o.get("puts") or [] if x.get("strike") is not None}
    ks = sorted(k for k in set(calls) | set(puts) if abs(k / price - 1) <= span)
    return ks, [calls.get(k, 0) for k in ks], [puts.get(k, 0) for k in ks]


# ------------------------------------------------------------------ insiders + analysts
BUY_WORDS, SELL_WORDS = ("purchase", "buy", "acquisition (non"), ("sale", "sold", "disposition (non")


def insider_stats(txs: list[dict[str, Any]] | None, now: float | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now().timestamp()
    rows = []
    for t in txs or []:
        ts = t.get("date")
        if not isinstance(ts, (int, float)) or now - ts > 365 * 86400 or not t.get("name"):
            continue
        txt = (t.get("text") or "").lower()
        kind = "buy" if any(w in txt for w in BUY_WORDS) else "sell" if any(w in txt for w in SELL_WORDS) else "other"
        rows.append({**t, "kind": kind, "ts": ts})
    agg: dict[str, Any] = {}
    for k in ("buy", "sell", "other"):
        sel = [r for r in rows if r["kind"] == k]
        agg[k] = {"n": len(sel), "shares": sum(r.get("shares") or 0 for r in sel), "value": sum(r.get("value") or 0 for r in sel), "insiders": len({r["name"] for r in sel})}
    cluster = None
    for k in ("buy", "sell"):
        sel = sorted((r for r in rows if r["kind"] == k), key=lambda r: r["ts"])
        for i, r in enumerate(sel):
            win = {x["name"] for x in sel if 0 <= x["ts"] - r["ts"] <= 30 * 86400}
            if len(win) >= 3 and (not cluster or len(win) > cluster[1]):
                cluster = (k, len(win), dt.datetime.fromtimestamp(r["ts"]).date())
    return {"rows": sorted(rows, key=lambda r: -r["ts"]), "agg": agg, "cluster": cluster}


def analyst_stats(hist: list[dict[str, Any]] | None, price: float | None, now: float | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now().timestamp()
    rows = [h for h in hist or [] if isinstance(h.get("epochGradeDate"), (int, float)) and now - h["epochGradeDate"] <= 365 * 86400]
    rows.sort(key=lambda h: -h["epochGradeDate"])
    tally = {}
    for label, days in (("6m", 183), ("12m", 365)):
        sel = [h for h in rows if now - h["epochGradeDate"] <= days * 86400]
        tally[label] = {"up": sum(1 for h in sel if h.get("action") == "up"), "down": sum(1 for h in sel if h.get("action") == "down"), "init": sum(1 for h in sel if h.get("action") == "init"),
                        "main": sum(1 for h in sel if h.get("action") in ("main", "reit")), "pt_up": sum(1 for h in sel if h.get("priceTargetAction") == "Raises"),
                        "pt_dn": sum(1 for h in sel if h.get("priceTargetAction") == "Lowers"), "n": len(sel)}
    latest: dict[str, float] = {}
    for h in rows:
        if h.get("firm") and h["firm"] not in latest and isinstance(h.get("currentPriceTarget"), (int, float)) and h["currentPriceTarget"] > 0 and now - h["epochGradeDate"] <= 183 * 86400:
            latest[h["firm"]] = h["currentPriceTarget"]
    t = sorted(latest.values())
    disp = None
    if len(t) >= 3:
        disp = {"n": len(t), "min": t[0], "median": statistics.median(t), "max": t[-1], "stdev": statistics.stdev(t), "mean": statistics.mean(t)}
        if price:
            disp["upside_median"] = (disp["median"] / price - 1) * 100
    by_month: dict[str, dict[str, int]] = {}
    for h in rows:
        m = dt.datetime.fromtimestamp(h["epochGradeDate"]).strftime("%Y-%m")
        d = by_month.setdefault(m, {"up": 0, "down": 0, "other": 0})
        d[h.get("action") if h.get("action") in ("up", "down") else "other"] += 1
    return {"rows": rows, "tally": tally, "targets": latest, "dispersion": disp, "by_month": sorted(by_month.items())}


# ------------------------------------------------------------------ crypto
def funding_summary(rows: list[dict[str, Any]] | None, sym: str) -> dict[str, Any] | None:
    base = sym.upper().replace("-USD", "") + "USDT"
    r = next((x for x in rows or [] if x["symbol"] == base), None)
    if not r:
        return None
    return {**r, "tone": "longs paying shorts (crowded long)" if r["funding"] > 0.03 else "shorts paying longs (crowded short)" if r["funding"] < -0.01 else "balanced"}
