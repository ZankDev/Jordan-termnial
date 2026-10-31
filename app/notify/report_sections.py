"""Price-based /report sections shared by stocks and coins (performance, technicals, behaviour, risk, relative strength, volume).

Each section is `fn(X)`; X is a Ctx that collects PDF flowables (X.body), automatic observations (X.flags),
dashboard tiles (X.kpis) and raw datasets for the companion spreadsheet (X.sets). A failing section is skipped by the runner.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import statistics
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Image, Paragraph, Spacer  # noqa: E402

from app.notify import report_calc as rc  # noqa: E402
from app.notify import report_data as rd  # noqa: E402
from app.notify.report_ui import (BODY, H2, SMALL, W, _big, _colored, _d, _dtable, _kv, _n, _p, _pct_plain, _png, _price, _s, _style_ax, _sub, _usd)  # noqa: E402

logger = logging.getLogger(__name__)
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class Ctx:
    def __init__(self, sym: str, sec: dict[str, Any], D: dict[str, Any], is_coin: bool, all_c: list[list[float]]) -> None:
        self.sym, self.sec, self.D, self.is_coin = sym, sec, D, is_coin
        self.c = all_c
        self.cl = [r[4] for r in all_c]
        self.yr = 365 if is_coin else 252
        self.c1y = all_c[-self.yr:]
        self.cl1y = [r[4] for r in self.c1y]
        self.price = sec.get("price")
        self.body: list[Any] = []
        self.flags: list[tuple[str, str, str]] = []
        self.kpis: list[tuple[str, str, str | None]] = []
        self.sets: dict[str, tuple[list[str], list[list[Any]]]] = {}
        self.sources: list[tuple[str, str]] = []

    def title(self, t: str) -> None:
        self.body.append(Paragraph(_s(t), H2))

    def add(self, *f: Any) -> None:
        self.body.extend(f)

    def sp(self, h: float = 4) -> None:
        self.body.append(Spacer(1, h))

    def note(self, t: str) -> None:
        self.body.append(_sub(t))

    def text(self, html: str) -> None:
        self.body.append(Paragraph(html, BODY))

    def flag(self, tone: str, text: str, theme: str) -> None:
        self.flags.append((tone, text, theme))

    def kpi(self, label: str, value: str, tone: str | None = None) -> None:
        self.kpis.append((label, value, tone))

    def data(self, name: str, header: list[str], rows: list[list[Any]]) -> None:
        self.sets[name] = (header, rows)

    def source(self, name: str, asof: str) -> None:
        self.sources.append((name, asof))

    def fig(self, fig) -> None:
        w, h = fig.get_size_inches()
        self.body.append(Image(io.BytesIO(_png(fig)), width=W, height=W * h / w))


def _is_na_kv(item: Any) -> bool:
    """A label/value grid whose every value is n/a (e.g. fundamentals for an ETF)."""
    inner = getattr(item, "_content", None)
    tbl = inner[0] if inner and len(inner) == 1 else None
    rows = getattr(tbl, "_cellvalues", None)
    if not rows:
        return False
    vals = [c for r in rows for c in r[1::2] if isinstance(c, Paragraph)]
    if not vals:
        return False
    return all((c.text or "").replace("<b>", "").replace("</b>", "").strip() in ("n/a", "") for c in vals)


def run(X: Ctx, *fns, progress=None) -> None:
    for idx, fn in enumerate(fns):
        if progress:
            try:
                progress(idx, len(fns), getattr(fn, "__name__", "section"))
            except Exception:  # noqa: BLE001
                pass
        n, sets_before = len(X.body), set(X.sets)
        try:
            fn(X)
        except Exception:  # noqa: BLE001
            logger.exception("report section %s failed", getattr(fn, "__name__", fn))
            del X.body[n:]
            continue
        new = [it for it in X.body[n:] if not _is_na_kv(it)]
        if not any(not (isinstance(it, Spacer) or (isinstance(it, Paragraph) and it.style in (H2, SMALL))) for it in new):
            del X.body[n:]
            for k in set(X.sets) - sets_before:
                del X.sets[k]
        else:
            out: list[Any] = []
            for i, it in enumerate(new):
                if isinstance(it, Paragraph) and it.style is H2:
                    nxt = next((x for x in new[i + 1:] if not isinstance(x, Spacer)), None)
                    if nxt is None or (isinstance(nxt, Paragraph) and nxt.style is H2):
                        continue
                out.append(it)
            X.body[n:] = out


def pc(v: float | None, d: int = 1) -> str:
    return _colored(v, _p(v, d)) if v is not None else "n/a"


def num(v: Any, d: int = 2) -> str:
    return _n(v, d) if isinstance(v, (int, float)) else "n/a"


# ------------------------------------------------------------------ performance windows
def performance(X: Ctx) -> None:
    pw = rc.perf_windows(X.c, X.is_coin)
    if not pw:
        return
    X.title("PRICE PERFORMANCE ACROSS WINDOWS")
    rows = [["Window", "Return", "Volatility", "Max drawdown", "High", "Low", "From high", "From low", "Avg volume"]]
    raw = []
    for p in pw:
        rows.append([p["label"], pc(p["ret"]), _pct_plain(p["vol"], 1.0) if p["vol"] is not None else "n/a", _p(p["mdd"]) if p["mdd"] is not None else "n/a", _price(p["hi"]), _price(p["lo"]),
                     _p(p["from_hi"]), _p(p["from_lo"]), _big(p["avgvol"])])
        raw.append([p["label"], p["ret"], p["vol"], p["mdd"], p["hi"], p["lo"], p["from_hi"], p["from_lo"], p["avgvol"], p["n"]])
    X.add(_dtable(rows, [16 * mm, 19 * mm, 21 * mm, 23 * mm, 21 * mm, 21 * mm, 18 * mm, 18 * mm, 17 * mm], esc=False, bold_first=True))
    X.note("Return is close-to-close over calendar windows; volatility is annualised from daily returns inside the window; drawdown is peak-to-trough inside the window.")
    X.data("Performance windows", ["Window", "Return %", "Vol % ann", "Max DD %", "High", "Low", "From high %", "From low %", "Avg volume", "Bars"], raw)
    by = {p["label"]: p for p in pw}
    if "1Y" in by:
        X.kpi("1-year return", _p(by["1Y"]["ret"], 0), "good" if by["1Y"]["ret"] > 0 else "bad")
    if "3M" in by and "1M" in by and by["3M"]["ret"] < -15:
        X.flag("bad", f"Down {abs(by['3M']['ret']):.0f}% over the last 3 months", "Trend")
    if "1Y" in by and by["1Y"]["ret"] > 50:
        X.flag("good", f"Up {by['1Y']['ret']:.0f}% over the last year", "Trend")


# ------------------------------------------------------------------ technicals
def _chart_ta_small(c: list[list[float]], title: str, bars: int = 120) -> bytes:
    import io as _io

    seg = c[-bars:]
    ts = [dt.datetime.fromtimestamp(r[0]) for r in seg]
    cl = [r[4] for r in seg]
    fig, ax = plt.subplots(figsize=(4.2, 2.0), dpi=150)
    ax.plot(ts, cl, color="#14161d", lw=1.1)
    full = [r[4] for r in c]
    for n, cc in ((10, "#22a06b"), (50, "#d9830c"), (200, "#3b82f6")):
        if len(full) >= n:
            sm = [sum(full[i - n + 1:i + 1]) / n for i in range(len(full) - len(seg), len(full))]
            ax.plot(ts, sm, color=cc, lw=0.8, ls="--", label=f"MA{n}")
    ax.set_title(title, fontsize=8, loc="left")
    ax.legend(fontsize=6, frameon=False, ncol=3, loc="upper left")
    _style_ax(ax)
    fig.tight_layout()
    b = _io.BytesIO()
    fig.savefig(b, format="png")
    plt.close(fig)
    return b.getvalue()


def technicals(X: Ctx) -> None:
    from app.notify import report_pdf as pdf  # lazy: report_pdf imports this module

    sym, price, cl, candles = X.sym, X.price, X.cl1y, X.c1y
    X.title("TECHNICALS (DAILY)")
    X.add(Image(io.BytesIO(pdf._chart_ta(X.c, f"{sym} - last 180 sessions: price / Bollinger, volume, RSI(14), MACD(12,26,9)")), width=W, height=W * 6.6 / 8.6))
    X.sp()
    rsi = pdf._rsi(cl)
    emas = {n: rd.ema(cl, n)[-1] for n in (20, 50, 100, 200) if len(cl) >= n}
    mid, up_b, lo_b = rd.boll(cl)
    line, sig, hist_m = rd.macd(cl)
    at = rd.atr(candles)
    sk, sd_ = rd.stoch(candles)
    vol20 = sum((c[5] or 0) for c in candles[-21:-1]) / 20 if len(candles) > 21 else None
    rvol = (candles[-1][5] or 0) / vol20 if vol20 else None
    pctb = (cl[-1] - lo_b[-1]) / (up_b[-1] - lo_b[-1]) * 100 if up_b[-1] and lo_b[-1] and up_b[-1] > lo_b[-1] else None
    tech = [("RSI (14)", _n(rsi, 1) + ("  overbought" if rsi and rsi > 70 else "  oversold" if rsi and rsi < 30 else "")),
            ("Stochastic %K / %D", f"{_n(sk, 0)} / {_n(sd_, 0)}"),
            ("MACD / signal", f"{_n(line[-1], 3 if abs(line[-1]) < 1 else 2)} / {_n(sig[-1], 3 if abs(sig[-1]) < 1 else 2)}"),
            ("MACD histogram", ("rising" if len(hist_m) > 1 and hist_m[-1] > hist_m[-2] else "falling") + f" ({_n(hist_m[-1], 3)})"),
            ("Bollinger %B", _n(pctb, 0) + "%" if pctb is not None else "n/a"),
            ("Bollinger bandwidth", _pct_plain((up_b[-1] - lo_b[-1]) / mid[-1], 100) if up_b[-1] and mid[-1] else "n/a"),
            ("ATR (14)", f"{_n(at, 2 if at >= 1 else 4)} ({_pct_plain(at / price, 100)})" if at and price else "n/a"),
            ("Volume vs 20d avg", f"{rvol:.1f}x" if rvol is not None else "n/a")]
    for n, v in emas.items():
        tech.append((f"EMA {n}", f"{_price(v)} ({_p((price / v - 1) * 100 if price else None)})"))
    X.add(_kv(tech))
    X.sp()
    pv = rd.pivots(candles)
    res, sup = rd.swing_levels(candles, price or cl[-1])
    if pv:
        from app.notify.report_ui import _table

        X.add(_table([["Pivots (classic)", "S2", "S1", "Pivot", "R1", "R2"], [""] + [_price(pv[k]) for k in ("S2", "S1", "P", "R1", "R2")]], [38 * mm] + [27.2 * mm] * 5, True))
        X.sp(3)
    X.text("<font color='#6b7285'>Resistance (swing highs)</font>  <b>" + _s(", ".join(_price(v) for v in res) or "none above") + "</b><br/>"
           "<font color='#6b7285'>Support (swing lows)</font>  <b>" + _s(", ".join(_price(v) for v in sup) or "none below") + "</b>")
    bull, bear, notes = rd.signal_count(cl, candles)
    X.sp()
    X.text(f"Signal tally: <font color='#15803d'><b>{bull} bullish</b></font> / <font color='#b91c1c'><b>{bear} bearish</b></font>. " + _s(pdf._trend_text(cl)))
    X.note("  |  ".join(notes))
    if rsi is not None:
        X.kpi("RSI (14)", f"{rsi:.0f}", "bad" if rsi > 70 else "good" if rsi < 30 else None)
        if rsi > 70 or rsi < 30:
            X.flag("bad" if rsi > 70 else "good", f"RSI {rsi:.0f}: {'overbought' if rsi > 70 else 'oversold'}", "Trend")
    s200 = pdf._sma(cl, 200)
    if s200 and price:
        X.flag("good" if price > s200 else "bad", f"Price {abs((price / s200 - 1) * 100):.0f}% {'above' if price > s200 else 'below'} the 200-day average", "Trend")
    if rvol and rvol >= 2:
        X.flag("info", f"Volume {rvol:.1f}x its 20-day average", "Trend")
    X.kpi("Signal tally", f"{bull} bull / {bear} bear", "good" if bull > bear + 1 else "bad" if bear > bull + 1 else None)
    X.data("Technicals daily", ["Metric", "Value"], [[k, v] for k, v in tech])


def multi_tf(X: Ctx) -> None:
    tfs = [("Daily", X.c), ("Weekly", rc.resample(X.c, "W")), ("Monthly", rc.resample(X.c, "M"))]
    T = [(n, rc.tf_tech(c)) for n, c in tfs]
    if not any(t.get("rsi") is not None for _, t in T):
        return
    X.title("MULTI-TIMEFRAME TECHNICALS")
    price = X.cl[-1]

    def col(f):
        return [f(t) if t.get("bars", 0) >= 15 else "n/a" for _, t in T]
    rows = [["Indicator"] + [n for n, _ in T],
            ["Bars available"] + [str(t.get("bars")) for _, t in T],
            ["RSI (14)"] + col(lambda t: num(t.get("rsi"), 1)),
            ["MACD vs signal"] + col(lambda t: "n/a" if "macd_above" not in t else ("above" if t["macd_above"] else "below") + (", rising" if t["macd_up"] else ", falling")),
            ["Stochastic %K (14)"] + col(lambda t: num(t.get("stoch"), 0)),
            ["ADX (trend strength)"] + col(lambda t: num(t.get("adx"), 0) + (" (strong)" if (t.get("adx") or 0) > 25 else " (weak)" if t.get("adx") is not None else "")),
            ["+DI / -DI"] + col(lambda t: f"{num(t.get('pdi'), 0)} / {num(t.get('mdi'), 0)}"),
            ["Bollinger width"] + col(lambda t: _pct_plain(t.get("bb_width"), 1.0) if t.get("bb_width") is not None else "n/a"),
            ["Bollinger %B"] + col(lambda t: _pct_plain(t.get("bb_pctb"), 1.0) if t.get("bb_pctb") is not None else "n/a"),
            ["ATR % of price"] + col(lambda t: _pct_plain(t.get("atr_pct"), 1.0) if t.get("atr_pct") is not None else "n/a")]
    for n in (10, 20, 50, 100, 200):
        rows.append([f"vs MA {n}"] + [("n/a" if n not in t.get("ma", {}) else f"{_price(t['ma'][n][0])} ({_p(t['ma'][n][1])})") for _, t in T])
    rows.append(["MAs price is above"] + [f"{t.get('ma_bull', 0)} of {t.get('ma_n', 0)}" if t.get("ma_n") else "n/a" for _, t in T])
    rows.append(["Bias (MA / MACD / RSI vote)"] + [t.get("bias", "n/a") for _, t in T])
    X.add(_dtable(rows, [48 * mm, 42 * mm, 42 * mm, 42 * mm], esc=True, bold_first=True))
    X.note("Weekly and monthly bars are resampled from daily data. Bias is a simple vote (price vs MAs, MACD vs signal, RSI vs 50), not a forecast.")
    X.data("Multi-timeframe technicals", [r[0] for r in rows[:1]] + [n for n, _ in T], rows[1:])
    b = [t.get("bias") for _, t in T]
    if b.count("bullish") >= 2:
        X.flag("good", f"Multi-timeframe bias bullish ({b[0]}/{b[1]}/{b[2]} daily/weekly/monthly)", "Trend")
    elif b.count("bearish") >= 2:
        X.flag("bad", f"Multi-timeframe bias bearish ({b[0]}/{b[1]}/{b[2]} daily/weekly/monthly)", "Trend")
    # weekly / monthly mini charts
    wk, mo = tfs[1][1], tfs[2][1]
    if len(wk) >= 60 and len(mo) >= 24:
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 2.3), dpi=150)
        for ax, c, t, n in ((a1, wk, "Weekly close with MA10 / MA50 / MA200", 104), (a2, mo, "Monthly close with MA10 / MA50", 60)):
            seg = c[-n:]
            ts = [dt.datetime.fromtimestamp(r[0]) for r in seg]
            full = [r[4] for r in c]
            ax.plot(ts, [r[4] for r in seg], color="#14161d", lw=1.2)
            for m_, cc in ((10, "#22a06b"), (50, "#d9830c"), (200, "#3b82f6")):
                if len(full) >= m_:
                    sm = [sum(full[i - m_ + 1:i + 1]) / m_ if i + 1 >= m_ else float("nan") for i in range(len(full) - len(seg), len(full))]
                    ax.plot(ts, sm, color=cc, lw=0.8, ls="--", label=f"MA{m_}")
            ax.set_title(t, fontsize=8, loc="left")
            ax.legend(fontsize=6, frameon=False, ncol=3, loc="upper left")
        _style_ax(a1, a2)
        X.fig(fig)
    del price


def candles(X: Ctx) -> None:
    rows = rc.candle_rows(X.c, 10)
    if not rows:
        return
    X.title("LAST 10 SESSIONS AND CANDLESTICK READ")
    t = [["Date", "Open", "High", "Low", "Close", "Change", "Range", "Volume", "vs 30d avg", "Pattern"]]
    for r in rows[::-1]:
        t.append([r["date"].strftime("%d %b"), _price(r["o"]), _price(r["h"]), _price(r["l"]), _price(r["c"]), pc(r["chg"], 2), _pct_plain(r["rng"], 1.0) if r["rng"] is not None else "n/a",
                  _big(r["vol"]), f"{r['rvol']:.1f}x" if r["rvol"] is not None else "n/a", _s(r["pattern"])])
    X.add(_dtable(t, [15 * mm, 17 * mm, 17 * mm, 17 * mm, 17 * mm, 16 * mm, 14 * mm, 18 * mm, 16 * mm, 27 * mm], esc=False))
    pat: dict[str, int] = {}
    for r in rows:
        pat[r["pattern"]] = pat.get(r["pattern"], 0) + 1
    ups = sum(1 for r in rows if (r["chg"] or 0) > 0)
    X.note(f"{ups} up / {len(rows) - ups} down sessions out of the last {len(rows)}. Patterns: " + ", ".join(f"{k} x{v}" for k, v in sorted(pat.items(), key=lambda kv: -kv[1])) + ".")
    X.data("Last 10 sessions", ["Date", "Open", "High", "Low", "Close", "Change %", "Range %", "Volume", "Rel vol", "Pattern"],
           [[r["date"].isoformat(), r["o"], r["h"], r["l"], r["c"], r["chg"], r["rng"], r["vol"], r["rvol"], r["pattern"]] for r in rows])


# ------------------------------------------------------------------ price behaviour (gaps, streaks, seasonality, biggest days)
def _own_seasonality(c: list[list[float]]) -> dict[str, list[dict[str, Any]]]:
    m = rc.resample(c, "M")
    by_m: dict[int, list[float]] = {}
    for a, b in zip(m, m[1:]):
        by_m.setdefault(dt.datetime.fromtimestamp(b[0], dt.timezone.utc).month, []).append((b[4] / a[4] - 1) * 100)
    by_w: dict[int, list[float]] = {}
    for a, b in zip(c, c[1:]):
        by_w.setdefault(dt.datetime.fromtimestamp(b[0], dt.timezone.utc).weekday(), []).append((b[4] / a[4] - 1) * 100)

    def agg(d: dict[int, list[float]], keys) -> list[dict[str, Any]]:
        out = []
        for k in keys:
            v = d.get(k) or []
            out.append({"k": k, "avg": statistics.mean(v) if v else None, "median": statistics.median(v) if v else None, "hit": 100 * sum(1 for x in v if x > 0) / len(v) if v else None,
                        "n": len(v), "best": max(v) if v else None, "worst": min(v) if v else None})
        return out
    return {"months": agg(by_m, range(1, 13)), "weekdays": agg(by_w, range(0, 7))}


def behaviour(X: Ctx) -> None:
    ds = rc.day_stats(X.c, 504)
    if not ds:
        return
    X.title("PRICE BEHAVIOUR: GAPS, STREAKS, RANGE (2 YEARS)")
    kv = [("Average daily range", _pct_plain(ds["avg_range"], 1.0)), ("Average absolute daily move", _pct_plain(ds["avg_abs_move"], 1.0)),
          ("Up days", _pct_plain(ds["up_pct"], 1.0)), ("Average up day / down day", f"{_p(ds['avg_up'], 2)} / {_p(ds['avg_dn'], 2)}"),
          ("Days moving 5% or more", f"{ds['days_5']} ({_pct_plain(ds['days_5'] / ds['n'] * 100, 1.0)})"), ("Days moving 10% or more", f"{ds['days_10']} ({_pct_plain(ds['days_10'] / ds['n'] * 100, 1.0)})"),
          ("Longest up / down streak", f"{ds['longest_up']} / {ds['longest_dn']} days"), ("Current streak", f"{abs(ds['streak'])} {'up' if ds['streak'] > 0 else 'down' if ds['streak'] < 0 else 'flat'} day(s)")]
    if not X.is_coin:
        kv[2:2] = [("Gap up 1%+ (frequency)", _pct_plain(ds["gap_up_pct"], 1.0) + (f", filled same day {_pct_plain(ds['gap_up_fill'], 1.0)}" if ds["gap_up_fill"] is not None else "")),
                   ("Gap down 1%+ (frequency)", _pct_plain(ds["gap_dn_pct"], 1.0) + (f", filled same day {_pct_plain(ds['gap_dn_fill'], 1.0)}" if ds["gap_dn_fill"] is not None else "")),
                   ("Average overnight gap", _pct_plain(ds["avg_gap"], 1.0))]
    X.add(_kv(kv))
    X.data("Price behaviour 2y", ["Metric", "Value"], [[k, v] for k, v in kv])
    own = _own_seasonality(X.c)
    api = X.D.get("seas") or {}
    months = []
    for i, o in enumerate(own["months"]):
        a = next((x for x in api.get("months") or [] if x.get("m") == i + 1 and x.get("n")), None)
        months.append({**o, **({k: a[k] for k in ("avg", "median", "hit", "n", "best", "worst")} if a else {}), "src": "long" if a else "5y"})
    wdays = []
    for i, o in enumerate(own["weekdays"]):
        a = next((x for x in api.get("weekdays") or [] if x.get("d") in (i, WEEKDAYS[i]) and x.get("n")), None) if isinstance(api.get("weekdays"), list) else None
        wdays.append({**o, **({k: a[k] for k in ("avg", "median", "hit", "n", "best", "worst") if k in a} if a else {})})
    cur_m = dt.datetime.now().month
    X.sp(6)
    X.title("SEASONALITY: CALENDAR MONTH AND WEEKDAY")
    rows = [["Month", "Avg return", "Median", "Hit rate", "Best", "Worst", "Samples"]]
    for i, m in enumerate(months):
        if m["avg"] is None:
            continue
        rows.append([("> " if i + 1 == cur_m else "") + MONTH_NAMES[i], pc(m["avg"]), _p(m["median"]), _pct_plain(m["hit"], 1.0), _p(m["best"], 0), _p(m["worst"], 0), str(m["n"])])
    X.add(_dtable(rows, [26 * mm, 26 * mm, 24 * mm, 24 * mm, 24 * mm, 26 * mm, 24 * mm], esc=False, bold_first=True))
    wrows = [["Weekday", "Avg return", "Median", "Hit rate", "Best", "Worst", "Samples"]]
    for i, m in enumerate(wdays):
        if m["avg"] is None or (X.is_coin is False and i > 4):
            continue
        wrows.append([WEEKDAYS[i], pc(m["avg"], 2), _p(m["median"], 2), _pct_plain(m["hit"], 1.0), _p(m["best"], 1), _p(m["worst"], 1), str(m["n"])])
    X.sp(5)
    X.add(_dtable(wrows, [26 * mm, 26 * mm, 24 * mm, 24 * mm, 24 * mm, 26 * mm, 24 * mm], esc=False, bold_first=True))
    X.note("Month rows use the long-history seasonality feed where it has data, otherwise the last 5 years (few samples: treat as descriptive, not predictive). '>' marks the current month.")
    X.data("Seasonality months", ["Month", "Avg %", "Median %", "Hit %", "Best %", "Worst %", "Samples"], [[MONTH_NAMES[i], m["avg"], m["median"], m["hit"], m["best"], m["worst"], m["n"]] for i, m in enumerate(months)])
    X.data("Seasonality weekdays", ["Weekday", "Avg %", "Median %", "Hit %", "Best %", "Worst %", "Samples"], [[WEEKDAYS[i], m["avg"], m["median"], m["hit"], m["best"], m["worst"], m["n"]] for i, m in enumerate(wdays)])
    cm = months[cur_m - 1]
    if cm["avg"] is not None and cm["n"] >= 4 and cm["hit"] is not None and (cm["hit"] >= 70 or cm["hit"] <= 30):
        X.flag("info", f"{MONTH_NAMES[cur_m - 1]} has been {'positive' if cm['hit'] >= 70 else 'negative'} in {cm['hit']:.0f}% of {cm['n']} years (avg {cm['avg']:+.1f}%)", "Trend")
    up, dn = rc.top_days(X.c, 10)
    X.sp(6)
    X.title("THE 10 BIGGEST UP AND DOWN DAYS (ALL AVAILABLE HISTORY)")
    rows = [["#", "Best day", "Move", "Worst day", "Move"]] + [[str(i + 1), u[0].strftime("%d %b %Y"), pc(u[1], 1), d[0].strftime("%d %b %Y"), pc(d[1], 1)] for i, (u, d) in enumerate(zip(up, dn))]
    X.add(_dtable(rows, [10 * mm, 50 * mm, 27 * mm, 50 * mm, 27 * mm], right=2, esc=False))
    X.data("Biggest days", ["Rank", "Best date", "Best %", "Worst date", "Worst %"], [[i + 1, u[0].isoformat(), u[1], d[0].isoformat(), d[1]] for i, (u, d) in enumerate(zip(up, dn))])


# ------------------------------------------------------------------ risk v2
def risk(X: Ctx) -> None:
    from app.notify import report_pdf as pdf

    cl, c = X.cl1y, X.c
    bench_c = [r for r in ((X.D.get("bench5") or {}).get("candles") or []) if r[4] is not None]
    bench_name = "BTC" if X.is_coin else "SPY"
    rs = rd.risk_stats(X.c1y, None if X.sym in ("SPY", "BTC-USD") else bench_c, X.yr)
    X.title("RISK AND PERFORMANCE (1 YEAR)")
    X.add(Image(io.BytesIO(pdf._chart_risk(c[-504:])), width=W, height=W * 2.6 / 8.6))
    X.sp()
    dd1 = rc.max_dd(cl)
    if rs:
        kv = [("Annualised return", _p(rs["ann_ret"])), ("Annualised volatility", _pct_plain(rs["ann_vol"], 1.0)), ("Sharpe (rf 0%)", _n(rs.get("sharpe"))), ("Sortino (rf 0%)", _n(rs.get("sortino"))),
              (f"Beta vs {bench_name}", _n(rs.get("beta"))), (f"Correlation vs {bench_name}", _n(rs.get("corr"))),
              (f"Alpha vs {bench_name} (ann.)", _p(rs.get("alpha"), 1) if rs.get("alpha") is not None else "n/a"), ("Max drawdown (1y)", _p(dd1, 1)),
              ("1-day VaR 95% (hist.)", _p(rs["var95"], 1)), ("Best / worst day", f"{_p(rs['best'], 1)} / {_p(rs['worst'], 1)}"), ("Positive days", _pct_plain(rs["pos_days"], 1.0)),
              ("From 1y high", _p((cl[-1] / max(cl) - 1) * 100))]
        X.add(_kv(kv))
        X.kpi("Volatility (1y)", _pct_plain(rs["ann_vol"], 1.0))
        X.kpi(f"Beta vs {bench_name}", _n(rs.get("beta")))
        X.kpi("Sharpe (1y)", _n(rs.get("sharpe")), "good" if (rs.get("sharpe") or 0) > 1 else "bad" if (rs.get("sharpe") or 0) < 0 else None)
        if rs.get("beta") and rs["beta"] > 1.5:
            X.flag("info", f"High beta {rs['beta']:.1f} vs {bench_name}: moves {rs['beta']:.1f}x the market", "Risk")
        if dd1 is not None and dd1 < -50:
            X.flag("bad", f"Max drawdown {dd1:.0f}% in the last year", "Risk")
    yr = rd.yearly_returns(c)
    if yr:
        X.sp(5)
        X.add(_dtable([[k for k, _ in yr], [_colored(v, _p(v, 1)) for _, v in yr]], [W / len(yr)] * len(yr), right=0, esc=False))
    ts = rc.tail_stats(c, X.is_coin)
    if ts:
        X.sp(6)
        X.title("TAIL RISK AND DISTRIBUTION (ALL AVAILABLE HISTORY)")
        ds, vs = rc.rolling_vol(c[-(X.yr * 2 + 40):], 30, X.is_coin)
        rets_ = [(b[4] / a[4] - 1) * 100 for a, b in zip(c, c[1:]) if a[4]]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 2.6), dpi=150, gridspec_kw={"width_ratios": [1.15, 1]})
        if vs:
            a1.plot(ds, vs, color="#7c3aed", lw=1.0)
            a1.axhline(statistics.mean(vs), color="#6b7285", lw=0.7, ls=":")
            a1.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
            a1.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
        a1.set_title("Rolling 30-day volatility (annualised %, dotted: average)", fontsize=8.5, loc="left")
        lim = max(abs(ts["var99"]) * 1.6, 5)
        a2.hist([x for x in rets_ if -lim <= x <= lim], bins=60, color="#9aa3b5")
        a2.axvline(ts["var99"], color="#b91c1c", lw=1.2)
        a2.axvline(ts["var95"], color="#d9830c", lw=1.2)
        a2.set_title("Daily return distribution (orange VaR95, red VaR99)", fontsize=8.5, loc="left")
        _style_ax(a1, a2)
        X.fig(fig)
        X.sp(4)
        kv = [("Skewness", _n(ts["skew"])), ("Excess kurtosis (fat tails)", _n(ts["kurt"])), ("VaR 95% / CVaR 95% (1 day)", f"{_p(ts['var95'])} / {_p(ts['cvar95'])}"),
              ("VaR 99% / CVaR 99% (1 day)", f"{_p(ts['var99'])} / {_p(ts['cvar99'])}"), ("Downside deviation (ann.)", _pct_plain(ts["downside_dev"], 1.0) if ts["downside_dev"] is not None else "n/a"),
              ("Ulcer index (1y)", _n(ts["ulcer"], 1)), ("Calmar ratio (1y)", _n(ts["calmar"]))]
        if "p_up10" in ts:
            kv += [("20-day move of +10% or more", _pct_plain(ts["p_up10"], 1.0)), ("20-day move of -10% or worse", _pct_plain(ts["p_dn10"], 1.0)),
                   ("20-day move of +20% or more", _pct_plain(ts["p_up20"], 1.0)), ("20-day move of -20% or worse", _pct_plain(ts["p_dn20"], 1.0)), ("Median 20-day return", _p(ts["med20"]))]
        X.add(_kv(kv))
        X.note("Probabilities are the empirical share of rolling 20-day windows in the available history, not forecasts.")
        X.data("Tail risk", ["Metric", "Value"], [[k, v] for k, v in kv])
        if ts["kurt"] is not None and ts["kurt"] > 6:
            X.flag("info", f"Fat-tailed returns (excess kurtosis {ts['kurt']:.1f}); 99% one-day VaR {ts['var99']:.1f}%", "Risk")
    dt_ = rc.drawdown_table(c, 6)
    if dt_:
        X.sp(6)
        rows = [["Peak date", "Trough date", "Depth", "Days down", "Recovered", "Total days"]] + [[e["peak"].strftime("%d %b %Y"), e["trough"].strftime("%d %b %Y"), pc(e["depth"], 1), str(e["down_days"]),
                                                                                                e["recovered"].strftime("%d %b %Y") if e["recovered"] else "not yet", str(e["total_days"])] for e in dt_]
        X.add(Paragraph("Deepest drawdowns (peak to trough)", BODY))
        X.sp(2)
        X.add(_dtable(rows, [30 * mm, 30 * mm, 24 * mm, 26 * mm, 34 * mm, 30 * mm], right=2, esc=False))
        X.data("Drawdowns", ["Peak", "Trough", "Depth %", "Days down", "Recovered", "Total days"], [[e["peak"].isoformat(), e["trough"].isoformat(), e["depth"], e["down_days"], e["recovered"].isoformat() if e["recovered"] else "", e["total_days"]] for e in dt_])


# ------------------------------------------------------------------ correlation / relative strength
def relative(X: Ctx) -> None:
    D, c = X.D, X.c
    hist = lambda k: rc.clean(((D.get(k) or {}).get("candles")))  # noqa: E731
    others: dict[str, list[list[float]]] = {}
    if X.is_coin:
        if X.sym != "BTC-USD":
            others["BTC"] = rc.clean(((D.get("bench5") or {}).get("candles")))
        others.update({"ETH": hist("h_eth"), "S&P 500 (SPY)": hist("h_spy"), "Gold (GC=F)": hist("h_gold")})
        windows = (90, 365)
    else:
        if X.sym != "SPY":
            others["S&P 500 (SPY)"] = rc.clean(((D.get("bench5") or {}).get("candles")))
        if X.sym != "QQQ":
            others["Nasdaq 100 (QQQ)"] = hist("h_qqq")
        if D.get("_etf") and X.sym != D["_etf"]:
            others[f"Sector ETF ({D['_etf']})"] = hist("h_sector")
        px = D.get("peer_extra") or {}
        for s, h in (px.get("hist") or {}).items():
            cc = rc.clean((h or {}).get("candles"))
            if cc:
                others[s] = cc
        windows = (60, 252)
    others = {k: v for k, v in others.items() if len(v) > 100}
    if not others:
        return
    X.title("CORRELATION, BETA AND RELATIVE STRENGTH")
    rows_c = rc.correlations(c, others, windows)
    rows = [["Versus", f"Beta {windows[0]}d", f"Corr {windows[0]}d", f"Beta {windows[1]}d", f"Corr {windows[1]}d"]]
    for r in rows_c:
        rows.append([r["name"], num(r[f"beta{windows[0]}"]), num(r[f"corr{windows[0]}"]), num(r[f"beta{windows[1]}"]), num(r[f"corr{windows[1]}"])])
    X.add(_dtable(rows, [58 * mm, 29 * mm, 29 * mm, 29 * mm, 29 * mm], bold_first=True))
    X.note("Beta = sensitivity of this security's daily returns to the other series; correlation near 1 means they move together, near 0 independent, negative opposite.")
    X.data("Correlation", ["Versus"] + [f"Beta {w}d" if i == 0 else f"Corr {w}d" for w in windows for i in (0, 1)],
           [[r["name"], r[f"beta{windows[0]}"], r[f"corr{windows[0]}"], r[f"beta{windows[1]}"], r[f"corr{windows[1]}"]] for r in rows_c])
    main_name, main = next(iter(others.items()))
    ds_rs, rs_ = rc.rel_strength(c, main, 250)
    bd, bb = rc.rolling_beta(c, main, 60)
    if rs_ or bb:
        X.sp(4)
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 2.5), dpi=150)
        if rs_:
            a1.plot(ds_rs, rs_, color="#14161d", lw=1.2, label=f"vs {main_name.split(' (')[0]}")
            for nm, oc in list(others.items())[1:3]:
                d2, r2 = rc.rel_strength(c, oc, 250)
                if r2:
                    a1.plot(d2, r2, lw=0.9, label=f"vs {nm.split(' (')[0]}")
            a1.axhline(100, color="#6b7285", lw=0.6, ls=":")
            a1.legend(fontsize=6.5, frameon=False, loc="upper left")
            a1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            a1.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
        a1.set_title("Relative strength, last 250 days (rebased to 100)", fontsize=8.5, loc="left")
        if bb:
            a2.plot(bd[-500:], bb[-500:], color="#7c3aed", lw=1.1)
            a2.axhline(1, color="#6b7285", lw=0.6, ls=":")
            a2.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
            a2.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
        a2.set_title(f"Rolling 60-day beta vs {main_name.split(' (')[0]}", fontsize=8.5, loc="left")
        _style_ax(a1, a2)
        X.fig(fig)
        if rs_:
            ch = rs_[-1] - 100
            X.flag("good" if ch > 10 else "bad" if ch < -10 else "info", f"Relative strength vs {main_name.split(' (')[0]}: {ch:+.0f}% over 250 days", "Trend")
    if not X.is_coin:
        b, cr = rc.beta_to_yield(c, hist("h_tnx"))
        if b is not None:
            X.sp(4)
            X.add(_kv([("Daily return (%) per +1.00pt move in 10Y yield", f"{b:+.2f}%"), ("Correlation with 10Y yield changes (1y)", _n(cr))]))
            X.note("Negative means the stock has tended to fall when yields rise (rate-sensitive); near zero means little rate sensitivity.")


# ------------------------------------------------------------------ volume profile + liquidity
def volume(X: Ctx) -> None:
    c1 = X.c1y
    vp = rc.volume_profile(c1, 36)
    if not vp:
        return
    X.title("VOLUME PROFILE AND LIQUIDITY (1 YEAR)")
    price = X.price or X.cl[-1]
    n = len(vp["vols"])
    ys = [vp["lo"] + (i + 0.5) * vp["step"] for i in range(n)]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 3.0), dpi=150, gridspec_kw={"width_ratios": [1, 1.6]}, sharey=True)
    cols = ["#d9830c" if vp["va_low"] <= y <= vp["va_high"] else "#c9cedb" for y in ys]
    a1.barh(ys, vp["vols"], height=vp["step"] * 0.92, color=cols)
    a1.axhline(vp["poc"], color="#b91c1c", lw=1.0)
    a1.axhline(price, color="#14161d", lw=1.0, ls="--")
    a1.set_title("Volume by price (orange: value area)", fontsize=8.5, loc="left")
    a1.set_xticks([])
    ts = [dt.datetime.fromtimestamp(r[0]) for r in c1]
    a2.plot(ts, [r[4] for r in c1], color="#14161d", lw=1.1)
    a2.axhspan(vp["va_low"], vp["va_high"], color="#d9830c", alpha=0.12)
    a2.axhline(vp["poc"], color="#b91c1c", lw=0.9)
    a2.set_title("Price with value area", fontsize=8.5, loc="left")
    _style_ax(a1, a2)
    X.fig(fig)
    sh = (X.sec.get("stats") or {}).get("shares_out")
    flt = (X.sec.get("stats") or {}).get("float") or ((X.D.get("keystats") or {}).get("defaultKeyStatistics") or {}).get("floatShares")
    lq = rc.liquidity(X.c, None if X.is_coin else sh, None if X.is_coin else flt)
    kv = [("Point of control (POC)", f"{_price(vp['poc'])} ({_p((price / vp['poc'] - 1) * 100)} from price)"), ("Value area (70% of volume)", f"{_price(vp['va_low'])} - {_price(vp['va_high'])}"),
          ("Price vs value area", "above" if price > vp["va_high"] else "below" if price < vp["va_low"] else "inside")]
    if lq:
        if X.is_coin:
            kv += [("Average daily volume (USD) 20d / 60d", f"{_usd(lq['vol20'])} / {_usd(lq['vol60'])}")]
        else:
            kv += [("Average volume 20d / 60d", f"{_big(lq['vol20'])} / {_big(lq['vol60'])}"), ("Average dollar volume 20d / 60d", f"{_usd(lq['dollar20'])} / {_usd(lq['dollar60'])}")]
        if "days_1pct" in lq:
            kv.append(("Days to trade 1% of shares (at 20% of volume)", _n(lq["days_1pct"], 1)))
        if "turnover_d" in lq and not X.is_coin:
            kv += [("Daily turnover (of float / shares)", _pct_plain(lq["turnover_d"], 1.0)), ("Annualised turnover (x float)", f"{lq['turnover_y'] / 100:.1f}x")]
    X.add(_kv(kv))
    X.data("Volume profile", ["Price mid", "Volume"], [[y, v] for y, v in zip(ys, vp["vols"])])
    X.data("Liquidity", ["Metric", "Value"], [[k, v] for k, v in kv])
    if price > vp["va_high"]:
        X.flag("info", f"Price above the 1-year value area (POC {_price(vp['poc'])})", "Trend")
    elif price < vp["va_low"]:
        X.flag("info", f"Price below the 1-year value area (POC {_price(vp['poc'])})", "Trend")
    if lq and lq.get("dollar20") and not X.is_coin and lq["dollar20"] < 2e6:
        X.flag("bad", f"Thin liquidity: about {_usd(lq['dollar20'])} traded per day", "Risk")
