"""Full PDF research report for one stock / ETF / coin (Telegram /report SYMBOL).

Everything comes from the terminal's own data layer (invest_routes: Yahoo quote/summary/history/news/analysts, CoinGecko for
coins) plus indicators computed here. Screening data, not investment advice.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import time
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle  # noqa: E402
from reportlab.platypus.tableofcontents import TableOfContents  # noqa: E402

from app.notify import report_data as rd  # noqa: E402
from app.notify.report_ui import (AMBER, BODY, CELL, GREEN, H1, H2, INK, LINE, MUTED, RED, SMALL, SUB, W, _big, _colored, _d, _dot, _h, _kv, _n, _p, _pct_plain,  # noqa: E402
                                  _dtable, _png, _price, _s, _style_ax, _sub, _table, _usd)




# ------------------------------------------------------------------ indicators
def _sma(c: list[float], n: int) -> float | None:
    return sum(c[-n:]) / n if len(c) >= n else None


def _rsi(c: list[float], n: int = 14) -> float | None:
    if len(c) <= n:
        return None
    gains = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def _vol_ann(c: list[float], n: int = 30) -> float | None:
    import math

    if len(c) < n + 1:
        return None
    r = [math.log(c[i] / c[i - 1]) for i in range(len(c) - n, len(c)) if c[i - 1] > 0 and c[i] > 0]
    if len(r) < 2:
        return None
    m = sum(r) / len(r)
    return (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** 0.5 * (252 ** 0.5) * 100


def _max_dd(c: list[float]) -> float | None:
    if not c:
        return None
    peak, dd = c[0], 0.0
    for x in c:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    return dd * 100


# ------------------------------------------------------------------ chart
def _chart(candles: list[list[float]], title: str) -> bytes:
    ts = [dt.datetime.fromtimestamp(c[0]) for c in candles]
    cl = [c[4] for c in candles]
    vol = [c[5] for c in candles]
    fig, (ax, av) = plt.subplots(2, 1, figsize=(8.6, 4.3), dpi=170, gridspec_kw={"height_ratios": [4, 1]}, sharex=True)
    up = cl[-1] >= cl[0]
    col = "#15803d" if up else "#b91c1c"
    ax.plot(ts, cl, color=col, lw=1.6)
    ax.fill_between(ts, cl, min(cl), color=col, alpha=0.08)
    for n, cc in ((50, "#d9830c"), (200, "#3b82f6")):
        if len(cl) >= n:
            sm = [sum(cl[i - n + 1:i + 1]) / n for i in range(n - 1, len(cl))]
            ax.plot(ts[n - 1:], sm, color=cc, lw=1.0, ls="--", label=f"SMA{n}")
    ax.legend(loc="upper left", fontsize=7, frameon=False)
    ax.set_title(title, fontsize=9, loc="left", color="#14161d")
    av.bar(ts, vol, color="#9aa3b5", width=1.0)
    for a in (ax, av):
        a.spines[["top", "right"]].set_visible(False)
        a.tick_params(labelsize=7)
        a.grid(alpha=0.25, lw=0.5)
    av.set_yticks([])
    fig.tight_layout()
    b = io.BytesIO()
    fig.savefig(b, format="png")
    plt.close(fig)
    return b.getvalue()


# ------------------------------------------------------------------ pdf building blocks



def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "JORDAN TERMINAL  -  screening data, not investment advice")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(AMBER)
    canvas.setLineWidth(1.5)
    canvas.line(18 * mm, A4[1] - 12 * mm, A4[0] - 18 * mm, A4[1] - 12 * mm)
    canvas.restoreState()



def _tech_rows(cl: list[float], price: float | None) -> list[tuple[str, str]]:
    r = _rsi(cl)
    out = [("RSI (14)", _n(r, 1) + ("  overbought" if r and r > 70 else "  oversold" if r and r < 30 else ""))]
    for n in (20, 50, 200):
        m = _sma(cl, n)
        out.append((f"SMA {n}", "n/a" if m is None else f"{_price(m)} ({_p((price / m - 1) * 100 if price else None)})"))
    out.append(("Volatility (30d, ann.)", _n(_vol_ann(cl), 1) + "%" if _vol_ann(cl) is not None else "n/a"))
    out.append(("Max drawdown (1y)", _n(_max_dd(cl), 1) + "%" if _max_dd(cl) is not None else "n/a"))
    hi, lo = (max(cl), min(cl)) if cl else (None, None)
    out.append(("From 1y high", _p((cl[-1] / hi - 1) * 100) if cl and hi else "n/a"))
    out.append(("From 1y low", _p((cl[-1] / lo - 1) * 100) if cl and lo else "n/a"))
    return out


def _trend_text(cl: list[float]) -> str:
    if len(cl) < 50:
        return "Not enough history for a trend read."
    p, s50, s200, r = cl[-1], _sma(cl, 50), _sma(cl, 200), _rsi(cl)
    bits = [f"Price is {'above' if p > s50 else 'below'} its 50-day average"]
    if s200:
        bits.append(f"and {'above' if p > s200 else 'below'} its 200-day average" + (" (50d over 200d)" if s50 > s200 else " (50d under 200d)"))
    t = " ".join(bits) + "."
    if r is not None:
        t += f" RSI {r:.0f}" + (": stretched to the upside." if r > 70 else ": stretched to the downside." if r < 30 else ": neutral momentum.")
    return t


# ------------------------------------------------------------------ main
def _clean(x: Any) -> Any:
    """Yahoo returns missing fields as {} - treat them as None."""
    if isinstance(x, dict):
        return {k: (None if v == {} else _clean(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x




# ------------------------------------------------------------------ extra charts


def _rsi_series(c: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(c)
    if len(c) <= n:
        return out
    gains = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n, len(gains)):
        ag, al = (ag * (n - 1) + gains[i]) / n, (al * (n - 1) + losses[i]) / n
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _chart_ta(candles: list[list[float]], title: str, bars: int = 180) -> bytes:
    rows = [c for c in candles if None not in c[:5]]
    cl = [c[4] for c in rows]
    ts = [dt.datetime.fromtimestamp(c[0]) for c in rows]
    sma = lambda n: [(sum(cl[i - n + 1:i + 1]) / n if i + 1 >= n else None) for i in range(len(cl))]  # noqa: E731
    mid, up, lo = rd.boll(cl)
    line, sig, hist = rd.macd(cl)
    rsi = _rsi_series(cl)
    k = -bars
    fig, axs = plt.subplots(4, 1, figsize=(8.6, 6.6), dpi=150, sharex=True, gridspec_kw={"height_ratios": [5, 1.2, 1.4, 1.6]})
    ax, av, ar, am = axs
    x = ts[k:]
    ax.plot(x, cl[k:], color="#14161d", lw=1.3, label="Close")
    ax.fill_between(x, [a if a is not None else b for a, b in zip(lo[k:], cl[k:])], [a if a is not None else b for a, b in zip(up[k:], cl[k:])], color="#3b82f6", alpha=0.10, label="Bollinger 20,2")
    for n, cc in ((50, "#d9830c"), (200, "#3b82f6")):
        ys = sma(n)[k:]
        if any(v is not None for v in ys):
            ax.plot(x, [v if v is not None else float("nan") for v in ys], color=cc, lw=1.0, ls="--", label=f"SMA{n}")
    ax.legend(loc="upper left", fontsize=7, frameon=False, ncol=4)
    ax.set_title(title, fontsize=9, loc="left", color="#14161d")
    vol = [c[5] or 0 for c in rows][k:]
    av.bar(x, vol, color=["#15803d" if rows[i][4] >= rows[i][1] else "#b91c1c" for i in range(len(rows))][k:], width=1.0)
    av.set_yticks([])
    av.set_ylabel("Vol", fontsize=7)
    ar.plot(x, [v if v is not None else float("nan") for v in rsi[k:]], color="#7c3aed", lw=1.1)
    ar.axhline(70, color="#b91c1c", lw=0.6, ls=":")
    ar.axhline(30, color="#15803d", lw=0.6, ls=":")
    ar.set_ylim(0, 100)
    ar.set_ylabel("RSI", fontsize=7)
    am.bar(x, hist[k:], color=["#15803d" if h >= 0 else "#b91c1c" for h in hist[k:]], width=1.0)
    am.plot(x, line[k:], color="#14161d", lw=0.9)
    am.plot(x, sig[k:], color="#d9830c", lw=0.9)
    am.set_ylabel("MACD", fontsize=7)
    _style_ax(*axs)
    return _png(fig)


def _chart_fin(periods: list[str], s: dict[str, list[float | None]]) -> bytes:
    keys = [("revenue", "Revenue", "#3b82f6"), ("netinc", "Net income", "#d9830c"), ("fcf", "Free cash flow", "#15803d")]
    keys = [k for k in keys if k[0] in s and any(v is not None for v in s[k[0]])]
    vals = [abs(v) for k, _, _ in keys for v in s[k] if v is not None]
    div, unit = (1e9, "B") if vals and max(vals) >= 1e9 else (1e6, "M")
    fig, ax = plt.subplots(figsize=(8.6, 2.7), dpi=150)
    w = 0.8 / max(1, len(keys))
    for j, (k, lab, cc) in enumerate(keys):
        ax.bar([i + j * w for i in range(len(periods))], [(v or 0) / div for v in s[k]], width=w, color=cc, label=lab)
    ax.set_xticks([i + w * (len(keys) - 1) / 2 for i in range(len(periods))])
    ax.set_xticklabels([f"FY{p[:4]}" for p in periods])
    ax.axhline(0, color="#14161d", lw=0.6)
    ax.set_title(f"Annual revenue, net income and free cash flow (${unit})", fontsize=9, loc="left")
    ax.legend(fontsize=7, frameon=False, ncol=3)
    _style_ax(ax)
    return _png(fig)


def _chart_risk(candles: list[list[float]]) -> bytes:
    ts, dd = rd.drawdown_series(candles)
    mr = rd.monthly_returns(candles)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 2.6), dpi=150, gridspec_kw={"width_ratios": [1.1, 1]})
    a1.fill_between(ts, dd, 0, color="#b91c1c", alpha=0.35)
    a1.plot(ts, dd, color="#b91c1c", lw=0.8)
    a1.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    a1.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    a1.set_title("Drawdown from peak, 2 years (%)", fontsize=9, loc="left")
    if mr:
        a2.bar([m for m, _ in mr], [v for _, v in mr], color=["#15803d" if v >= 0 else "#b91c1c" for _, v in mr])
        a2.set_xticks(range(len(mr)))
        a2.set_xticklabels([m for m, _ in mr], rotation=60, fontsize=6.5)
        a2.axhline(0, color="#14161d", lw=0.6)
    a2.set_title("Monthly returns (%)", fontsize=9, loc="left")
    _style_ax(a1, a2)
    return _png(fig)


def _chart_targets(tg: dict[str, Any], price: float | None, sp: list[dict[str, Any]]) -> bytes | None:
    have_t = all(tg.get(k) for k in ("low", "high")) and price
    ests = [x for x in sp if x.get("actual") is not None and x.get("estimate") is not None][-4:]
    if not have_t and not ests:
        return None
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 2.3), dpi=150)
    if have_t:
        lo, hi = tg["low"], tg["high"]
        a1.barh([0], [hi - lo], left=[lo], color="#e3e6ee", height=0.35)
        if tg.get("mean"):
            a1.plot([tg["mean"]] * 2, [-0.3, 0.3], color="#d9830c", lw=2.5)
            a1.text(tg["mean"], -0.55, f"mean {_price(tg['mean'])}", ha="center", fontsize=7, color="#d9830c")
        a1.plot([price] * 2, [-0.3, 0.3], color="#14161d", lw=2.5)
        a1.text(price, 0.36, f"price {_price(price)}", ha="center", fontsize=7)
        a1.text(lo, -0.55, f"low {_price(lo)}", ha="center", fontsize=7, color="#6b7285")
        a1.text(hi, -0.55, f"high {_price(hi)}", ha="center", fontsize=7, color="#6b7285")
        a1.set_xlim(min(lo, price) * 0.9, max(hi, price) * 1.1)
        a1.set_ylim(-0.8, 0.8)
        a1.set_yticks([])
    a1.set_title("Analyst target range vs price", fontsize=9, loc="left")
    if ests:
        xs = range(len(ests))
        a2.bar([i - 0.2 for i in xs], [x["estimate"] for x in ests], width=0.4, color="#9aa3b5", label="Estimate")
        a2.bar([i + 0.2 for i in xs], [x["actual"] for x in ests], width=0.4, color=["#15803d" if x["actual"] >= x["estimate"] else "#b91c1c" for x in ests], label="Actual")
        a2.set_xticks(list(xs))
        a2.set_xticklabels([dt.datetime.fromtimestamp(x["quarter"]).strftime("%b %y") if isinstance(x.get("quarter"), (int, float)) else str(x.get("quarter") or "") for x in ests], fontsize=7)
        a2.axhline(0, color="#14161d", lw=0.6)
        a2.legend(fontsize=7, frameon=False)
    a2.set_title("EPS: actual vs estimate", fontsize=9, loc="left")
    _style_ax(a1, a2)
    return _png(fig)


# ------------------------------------------------------------------ formatting helpers


def _fmt_fin(key: str, v: float | None) -> str:
    if v is None:
        return "-"
    if key == "eps":
        return f"{v:,.2f}"
    return ("-" if v < 0 else "") + _big(abs(v)) if key != "shares" else _big(v)



MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _fye(v: Any) -> str:
    t = str(v or "")
    return f"{MONTHS[int(t[:2]) - 1]} {int(t[2:])}" if len(t) == 4 and t.isdigit() and 1 <= int(t[:2]) <= 12 else "n/a"




# ------------------------------------------------------------------ main
THEMES = ["Trend", "Valuation", "Quality", "Balance sheet", "Ownership", "Sentiment", "Risk"]


class _Doc(BaseDocTemplate):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        fr = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="p", frames=[fr], onPage=_footer)])

    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph) and fl.style.name == "h2":
            self.notify("TOCEntry", (0, fl.getPlainText(), self.page))


def _kpi_tiles(kpis: list[tuple[str, str, str | None]]) -> Any:
    if not kpis:
        return None
    lab = ParagraphStyle("tile", parent=BODY, fontSize=7.5, leading=9, textColor=MUTED)
    val = ParagraphStyle("tileval", parent=BODY, fontName="Helvetica-Bold", fontSize=11.5, leading=14)
    per = 4
    cells = []
    for label, value, tone in kpis:
        col = {"bad": "#b91c1c", "good": "#15803d"}.get(tone or "", "#14161d")
        cells.append([Paragraph(_s(label), lab), Paragraph(f"<font color='{col}'>{_s(value)}</font>", val)])
    rows = []
    for i in range(0, len(cells), per):
        chunk = cells[i:i + per]
        chunk += [["", ""]] * (per - len(chunk))
        rows.append([Table([[c[0]], [c[1]]], colWidths=[W / per - 6]) if c[0] != "" else "" for c in chunk])
    t = Table(rows, colWidths=[W / per] * per)
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.4, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f9fc")), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _scorecard(flags: list[tuple[str, str, str]]) -> list[Any]:
    by: dict[str, list[tuple[str, str]]] = {}
    for tone, text, theme in flags:
        by.setdefault(theme, []).append((tone, text))
    order = [t for t in THEMES if t in by] + [t for t in by if t not in THEMES]
    rows = [["Theme", "Green", "Amber", "Red"]]
    for th in order:
        fl = by[th]
        rows.append([th, *(str(sum(1 for t, _ in fl if t == k)) for k in ("good", "info", "bad"))])
    rows.append(["Total", *(str(sum(1 for f in flags if f[0] == k)) for k in ("good", "info", "bad"))])
    out: list[Any] = [_dtable(rows, [W - 75 * mm, 25 * mm, 25 * mm, 25 * mm], right=1, bold_first=True), Spacer(1, 6)]
    for th in order:
        out.append(Paragraph(f"<b>{_s(th)}</b>", BODY))
        out.append(_table([[_dot(t), _s(x)] for t, x in by[th]], [7 * mm, W - 7 * mm], esc=False))
        out.append(Spacer(1, 3))
    return out


def _appendix(X: Any, D: dict[str, Any], is_coin: bool) -> list[Any]:
    src = [("Price history", "Yahoo Finance daily candles (5y), refreshed at build time")]
    if is_coin:
        src += [("Coin market data", "CoinGecko (market, supply, ATH/ATL)"), ("Derivatives", "Bybit perpetual funding and open interest, live"),
                ("Sentiment", "alternative.me Fear & Greed, CoinGecko trending and global"), ("Bitcoin network", "mempool.space")]
    else:
        src += [("Quote, valuation, analysts, holders", "Yahoo Finance quoteSummary, live; institutions and funds are capped at 10 rows each by Yahoo"),
                ("Financial statements", "SEC EDGAR XBRL company facts (10-K annual, 10-Q quarterly); Q4 values are derived, so Q4 EPS is approximate"),
                ("Filings and 8-K events", "SEC EDGAR submissions"), ("Insider transactions", "Yahoo insiderTransactions (Form 4 derived), last 12 months"),
                ("Options", "Yahoo options chain, live; IV rank uses the realised-volatility range as a proxy"),
                ("Off-exchange flow", "FINRA ATS weekly data (lags 2-4 weeks) and FINRA daily short-sale volume"), ("Macro", "FRED"), ("Short interest", "FINRA bi-monthly files"), ("Options deep dive", "CBOE delayed chain: gamma exposure, skew, expected move"), ("Filing language and rank vs all filers", "SEC full-text search and XBRL frames"), ("Pipeline, recalls, contracts", "ClinicalTrials.gov, NHTSA, USAspending (when the company has any)"), ("Attention", "Wikimedia pageviews, StockTwits, Hacker News, GDELT"), ("IMF outlook", "IMF DataMapper"), ("Scenario valuation and sensitivity", "Computed here from the data above (consensus revenue, margins, rates); assumptions are printed in the section and are illustrative"),
                ("Analyst-supplied sections", "Bottom-up model, sum-of-the-parts, regulatory, channel checks and governance notes appear only when sourced rows exist in data_cache/thesis_inputs"),
                ("Peers", "Yahoo similar symbols; multiples from Yahoo summary data")]
    src += X.sources
    t = [["Source", "Coverage / freshness"]] + [[_s(a), _s(b)] for a, b in src]
    out: list[Any] = [Paragraph("APPENDIX: DATA SOURCES AND FRESHNESS", H2), _dtable(t, [52 * mm, W - 52 * mm], right=99, esc=False)]
    late = D.get("_late") or []
    if late:
        out += [Spacer(1, 4), _sub("Sources that did not answer in time and were skipped: " + ", ".join(late))]
    out += [Spacer(1, 4), _sub("Every table in this report is also in the companion spreadsheet (one sheet per dataset). Sections whose source returned nothing are omitted.")]
    return out


def build(query: str, progress=None) -> tuple[bytes, str, dict[str, Any]]:
    """(pdf bytes, resolved symbol, datasets for the xlsx). Raises ValueError with a readable message if the symbol can't be found."""
    from fastapi import HTTPException

    from app.api import invest_routes as inv
    from app.notify import report_alt as ra, report_visual as rv, report_formulas as rfm, report_coin as rcoin, report_fund as rf, report_mkt as rm, report_sections as rs, report_thesis as rt

    try:
        sym = inv.resolve(query)["symbol"]
        sec = _clean(inv._security(sym))
    except HTTPException as e:
        raise ValueError(str(e.detail)) from e
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"no data for '{query}'") from e
    is_coin = (sec.get("type") or "").upper() == "CRYPTOCURRENCY"
    note = progress or (lambda *a, **k: None)
    note("resolved", sym=sym, name=sec.get("name"))
    D = _clean(rd.fetch_all(inv, sym, sec, is_coin, progress=lambda done, total: note("fetch", done=done, total=total)))
    note("fetched", late=list(D.get("_late") or []))
    h5 = D.get("hist5") or inv._safe_history(sym, "1y", "1d")
    if not h5 or not h5.get("candles"):
        raise ValueError(f"no price history for '{query}'")
    all_c = [c for c in h5["candles"] if None not in c[:5]]
    candles = all_c[-252:]
    X = rs.Ctx(sym, sec, D, is_coin, all_c)

    price, pct = sec.get("price"), sec.get("pct")
    story: list[Any] = []
    story.append(Paragraph(_s(sym), H1))
    kind = "Cryptocurrency" if is_coin else (sec.get("type") or "Security").title()
    story.append(Paragraph(_s(f"{sec.get('name')}  |  {kind}  |  {sec.get('exchange') or ''}  |  {dt.datetime.now().strftime('%d %b %Y %H:%M')}"), SUB))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"<font size=22><b>{_s(_price(price))}</b></font> <font size=9 color='#6b7285'>{_s(sec.get('currency') or '')}</font>   "
                           + _colored(pct, _p(pct, 2)) + f"  <font size=9 color='#6b7285'>today ({_s(sec.get('market_state') or '')})</font>", ParagraphStyle("px", parent=BODY, leading=30)))
    story.append(Spacer(1, 8))
    story.append(Image(io.BytesIO(_chart(candles, f"{sym} - 1 year, daily close (dashed: SMA50 / SMA200)")), width=W, height=87 * mm))
    w52 = sec.get("week52") or [None, None]
    perf = sec.get("perf") or {}
    story.append(Paragraph("SNAPSHOT", H2))
    pos52 = (price - w52[0]) / (w52[1] - w52[0]) * 100 if price and w52[0] and w52[1] and w52[1] > w52[0] else None
    snap = [("Market cap", _big(sec.get("market_cap"))), ("Volume", _big(sec.get("volume"))), ("Day range", f"{_price(sec.get('low'))} - {_price(sec.get('high'))}"),
            ("Avg volume", _big(sec.get("avg_volume"))), ("52-week range", f"{_price(w52[0])} - {_price(w52[1])}"), ("Position in 52w range", _pct_plain(pos52, 1.0) if pos52 is not None else "n/a")]
    story.append(_kv(snap))
    story.append(Spacer(1, 6))
    if perf:
        story.append(_table([list(perf), [_p(v) if v is not None else "n/a" for v in perf.values()]], [W / max(1, len(perf))] * max(1, len(perf)), True))

    pre = [rs.performance, rs.technicals, rs.multi_tf, rs.candles, rs.behaviour, rs.risk, rs.relative, rs.volume]
    if is_coin:
        fns = [rcoin.coin_data, rcoin.price_cone, *pre, rcoin.derivatives, rcoin.options_crypto, rcoin.sentiment, rcoin.defi, rcoin.network, rm.macro, rm.global_macro, rm.cot, rm.news]
    else:
        fns = [*pre, rf.market_context, rf.valuation_ttm, rv.valuation_field, rcoin.price_cone, rf.financials, rv.profit_sankey, rv.profit_funnel, rv.cash_waterfall, rf.ratios, rf.quality, rfm.formula_lab, rf.earnings, rf.peers, rv.multiples_chart, rv.peer_visuals, ra.rank, rf.ownership, ra.short_int, rf.insiders, rf.analysts,
               rt.scenarios, rt.sensitivity, rt.sotp, rt.segment_model, rt.moat, rt.catalysts, rt.analyst_inputs, rt.governance, rt.capital_allocation,
               rm.options, ra.options_cboe, rm.flow, rm.macro, rm.global_macro, ra.imf, rm.cot, rm.ark, ra.trials, ra.recalls, ra.contracts, rm.filings, ra.language, ra.attention, rm.dividends, rm.news, rm.company]
    rs.run(X, *fns, progress=lambda i, n, name: note("sections", done=i, total=n, name=name))
    note("layout")

    story.append(PageBreak())
    story.append(Paragraph("EXECUTIVE DASHBOARD", H2))
    tiles = _kpi_tiles(X.kpis[:24])
    if tiles is not None:
        story.append(tiles)
        story.append(Spacer(1, 8))
    if X.flags:
        story.append(Paragraph("SCORECARD BY THEME", H2))
        story.extend(_scorecard(X.flags))
        story.append(_sub("Automatic observations from the numbers in this report, not recommendations."))
    story.append(PageBreak())
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("toc0", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK, leftIndent=2, rightIndent=8)]
    toc.dotsMinLevel = 0
    story.append(Paragraph("CONTENTS", H2))
    story.append(toc)
    story.append(PageBreak())

    first = True
    for i, it in enumerate(X.body):
        if isinstance(it, Paragraph) and it.style is H2:
            nxt = [x for x in X.body[i + 1:i + 5] if not isinstance(x, Spacer)][:2]
            need = min(sum(x.drawHeight if isinstance(x, Image) else _h(x) for x in nxt), 110 * mm) + 16 * mm
            if not first:
                story.append(CondPageBreak(need))
            first = False
        story.append(it)
    story.append(Spacer(1, 8))
    story.extend(_appendix(X, D, is_coin))
    story.append(Spacer(1, 10))
    story.append(KeepTogether([Paragraph("Disclaimer: This report is generated automatically from public market data (Yahoo Finance, SEC EDGAR, FINRA, FRED, CoinGecko, Bybit, news feeds) and computed indicators. "
                                         "It is screening information only, may be delayed or incomplete, and is not investment advice.", SMALL)]))
    buf = io.BytesIO()
    doc = _Doc(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title=f"{sym} report", author="Jordan Terminal")
    doc.multiBuild(story)
    sets = dict(X.sets)
    sets["Price history"] = (["Date", "Open", "High", "Low", "Close", "Volume"], [[_d(c[0], "%Y-%m-%d"), *c[1:6]] for c in all_c])
    return buf.getvalue(), sym, sets
