"""Stock-only /report sections, part 1: fundamentals, earnings, valuation vs peers, ownership, analysts."""
from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Image, Paragraph  # noqa: E402

from app.notify import report_calc as rc  # noqa: E402
from app.notify import report_data as rd  # noqa: E402
from app.notify import report_sec  # noqa: E402
from app.notify.report_sections import Ctx, num, pc  # noqa: E402
from app.notify.report_ui import BODY, SMALL, W, _big, _colored, _d, _dtable, _kv, _n, _p, _pct_plain, _price, _s, _style_ax, _table, _usd  # noqa: E402

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PERIOD = {"0m": "This month", "-1m": "1 month ago", "-2m": "2 months ago", "-3m": "3 months ago", "0q": "Current quarter", "+1q": "Next quarter", "0y": "Current year", "+1y": "Next year"}

INCOME = [("revenue", "Revenue"), ("cogs", "Cost of revenue"), ("gross", "Gross profit"), ("rnd", "R&D"), ("sga", "SG&A"), ("opex", "Total operating expenses"), ("opinc", "Operating income"),
          ("intexp", "Interest expense"), ("pretax", "Pre-tax income"), ("tax", "Income tax"), ("netinc", "Net income"), ("eps", "Diluted EPS"), ("shares", "Diluted shares")]
CASHFLOW = [("ocf", "Operating cash flow"), ("capex", "Capital expenditure"), ("fcf", "Free cash flow"), ("da", "Depreciation and amortisation"), ("sbc", "Stock-based compensation"),
            ("icf", "Investing cash flow"), ("fincf", "Financing cash flow"), ("div", "Dividends paid"), ("buyback", "Share buybacks")]
BALANCE = [("cash", "Cash and equivalents"), ("ar", "Receivables"), ("inv", "Inventory"), ("curassets", "Current assets"), ("ppe", "Property and equipment"), ("goodwill", "Goodwill"),
           ("assets", "Total assets"), ("ap", "Payables"), ("curliab", "Current liabilities"), ("debt", "Long-term debt"), ("liab", "Total liabilities"), ("retained", "Retained earnings"),
           ("equity", "Shareholders' equity")]


def _fv(key: str, v: float | None) -> str:
    if v is None:
        return "-"
    if key == "eps":
        return f"{v:,.2f}"
    if key == "shares":
        return _big(v)
    return ("-" if v < 0 else "") + _big(abs(v))


def _fmt(fmt: str, v: float | None) -> str:
    if v is None:
        return "-"
    return {"pct": f"{v:.1f}%", "x": f"{v:.2f}x", "usd": _usd(v), "num": f"{v:,.0f}"}.get(fmt, f"{v:.2f}")


def _flt(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ------------------------------------------------------------------ market context + TTM valuation (Yahoo)
def market_context(X: Ctx) -> None:
    D, sec = X.D, X.sec
    perf, pct = sec.get("perf") or {}, sec.get("pct")
    ctx = [["", "Day", "1W", "1M", "YTD"]]
    ctx.append([f"<b>{_s(X.sym)}</b>", _colored(pct, _p(pct, 2)), _colored(perf.get("1W"), _p(perf.get("1W"))), _colored(perf.get("1M"), _p(perf.get("1M"))), _colored(perf.get("YTD"), _p(perf.get("YTD")))])
    for k, nm in (("m_sector", None), ("m_spy", "S&P 500 (SPY)"), ("m_qqq", "Nasdaq 100 (QQQ)"), ("m_iwm", "Russell 2000 (IWM)")):
        r = D.get(k)
        if r:
            ctx.append([_s(nm or r.get("name")), _colored(r.get("pct"), _p(r.get("pct"), 2)), _colored(r.get("p1w"), _p(r.get("p1w"))), _colored(r.get("p1m"), _p(r.get("p1m"))), _colored(r.get("ytd"), _p(r.get("ytd")))])
    X.title("MARKET CONTEXT")
    X.add(_table(ctx, [50 * mm] + [31 * mm] * 4, True, esc=False))
    vix, tnx = D.get("m_vix"), D.get("m_tnx")
    extra = []
    if vix:
        extra.append(("VIX", f"{vix['price']:.1f} ({_p(vix.get('pct'), 1)})"))
        if vix["price"] > 25:
            X.flag("bad", f"VIX {vix['price']:.0f}: elevated market fear", "Risk")
    if tnx:
        extra.append(("US 10Y yield", f"{tnx['price']:.2f}%"))
    if extra:
        X.sp(4)
        X.add(_kv(extra))
    X.data("Market context", ["Series", "Day %", "1W %", "1M %", "YTD %"], [[r.get("name") or r.get("symbol"), r.get("pct"), r.get("p1w"), r.get("p1m"), r.get("ytd")] for r in (D.get(k) for k in ("m_sector", "m_spy", "m_qqq", "m_iwm")) if r])


def valuation_ttm(X: Ctx) -> None:
    st = X.sec.get("stats") or {}
    X.title("VALUATION (YAHOO, TRAILING TWELVE MONTHS)")
    kv = [("P/E (trailing)", _n(st.get("pe"), 1)), ("P/E (forward)", _n(st.get("fwd_pe"), 1)), ("EPS (ttm)", _n(st.get("eps"))), ("EPS (forward)", _n(st.get("fwd_eps"))),
          ("PEG", _n(st.get("peg"))), ("Price / book", _n(st.get("pb"))), ("Price / sales", _n(st.get("ps"))), ("EV / revenue", _n(st.get("ev_rev"), 1)),
          ("EV / EBITDA", _n(st.get("ev_ebitda"), 1)), ("Enterprise value", _usd(st.get("ev"))), ("Book value / share", _n(st.get("book_value"))), ("Beta", _n(st.get("beta"))),
          ("Dividend yield", _pct_plain(st.get("div_yield"), 1.0) if (st.get("div_yield") or 0) > 0.5 else _pct_plain(st.get("div_yield"))), ("Payout ratio", _pct_plain(st.get("payout")))]
    X.add(_kv(kv))
    X.title("PROFITABILITY AND BALANCE SHEET (TTM)")
    kv2 = [("Revenue (ttm)", _usd(st.get("revenue"))), ("Revenue growth", _pct_plain(st.get("rev_growth"))), ("Earnings growth", _pct_plain(st.get("earn_growth"))),
           ("Gross margin", _pct_plain(st.get("gross_margin"))), ("Operating margin", _pct_plain(st.get("op_margin"))), ("Net margin", _pct_plain(st.get("profit_margin"))),
           ("EBITDA", _usd(st.get("ebitda"))), ("Free cash flow", _usd(st.get("fcf"))), ("ROE", _pct_plain(st.get("roe"))), ("ROA", _pct_plain(st.get("roa"))),
           ("Total cash", _usd(st.get("total_cash"))), ("Total debt", _usd(st.get("total_debt"))), ("Debt / equity", _n(st.get("debt_equity"), 1)), ("Current ratio", _n(st.get("current_ratio")))]
    X.add(_kv(kv2))
    X.data("Valuation and TTM", ["Metric", "Value"], [[k, v] for k, v in kv + kv2])
    if _flt(st.get("ps")) and st["ps"] > 15:
        X.flag("bad", f"Price/sales {st['ps']:.0f}x: priced far ahead of current revenue", "Valuation")
    if _flt(st.get("pe")) and 0 < st["pe"] < 12:
        X.flag("good", f"Trailing P/E {st['pe']:.1f}", "Valuation")
    elif _flt(st.get("pe")) and st["pe"] > 45:
        X.flag("bad", f"Trailing P/E {st['pe']:.0f}", "Valuation")
    if _flt(st.get("profit_margin")) and st["profit_margin"] < -0.2:
        X.flag("bad", f"Net margin {st['profit_margin'] * 100:.0f}% (loss-making)", "Quality")
    elif _flt(st.get("profit_margin")) and st["profit_margin"] > 0.2:
        X.flag("good", f"Net margin {st['profit_margin'] * 100:.0f}%", "Quality")
    if _flt(st.get("rev_growth")) and st["rev_growth"] > 0.25:
        X.flag("good", f"Revenue growing {st['rev_growth'] * 100:.0f}% year over year", "Quality")
    elif _flt(st.get("rev_growth")) and st["rev_growth"] < -0.1:
        X.flag("bad", f"Revenue shrinking {abs(st['rev_growth']) * 100:.0f}% year over year", "Quality")
    if _flt(st.get("debt_equity")) and st["debt_equity"] > 200:
        X.flag("bad", f"Debt/equity {st['debt_equity']:.0f}%", "Balance sheet")
    if _flt(st.get("current_ratio")) and st["current_ratio"] < 1:
        X.flag("bad", f"Current ratio {st['current_ratio']:.2f} (short-term liabilities exceed short-term assets)", "Balance sheet")
    X.kpi("Market cap", _usd(X.sec.get("market_cap")))
    if _flt(st.get("pe")):
        X.kpi("P/E (trailing)", f"{st['pe']:.1f}")
    elif _flt(st.get("ps")):
        X.kpi("Price / sales", f"{st['ps']:.1f}x")


# ------------------------------------------------------------------ SEC financial statements
def _stmt_table(store: dict[str, dict[str, float]], es: list[str], spec: list[tuple[str, str]], fmt_end: str = "%b %Y") -> list[list[str]]:
    rows = [[""] + [dt.date.fromisoformat(e).strftime(fmt_end) for e in es]]
    for k, lab in spec:
        vals = [(store.get(k) or {}).get(e) for e in es]
        if any(v is not None for v in vals):
            rows.append([lab] + [_fv(k, v) for v in vals])
    return rows


def _fin_fallback(X: Ctx) -> None:
    periods, fs = rd.fin_series(X.D.get("fin"))
    if not periods:
        return
    periods, fs = periods[-5:], {k: v[-5:] for k, v in fs.items()}
    from app.notify import report_pdf as pdf

    X.title("FINANCIAL HISTORY (SEC 10-K)")
    X.add(Image(__import__("io").BytesIO(pdf._chart_fin(periods, fs)), width=W, height=W * 2.7 / 8.6))
    X.sp()
    labels = [("revenue", "Revenue"), ("gross", "Gross profit"), ("opinc", "Operating income"), ("netinc", "Net income"), ("eps", "Diluted EPS"), ("ocf", "Operating cash flow"), ("fcf", "Free cash flow"),
              ("cash", "Cash"), ("debt", "Long-term debt"), ("equity", "Equity"), ("shares", "Diluted shares")]
    rows = [[""] + [f"FY{p[:4]}" for p in periods]] + [[lab] + [_fv(k, v) for v in fs[k]] for k, lab in labels if k in fs and any(v is not None for v in fs[k])]
    cw = (W - 40 * mm) / len(periods)
    X.add(_dtable(rows, [40 * mm] + [cw] * len(periods), bold_first=True))
    X.data("Annual financials", rows[0], rows[1:])


def financials(X: Ctx) -> None:
    S = X.D.get("sec")
    if not S or not S.get("annual"):
        _fin_fallback(X)
        return
    ann, qtr = S["annual"], S["quarterly"]
    es = rc.ends(ann, keys=("revenue", "netinc", "assets", "ocf"), n=8)
    if es:
        X.title("ANNUAL FINANCIAL STATEMENTS (SEC 10-K, USD)")
        w0 = 44 * mm
        cw = (W - w0) / len(es)
        for name, spec in (("Income statement", INCOME), ("Cash flow", CASHFLOW), ("Balance sheet", BALANCE)):
            rows = _stmt_table(ann, es, spec)
            if len(rows) > 1:
                X.add(Paragraph(f"<b>{name}</b>", BODY))
                X.sp(2)
                X.add(_dtable(rows, [w0] + [cw] * len(es), bold_first=True))
                X.sp(6)
                X.data(f"Annual - {name}", rows[0], rows[1:])
        X.note("Negative values are shown with a minus sign. Figures are the company's audited values as tagged in SEC XBRL; Q4 is not reported separately so quarterly tables derive from 10-Q filings.")
        _annual_charts(X, ann, es)
    eq = rc.ends(qtr, keys=("revenue", "netinc", "assets", "ocf"), n=8)
    if eq:
        X.title("QUARTERLY FINANCIALS (SEC 10-Q, LAST 8 QUARTERS)")
        w0 = 40 * mm
        cw = (W - w0) / len(eq)
        for name, spec in (("Income statement", INCOME[:12]), ("Cash flow", CASHFLOW[:3] + CASHFLOW[4:6]), ("Balance sheet", BALANCE[:1] + BALANCE[6:7] + BALANCE[9:10] + BALANCE[12:13])):
            rows = _stmt_table(qtr, eq, spec, "%b %y")
            if len(rows) > 1:
                X.add(Paragraph(f"<b>{name}</b>", BODY))
                X.sp(2)
                X.add(_dtable(rows, [w0] + [cw] * len(eq), bold_first=True))
                X.sp(6)
                X.data(f"Quarterly - {name}", rows[0], rows[1:])
        grow = [[""] + [dt.date.fromisoformat(e).strftime("%b %y") for e in eq]]
        gkeys = [("revenue", "Revenue"), ("gross", "Gross profit"), ("opinc", "Operating income"), ("netinc", "Net income"), ("ocf", "Operating cash flow")]
        for k, lab in gkeys:
            allq = sorted(qtr.get(k, {}))
            ys, qs = zip(*(rc.growth_pair(qtr, k, e, allq) if e in qtr.get(k, {}) else (None, None) for e in eq))
            if any(v is not None for v in ys):
                grow.append([f"{lab} YoY"] + [pc(v, 0) if v is not None else "-" for v in ys])
            if any(v is not None for v in qs):
                grow.append([f"{lab} QoQ"] + [pc(v, 0) if v is not None else "-" for v in qs])
        if len(grow) > 1:
            X.add(Paragraph("<b>Growth (percent change; shown only where the base period is positive)</b>", BODY))
            X.sp(2)
            X.add(_dtable(grow, [w0] + [cw] * len(eq), esc=False, bold_first=True))
            X.sp(4)
            X.data("Quarterly growth", grow[0], [[re.sub("<[^>]+>", "", str(c)) for c in r] for r in grow[1:]])
        _quarter_chart(X, qtr, eq)
        _quarter_flags(X, qtr, eq)


def _annual_charts(X: Ctx, ann: dict[str, dict[str, float]], es: list[str]) -> None:
    from app.notify import report_pdf as pdf

    fs = {k: [(ann.get(k) or {}).get(e) for e in es] for k in ("revenue", "netinc", "fcf")}
    if any(v is not None for v in fs["revenue"]):
        X.add(Image(__import__("io").BytesIO(pdf._chart_fin(es, fs)), width=W, height=W * 2.7 / 8.6))


def _quarter_chart(X: Ctx, qtr: dict[str, dict[str, float]], eq: list[str]) -> None:
    m = rc.quarter_margins(qtr, eq)
    rev = [(qtr.get("revenue") or {}).get(e) for e in eq]
    if not any(v for v in rev):
        return
    lab = [dt.date.fromisoformat(e).strftime("%b %y") for e in eq]
    div, unit = (1e9, "B") if max(abs(v or 0) for v in rev) >= 1e9 else (1e6, "M")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 2.6), dpi=150)
    a1.bar(range(len(eq)), [(v or 0) / div for v in rev], color="#3b82f6")
    ni = [(qtr.get("netinc") or {}).get(e) for e in eq]
    a1.plot(range(len(eq)), [(v or 0) / div for v in ni], color="#d9830c", marker="o", ms=3, lw=1.2, label="Net income")
    a1.axhline(0, color="#14161d", lw=0.6)
    a1.set_xticks(range(len(eq)))
    a1.set_xticklabels(lab, fontsize=6.5, rotation=45)
    a1.set_title(f"Quarterly revenue (bars) and net income (${unit})", fontsize=8.5, loc="left")
    a1.legend(fontsize=6.5, frameon=False)
    for k, nm, cc in (("gm", "Gross", "#15803d"), ("om", "Operating", "#d9830c"), ("nm", "Net", "#b91c1c")):
        ys = [v if v is not None else float("nan") for v in m[k]]
        if any(v == v for v in ys):
            a2.plot(range(len(eq)), [max(min(v, 150), -300) if v == v else v for v in ys], marker="o", ms=3, lw=1.2, color=cc, label=nm)
    a2.axhline(0, color="#14161d", lw=0.6)
    a2.set_xticks(range(len(eq)))
    a2.set_xticklabels(lab, fontsize=6.5, rotation=45)
    a2.set_title("Margin trend by quarter (%)", fontsize=8.5, loc="left")
    a2.legend(fontsize=6.5, frameon=False, ncol=3)
    _style_ax(a1, a2)
    X.fig(fig)


def _quarter_flags(X: Ctx, qtr: dict[str, dict[str, float]], eq: list[str]) -> None:
    last = eq[-1]
    ys, qs = rc.growth_pair(qtr, "revenue", last, sorted(qtr.get("revenue", {}))) if last in qtr.get("revenue", {}) else (None, None)
    if ys is not None:
        X.kpi("Revenue YoY (latest qtr)", _p(ys, 0), "good" if ys > 0 else "bad")
        if ys > 30:
            X.flag("good", f"Latest quarter revenue up {ys:.0f}% year over year", "Quality")
        elif ys < -10:
            X.flag("bad", f"Latest quarter revenue down {abs(ys):.0f}% year over year", "Quality")
    sh = qtr.get("shares") or {}
    es_sh = sorted(sh)
    if len(es_sh) >= 5 and sh[es_sh[-5]]:
        ch = (sh[es_sh[-1]] / sh[es_sh[-5]] - 1) * 100
        if ch > 10:
            X.flag("bad", f"Diluted share count up {ch:.0f}% over the last 4 quarters", "Quality")


# ------------------------------------------------------------------ ratios + quality
def ratios(X: Ctx) -> None:
    S = X.D.get("sec")
    if not S or not S.get("annual"):
        return
    ann = S["annual"]
    es, rows = rc.ratio_table(ann, 6)
    if not rows:
        return
    X.title("RATIO ANALYSIS (DERIVED FROM 10-K DATA)")
    w0 = 70 * mm
    cw = (W - w0) / len(es)
    t = [[""] + [dt.date.fromisoformat(e).strftime("%b %Y") for e in es]] + [[lab] + [_fmt(f, v) for v in vals] for lab, vals, f in rows]
    X.add(_dtable(t, [w0] + [cw] * len(es), bold_first=True))
    X.note("ROE/ROA use average balances. ROIC = operating income x (1 - tax rate, default 21%) / (debt + equity - cash). Cash conversion cycle = DSO + DIO - DPO.")
    X.data("Ratios", t[0], t[1:])
    az = rc.altman_z(ann, X.sec.get("market_cap"))
    if az:
        X.sp(6)
        X.title("ALTMAN Z-SCORE (DISTRESS SCREEN)")
        X.text(f"Z = <b>{az['z']:.2f}</b>: <b>{_s(az['zone'])}</b> (fiscal year ended {_s(az['fy'])}, uses today's market cap). Original manufacturing-firm model; less meaningful for banks, biotech and early-stage companies.")
        X.sp(3)
        X.add(_kv([(k, f"{v:+.2f}") for k, v in az["parts"].items()], cols=1, width=110 * mm))
        X.flag("bad" if az["z"] < 1.81 else "good" if az["z"] > 2.99 else "info", f"Altman Z-score {az['z']:.1f}: {az['zone']}", "Balance sheet")
        X.data("Altman Z", ["Component", "Contribution"], [[k, v] for k, v in az["parts"].items()] + [["Z", az["z"]]])
    last = {lab: vals[-1] for lab, vals, _ in rows}
    roic = last.get("ROIC (NOPAT / invested capital)")
    if roic is not None:
        X.kpi("ROIC (last FY)", f"{roic:.0f}%", "good" if roic > 12 else "bad" if roic < 0 else None)
        if roic > 20:
            X.flag("good", f"ROIC {roic:.0f}% in the last fiscal year", "Quality")
    nde = last.get("Net debt / EBITDA (x)")
    if nde is not None and nde > 4:
        X.flag("bad", f"Net debt / EBITDA {nde:.1f}x", "Balance sheet")
    cov = last.get("Interest coverage (EBIT / interest, x)")
    if cov is not None and 0 < cov < 2:
        X.flag("bad", f"Interest coverage only {cov:.1f}x", "Balance sheet")


def quality(X: Ctx) -> None:
    periods, fs = rd.fin_series(X.D.get("fin"))
    if not periods:
        return
    periods, fs = periods[-5:], {k: v[-5:] for k, v in fs.items()}
    st = X.sec.get("stats") or {}

    def first_ok(k: str) -> int:
        v = fs.get(k) or []
        return next((i for i, x in enumerate(v) if x), 0)
    i_rev, i_sh = first_ok("revenue"), first_ok("shares")
    n_rev, n_sh = len(periods) - 1 - i_rev, len(periods) - 1 - i_sh
    cg_rev = rd.cagr(rd._g(fs, "revenue", i_rev), rd._g(fs, "revenue", -1), n_rev)
    rev, ni, gp = rd._g(fs, "revenue", -1), rd._g(fs, "netinc", -1), rd._g(fs, "gross", -1)
    sh_chg = (rd._g(fs, "shares", -1) / rd._g(fs, "shares", i_sh) - 1) * 100 if n_sh > 0 and rd._g(fs, "shares", i_sh) and rd._g(fs, "shares", -1) else None
    sh_yoy = (rd._g(fs, "shares", -1) / rd._g(fs, "shares", -2) - 1) * 100 if rd._g(fs, "shares", -2) and rd._g(fs, "shares", -1) else None
    cash = st.get("total_cash") if st.get("total_cash") is not None else rd._g(fs, "cash", -1)
    fcf_now = rd._g(fs, "fcf", -1) if rd._g(fs, "fcf", -1) is not None else st.get("fcf")
    run = rd.runway_months(cash, fcf_now)
    cr = rd._g(fs, "curassets", -1) / rd._g(fs, "curliab", -1) if rd._g(fs, "curliab", -1) and rd._g(fs, "curassets", -1) else None
    X.title("GROWTH, DILUTION AND RUNWAY")
    X.add(_kv([(f"Revenue CAGR ({n_rev}y)", _p(cg_rev, 1) if cg_rev is not None else "n/a"), ("Net margin (last FY)", _pct_plain(ni / rev, 100) if ni is not None and rev else "n/a"),
               ("Gross margin (last FY)", _pct_plain(gp / rev, 100) if gp is not None and rev else "n/a"), ("Current ratio (last FY)", _n(cr)),
               (f"Share count change ({n_sh}y)", _p(sh_chg, 0) if sh_chg is not None else "n/a"), ("Share count change (last FY)", _p(sh_yoy, 1) if sh_yoy is not None else "n/a"),
               ("Runway (latest cash / last FY FCF burn)", f"{run:.0f} months" if run is not None else ("positive FCF last FY" if fcf_now is not None and fcf_now >= 0 else "n/a"))]))
    if run is not None:
        X.flag("bad" if run < 18 else "info", f"Cash runway about {run:.0f} months (latest cash vs last fiscal year FCF burn)", "Balance sheet")
        X.kpi("Cash runway", f"{run:.0f} months", "bad" if run < 18 else None)
    elif fcf_now is not None and fcf_now > 0:
        X.flag("good", "Positive free cash flow in the last fiscal year", "Balance sheet")
    if sh_yoy is not None and sh_yoy > 8:
        X.flag("bad", f"Share count up {sh_yoy:.0f}% in the last fiscal year (dilution)", "Quality")
    if cg_rev is not None and cg_rev > 15:
        X.flag("good", f"Revenue CAGR {cg_rev:.0f}% over {n_rev} years", "Quality")
    pt = rd.piotroski(fs)
    tested = [t for t in pt if t[1] is not None]
    score = sum(1 for _, ok in tested if ok)
    X.title("QUALITY SCORE: PIOTROSKI F-SCORE")
    X.text(f"<b>{score} / {len(tested)}</b> tests passed (8-9 strong, 0-3 weak). Latest fiscal year vs the one before, from audited filings.")
    X.sp(3)
    cells = [(f"<font color='{'#15803d' if ok else '#b91c1c' if ok is False else '#6b7285'}'><b>{'PASS' if ok else 'FAIL' if ok is False else 'n/a'}</b></font>", _s(t)) for t, ok in pt]
    X.add(_table([[cells[i][0], cells[i][1], cells[i + 1][0] if i + 1 < len(cells) else "", cells[i + 1][1] if i + 1 < len(cells) else ""] for i in range(0, len(cells), 2)], [14 * mm, 73 * mm, 14 * mm, 73 * mm], esc=False))
    X.data("Piotroski", ["Test", "Result"], [[t, "pass" if ok else "fail" if ok is False else "n/a"] for t, ok in pt])
    if len(tested) >= 6:
        X.kpi("Piotroski F-score", f"{score}/{len(tested)}", "good" if score >= 7 else "bad" if score <= 3 else None)
        X.flag("good" if score >= 7 else "bad" if score <= 3 else "info", f"Piotroski F-score {score}/{len(tested)}", "Quality")


# ------------------------------------------------------------------ earnings
def earnings(X: Ctx) -> None:
    D, sec = X.D, X.sec
    an = D.get("analysts") or {}
    S = D.get("sec") or {}
    sp = an.get("surprises") or []
    est = [e for e in (an.get("estimates") or []) if e.get("eps_avg") is not None]
    ne = an.get("next_earnings") or sec.get("next_earnings")
    dates = report_sec.earnings_dates(S.get("events") or []) if S else []
    react = rc.earnings_reactions(X.c, dates, 12) if dates else {}
    trend = (D.get("raw") or {}).get("earningsTrend") or {}
    if not (sp or est or ne or react.get("rows") or trend.get("trend")):
        return
    X.title("EARNINGS: HISTORY, REACTIONS AND ESTIMATES")
    d0 = None
    if ne:
        d0 = ne[0] if isinstance(ne, list) else ne
        X.text(f"Next earnings: <b>{_s(_d(d0))}</b>")
        X.sp(3)
        if isinstance(d0, (int, float)) and 0 <= d0 - time.time() <= 14 * 86400:
            X.flag("info", f"Earnings in {max(0, int((d0 - time.time()) // 86400))} days ({_d(d0)})", "Sentiment")
        if isinstance(d0, (int, float)):
            X.kpi("Next earnings", _d(d0, "%d %b"))
    rows_r = react.get("rows") or []
    if rows_r:
        X.add(Paragraph("<b>Price reaction to the last earnings releases</b>", BODY))
        X.sp(2)
        t = [["Release", "Timing", "Reaction day", "Move", "Open gap", "Day range", "5-day drift", "Rel. volume"]] + [
            [r["date"].strftime("%d %b %Y"), r["timing"], r["react_day"].strftime("%d %b"), pc(r["move"], 1), pc(r["gap"], 1), _pct_plain(r["range"], 1.0), pc(r["drift5"], 1) if r["drift5"] is not None else "-",
             f"{r['rvol']:.1f}x" if r["rvol"] else "-"] for r in rows_r]
        X.add(_dtable(t, [24 * mm, 24 * mm, 22 * mm, 19 * mm, 19 * mm, 19 * mm, 24 * mm, 23 * mm], right=3, esc=False))
        X.sp(3)
        kv = [("Average absolute move", _pct_plain(react["avg_abs"], 1.0)), ("Median absolute move", _pct_plain(react["median_abs"], 1.0)), ("Up after earnings", f"{react['up_pct']:.0f}% of {react['n']}"),
              ("Average signed move", _p(react["avg"], 1)), ("Best / worst reaction", f"{_p(react['best'], 1)} / {_p(react['worst'], 1)}")]
        ov = D.get("optvol") or {}
        ex = next((e for e in ov.get("expiries") or [] if d0 and isinstance(d0, (int, float)) and e["expiry"] >= d0), None)
        if ex and ex.get("exp_move_pct"):
            kv.append((f"Options-implied move to {_d(ex['expiry'], '%d %b')} expiry", f"+/- {ex['exp_move_pct']:.1f}% (includes the earnings event)"))
        X.add(_kv(kv))
        X.data("Earnings reactions", ["Release", "Timing", "Reaction day", "Move %", "Gap %", "Range %", "5d drift %", "Rel volume"],
               [[r["date"].isoformat(), r["timing"], r["react_day"].isoformat(), r["move"], r["gap"], r["range"], r["drift5"], r["rvol"]] for r in rows_r])
        fig, ax = plt.subplots(figsize=(8.6, 2.2), dpi=150)
        mv = [r["move"] for r in rows_r][::-1]
        ax.bar(range(len(mv)), mv, color=["#15803d" if v >= 0 else "#b91c1c" for v in mv])
        ax.set_xticks(range(len(mv)))
        ax.set_xticklabels([r["react_day"].strftime("%b %y") for r in rows_r][::-1], fontsize=6.5)
        ax.axhline(0, color="#14161d", lw=0.6)
        ax.set_title("Price move on the reaction day (%)", fontsize=8.5, loc="left")
        _style_ax(ax)
        X.sp(4)
        X.fig(fig)
        X.kpi("Avg earnings move", f"+/-{react['avg_abs']:.1f}%")
        if react["avg_abs"] > 12:
            X.flag("info", f"Earnings moves average +/-{react['avg_abs']:.0f}% (last {react['n']})", "Risk")
    if sp:
        X.sp(6)
        X.add(Paragraph("<b>EPS surprises</b>", BODY))
        X.sp(2)
        t = [["Quarter", "EPS actual", "EPS estimate", "Surprise"]] + [[_d(x.get("quarter"), "%b %Y"), _n(x.get("actual")), _n(x.get("estimate")), _p(x["surprise_pct"] * 100 if x.get("surprise_pct") is not None else None)] for x in sp]
        X.add(_dtable(t, [W / 4] * 4))
        X.data("EPS surprises", t[0], t[1:])
        beats = sum(1 for x in sp if (x.get("surprise_pct") or 0) > 0)
        if len(sp) >= 4:
            X.flag("good" if beats == len(sp) else "bad" if beats == 0 else "info", f"Beat EPS estimates in {beats} of the last {len(sp)} quarters", "Sentiment")
    if est:
        X.sp(6)
        X.add(Paragraph("<b>Consensus estimates</b>", BODY))
        X.sp(2)
        t = [["Period", "EPS est (avg)", "Low / High", "Revenue est", "Growth", "Analysts"]] + [[PERIOD.get(str(e.get("period")), str(e.get("period") or "")), _n(e.get("eps_avg")),
                                                                                                f"{_n(e.get('eps_low'))} / {_n(e.get('eps_high'))}", _usd(e.get("rev_avg")) if e.get("rev_avg") else "n/a",
                                                                                                _pct_plain(e.get("growth")), str(e.get("analysts") or "")] for e in est]
        X.add(_dtable(t, [28 * mm, 28 * mm, 34 * mm, 32 * mm, 26 * mm, 26 * mm]))
        X.data("Consensus estimates", t[0], t[1:])
    tr = trend.get("trend") or []
    rows = []
    for x in tr:
        et, rv = x.get("epsTrend") or {}, x.get("epsRevisions") or {}
        ee, re_ = x.get("earningsEstimate") or {}, x.get("revenueEstimate") or {}
        if et.get("current") is None:
            continue
        rows.append([PERIOD.get(str(x.get("period")), str(x.get("period"))), _n(et.get("current"), 3), _n(et.get("7daysAgo"), 3), _n(et.get("30daysAgo"), 3), _n(et.get("60daysAgo"), 3), _n(et.get("90daysAgo"), 3),
                     f"{int(rv.get('upLast30days') or 0)} / {int(rv.get('downLast30days') or 0)}", _pct_plain(re_.get("growth")) if re_.get("growth") is not None else "n/a"])
    if rows:
        X.sp(6)
        X.add(Paragraph("<b>EPS estimate revisions (consensus now vs earlier)</b>", BODY))
        X.sp(2)
        t = [["Period", "Now", "7d ago", "30d ago", "60d ago", "90d ago", "Revisions 30d (up / down)", "Revenue growth est."]] + rows
        X.add(_dtable(t, [28 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 30 * mm, 26 * mm]))
        X.note("Rising estimates ahead of earnings are a positive revision trend; falling estimates the opposite. For loss-makers a less negative EPS is an improvement.")
        X.data("Estimate revisions", t[0], t[1:])
        cur = next((x for x in tr if x.get("period") == "0q"), None)
        if cur:
            r30 = (cur.get("epsRevisions") or {})
            if (r30.get("upLast30days") or 0) > (r30.get("downLast30days") or 0) + 1:
                X.flag("good", f"{int(r30['upLast30days'])} upward EPS revisions in the last 30 days", "Sentiment")
            elif (r30.get("downLast30days") or 0) > (r30.get("upLast30days") or 0) + 1:
                X.flag("bad", f"{int(r30['downLast30days'])} downward EPS revisions in the last 30 days", "Sentiment")


# ------------------------------------------------------------------ valuation vs peers
def _vfmt(fmt: str, v: float | None) -> str:
    if v is None:
        return "-"
    return {"x": f"{v:.1f}x", "pct": f"{v:.1f}%", "usd": _usd(v)}[fmt]


def peers(X: Ctx) -> None:
    D = X.D
    px = D.get("peer_extra") or {}
    plist = px.get("peers") or []
    if not plist:
        return
    X.title("PEER GROUP AND RELATIVE VALUATION")
    rows = [["Symbol", "Name", "Price", "Day", "~1M", "52w position"]]
    raw = []
    for p_ in plist[:10]:
        sp_ = p_.get("spark") or []
        m1 = (sp_[-1] / sp_[-21] - 1) * 100 if len(sp_) >= 21 and sp_[-21] else None
        w = p_.get("week52") or [None, None]
        pos = (p_["price"] - w[0]) / (w[1] - w[0]) * 100 if p_.get("price") and w[0] and w[1] and w[1] > w[0] else None
        rows.append([f"<b>{_s(p_['symbol'])}</b>", _s((p_.get("name") or "")[:32]), _price(p_.get("price")), pc(p_.get("pct"), 1), pc(m1, 0), _pct_plain(pos, 1.0) if pos is not None else "n/a"])
        raw.append([p_["symbol"], p_.get("name"), p_.get("price"), p_.get("pct"), m1, pos])
    X.add(_dtable(rows, [22 * mm, 62 * mm, 26 * mm, 20 * mm, 20 * mm, 24 * mm], right=2, esc=False))
    X.data("Peers", ["Symbol", "Name", "Price", "Day %", "1M %", "52w position %"], raw)
    subj = rc.val_row(D.get("subj_val"))
    pv = {s: rc.val_row(q) for s, q in (px.get("val") or {}).items() if q}
    if subj and pv:
        cmp_ = rc.valuation_compare(subj, pv)
        if cmp_:
            X.sp(8)
            X.add(Paragraph(f"<b>{_s(X.sym)} vs {len(pv)} peers ({_s(', '.join(pv))})</b>", BODY))
            X.sp(2)
            t = [["Metric", X.sym, "Peer median", "Peer low", "Peer high", "Percentile", "Premium to median"]]
            for r in cmp_:
                t.append([r["label"], _vfmt(r["fmt"], r["subject"]), _vfmt(r["fmt"], r["median"]), _vfmt(r["fmt"], r["min"]), _vfmt(r["fmt"], r["max"]),
                          f"{r['pctile']:.0f}th" if r["pctile"] is not None else "-", pc(r["prem"], 0) if r["prem"] is not None else "-"])
            X.add(_dtable(t, [38 * mm, 24 * mm, 26 * mm, 24 * mm, 24 * mm, 18 * mm, 20 * mm], esc=False, bold_first=True))
            X.note("Percentile = share of peers with a lower value. Premium compares multiples only where both sides are positive. Peers are Yahoo's 'similar companies', not a curated comp set.")
            X.data("Valuation vs peers", t[0], [[r["label"], r["subject"], r["median"], r["min"], r["max"], r["pctile"], r["prem"]] for r in cmp_])
            mat = [["Peer", "P/E fwd", "P/S", "EV/Rev", "EV/EBITDA", "Gross margin", "Rev. growth", "Market cap"]]
            for s, r in [(X.sym, subj)] + list(pv.items()):
                mat.append([f"<b>{_s(s)}</b>", _vfmt("x", r.get("fpe")), _vfmt("x", r.get("ps")), _vfmt("x", r.get("evrev")), _vfmt("x", r.get("eveb")), _vfmt("pct", r.get("gm")), _vfmt("pct", r.get("rg")),
                            _vfmt("usd", r.get("mcap"))])
            if any(str(c) not in ("-", "n/a") and not str(c).startswith("<b>") for r in mat[1:] for c in r):
                X.sp(6)
                X.add(_dtable(mat, [24 * mm, 20 * mm, 18 * mm, 20 * mm, 24 * mm, 26 * mm, 22 * mm, 20 * mm], esc=False))
                X.data("Valuation matrix", [c.replace("<b>", "") for c in mat[0]], [[re.sub("<[^>]+>", "", str(c)) for c in r] for r in mat[1:]])
            ps = next((r for r in cmp_ if r["key"] == "ps"), None)
            if ps and ps["prem"] is not None and ps["n"] >= 3:
                if ps["prem"] > 100:
                    X.flag("bad", f"Price/sales {ps['prem']:+.0f}% vs peer median", "Valuation")
                elif ps["prem"] < -30:
                    X.flag("good", f"Price/sales {ps['prem']:+.0f}% vs peer median", "Valuation")
    st = X.sec.get("stats") or {}
    fcf, sh = st.get("fcf"), st.get("shares_out")
    if fcf and sh and X.price:
        g = rc.reverse_dcf(X.price, fcf / sh)
        if g is not None:
            X.sp(6)
            X.text(f"<b>Reverse DCF (illustrative):</b> at {_s(_price(X.price))}, with trailing free cash flow of {_s(_price(fcf / sh))} per share, a 10% discount rate and 3% terminal growth, "
                   f"the price implies about <b>{g:.0f}%</b> annual FCF growth for 10 years. Compare with the growth you expect; this is a sanity check, not a valuation.")
            X.flag("bad" if g > 30 else "info", f"Price implies ~{g:.0f}% annual FCF growth for 10 years (reverse DCF)", "Valuation")


# ------------------------------------------------------------------ ownership + insiders
def _norm_insiders(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for t in raw or []:
        out.append({"name": t.get("filerName"), "relation": t.get("filerRelation"), "text": t.get("transactionText"), "date": t.get("startDate"), "shares": t.get("shares"), "value": t.get("value")})
    return out


def ownership(X: Ctx) -> None:
    D, st = X.D, X.sec.get("stats") or {}
    ho = D.get("holders") or {}
    ks = D.get("keystats") or {}
    kst = ks.get("defaultKeyStatistics") or {}
    mb = ho.get("breakdown") or ks.get("majorHoldersBreakdown") or {}
    X.title("OWNERSHIP AND SHORT INTEREST")
    own = [("Held by insiders", _pct_plain(mb.get("insidersPercentHeld"))), ("Held by institutions", _pct_plain(mb.get("institutionsPercentHeld"))),
           ("Institutions (% of float)", _pct_plain(mb.get("institutionsFloatPercentHeld"))), ("Institutions count", f"{int(mb['institutionsCount']):,}" if mb.get("institutionsCount") else "n/a"),
           ("Shares outstanding", _big(st.get("shares_out"))), ("Float", _big(st.get("float") or kst.get("floatShares")))]
    ss = kst.get("sharesShort")
    spf = kst.get("shortPercentOfFloat") if kst.get("shortPercentOfFloat") is not None else st.get("short_pct_float")
    own += [("Shares short", _big(ss)), ("Short % of float", _pct_plain(spf)), ("Days to cover", _n(kst.get("shortRatio"), 1)),
            ("Short interest vs prior month", _p((ss / kst["sharesShortPriorMonth"] - 1) * 100, 0) if ss and kst.get("sharesShortPriorMonth") else "n/a")]
    inst = ho.get("institutions") or []
    top10 = sum(i.get("pct") or 0 for i in inst[:10])
    if top10:
        own.append(("Top-10 institutions combined", _pct_plain(top10)))
    X.add(_kv(own))
    X.data("Ownership", ["Metric", "Value"], [[k, v] for k, v in own])
    if spf is not None:
        X.kpi("Short % of float", _pct_plain(spf), "bad" if spf >= 0.15 else None)
    if spf is not None and spf >= 0.10:
        X.flag("bad", f"Short interest {spf * 100:.0f}% of float ({_n(kst.get('shortRatio'), 1)} days to cover)", "Ownership")
    ip = mb.get("institutionsPercentHeld")
    if ip is not None and ip > 0.8:
        X.flag("info", f"Institutions hold {ip * 100:.0f}% of shares (crowded ownership)", "Ownership")
    for title, rows_, key in (("Top institutional holders (Yahoo lists the top 10)", inst, "Institutions"), ("Top fund holders (Yahoo lists the top 10)", ho.get("funds") or [], "Funds")):
        if rows_:
            X.sp(6)
            X.add(Paragraph(f"<b>{title}</b>", BODY))
            X.sp(2)
            t = [["Holder", "Shares", "% held", "Value", "Change", "Reported"]] + [[_s((i.get("name") or "")[:54]), _big(i.get("shares")), _pct_plain(i.get("pct")), _usd(i.get("value")) if i.get("value") else "",
                                                                                   pc(i["chg"] * 100 if i.get("chg") is not None else None, 1), _d(i.get("date"), "%d %b %y")] for i in rows_]
            X.add(_dtable(t, [72 * mm, 24 * mm, 16 * mm, 24 * mm, 20 * mm, 18 * mm], esc=False))
            X.data(f"Holders - {key}", ["Holder", "Shares", "% held", "Value", "Change", "Reported"], [[i.get("name"), i.get("shares"), i.get("pct"), i.get("value"), i.get("chg"), _d(i.get("date"), "%Y-%m-%d")] for i in rows_])
    na = ho.get("net_activity") or {}
    if na:
        netsh = na.get("netInfoShares")
        X.sp(6)
        X.add(_kv([(f"Insider buys ({na.get('period') or ''})", f"{int(na.get('buyInfoCount') or 0)} tx, {_big(na.get('buyInfoShares'))} sh"),
                   (f"Insider sells ({na.get('period') or ''})", f"{int(na.get('sellInfoCount') or 0)} tx, {_big(na.get('sellInfoShares'))} sh"),
                   ("Net insider shares", _big(netsh) if netsh is not None else "n/a"), ("Institutional net change", _big(na.get("netInstSharesBuying")) if na.get("netInstSharesBuying") is not None else "n/a")]))
        if netsh is not None and netsh != 0:
            X.flag("good" if netsh > 0 else "bad", f"Insiders net {'buyers' if netsh > 0 else 'sellers'} ({_big(abs(netsh))} shares, {na.get('period') or 'recent'})", "Ownership")


def insiders(X: Ctx) -> None:
    form4 = X.D.get("form4_tx")
    source = "SEC Form 4 filings (direct, parsed from the raw XML)"
    if not form4:
        raw = ((X.D.get("raw") or {}).get("insiderTransactions") or {}).get("transactions")
        form4 = _norm_insiders(raw) or (X.D.get("holders") or {}).get("transactions")
        source = "Yahoo Finance insider transactions (from SEC Form 4)"
    ist = rc.insider_stats(form4)
    if not ist["rows"]:
        return
    a = ist["agg"]
    X.title("INSIDER TRANSACTIONS (LAST 12 MONTHS)")
    kv = [("Open-market purchases", f"{a['buy']['n']} tx by {a['buy']['insiders']} insiders, {_big(a['buy']['shares'])} sh, {_usd(a['buy']['value'])}"),
          ("Sales", f"{a['sell']['n']} tx by {a['sell']['insiders']} insiders, {_big(a['sell']['shares'])} sh, {_usd(a['sell']['value'])}"),
          ("Other (awards, exercises, conversions)", f"{a['other']['n']} tx, {_big(a['other']['shares'])} sh"), ("Net bought / sold value", _usd(a["buy"]["value"] - a["sell"]["value"]))]
    if ist["cluster"]:
        k, n, d0 = ist["cluster"]
        kv.append(("Cluster detected", f"{n} insiders {'buying' if k == 'buy' else 'selling'} within 30 days from {d0.strftime('%d %b %Y')}"))
    X.add(_kv(kv, cols=1))
    X.sp(5)
    t = [["Date", "Insider", "Role", "Type", "Transaction", "Shares", "Value"]]
    for r in ist["rows"]:
        kind = {"buy": "<font color='#15803d'><b>BUY</b></font>", "sell": "<font color='#b91c1c'><b>SELL</b></font>", "other": "other"}[r["kind"]]
        t.append([dt.datetime.fromtimestamp(r["ts"]).strftime("%d %b %y"), _s((r.get("name") or "").title()[:26]), _s((r.get("relation") or "")[:22]), kind, _s((r.get("text") or "")[:46]), _big(r.get("shares")),
                  _usd(r["value"]) if r.get("value") else ""])
    X.add(_dtable(t, [17 * mm, 34 * mm, 29 * mm, 11 * mm, 53 * mm, 15 * mm, 15 * mm], right=5, esc=False))
    X.note(f"Source: {source}. 'Other' includes option exercises, conversions, tax withholding and stock awards, which are not open-market buy/sell signals. SEC filings cap at the most recent 25 in the last 12 months when the company has more than that.")
    X.data("Insider transactions", ["Date", "Insider", "Role", "Type", "Transaction", "Shares", "Value"],
           [[dt.datetime.fromtimestamp(r["ts"]).date().isoformat(), r.get("name"), r.get("relation"), r["kind"], r.get("text"), r.get("shares"), r.get("value")] for r in ist["rows"]])
    if a["buy"]["n"] and a["buy"]["value"] > 100_000:
        X.flag("good", f"Insiders bought {_usd(a['buy']['value'])} in the open market over 12 months", "Ownership")
    if a["sell"]["value"] > 5_000_000 and a["sell"]["value"] > 5 * a["buy"]["value"]:
        X.flag("bad", f"Insiders sold {_usd(a['sell']['value'])} over 12 months vs {_usd(a['buy']['value'])} bought", "Ownership")
    if ist["cluster"]:
        k, n, d0 = ist["cluster"]
        X.flag("good" if k == "buy" else "bad", f"Insider {'buying' if k == 'buy' else 'selling'} cluster: {n} insiders within 30 days ({d0.strftime('%d %b %Y')})", "Ownership")
    f4 = (X.D.get("sec") or {}).get("form4_by_month") or []
    if f4:
        fig, ax = plt.subplots(figsize=(8.6, 1.9), dpi=150)
        ax.bar([m for m, _ in f4], [n for _, n in f4], color="#9aa3b5")
        ax.set_title("Form 4 (insider) filings per month", fontsize=8.5, loc="left")
        ax.tick_params(axis="x", labelrotation=45, labelsize=6.5)
        _style_ax(ax)
        X.sp(4)
        X.fig(fig)


# ------------------------------------------------------------------ analysts
def analysts(X: Ctx) -> None:
    from app.notify import report_pdf as pdf

    D, tg, price = X.D, X.sec.get("target") or {}, X.price
    an = D.get("analysts") or {}
    hist = ((D.get("raw") or {}).get("upgradeDowngradeHistory") or {}).get("history")
    ast = rc.analyst_stats(hist, price)
    X.title("ANALYST COVERAGE")
    up = (tg["mean"] / price - 1) * 100 if tg.get("mean") and price else None
    kv = [("Consensus", (tg.get("rating") or "n/a").replace("_", " ").upper()), ("Analysts", str(tg.get("n") or "n/a")), ("Mean target", _price(tg.get("mean"))), ("Median target", _price(tg.get("median"))),
          ("Upside to mean", _p(up, 0)), ("Target high / low", f"{_price(tg.get('high'))} / {_price(tg.get('low'))}")]
    d = ast.get("dispersion")
    if d:
        kv += [("Target dispersion (stdev)", f"{_price(d['stdev'])} ({d['stdev'] / d['mean'] * 100:.0f}% of mean)"), ("Targets in last 6 months", f"{d['n']} firms, median {_price(d['median'])} ({_p(d.get('upside_median'), 0)})")]
    X.add(_kv(kv))
    X.data("Analyst consensus", ["Metric", "Value"], [[k, v] for k, v in kv])
    if up is not None:
        X.kpi("Analyst upside", _p(up, 0), "good" if up > 15 else "bad" if up < 0 else None)
        X.flag("good" if up > 25 else "bad" if up < 0 else "info", f"Analyst mean target implies {up:+.0f}% ({tg.get('n') or '?'} analysts, {(tg.get('rating') or 'n/a').replace('_', ' ')})", "Sentiment")
    if d and d["mean"] and d["stdev"] / d["mean"] > 0.4:
        X.flag("info", f"Wide disagreement on price targets (stdev {d['stdev'] / d['mean'] * 100:.0f}% of mean)", "Sentiment")
    png = pdf._chart_targets(tg, price, an.get("surprises") or [])
    if png:
        X.sp(4)
        X.add(Image(__import__("io").BytesIO(png), width=W, height=W * 2.3 / 8.6))
    trend = an.get("recommendation") or []
    if trend:
        X.sp(5)
        t = [["Period", "Strong buy", "Buy", "Hold", "Sell", "Strong sell"]] + [[PERIOD.get(str(x.get("period")), str(x.get("period") or "")), *[str(x.get(k, 0)) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")]] for x in trend]
        X.add(_dtable(t, [W / 6] * 6))
        X.data("Rating distribution", t[0], t[1:])
    tl = ast.get("tally") or {}
    if tl.get("12m", {}).get("n"):
        X.sp(5)
        t = [["Rating actions", "Upgrades", "Downgrades", "Initiations", "Reiterations", "Target raises", "Target cuts", "Total"]]
        for k, lab in (("6m", "Last 6 months"), ("12m", "Last 12 months")):
            x = tl[k]
            t.append([lab, str(x["up"]), str(x["down"]), str(x["init"]), str(x["main"]), str(x["pt_up"]), str(x["pt_dn"]), str(x["n"])])
        X.add(_dtable(t, [32 * mm, 20 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 20 * mm, 14 * mm], bold_first=True))
        X.data("Rating action tally", t[0], t[1:])
        x = tl["6m"]
        if x["up"] > x["down"] + 1:
            X.flag("good", f"{x['up']} upgrades vs {x['down']} downgrades in 6 months", "Sentiment")
        elif x["down"] > x["up"] + 1:
            X.flag("bad", f"{x['down']} downgrades vs {x['up']} upgrades in 6 months", "Sentiment")
    if ast.get("by_month") and len(ast["by_month"]) >= 3:
        bm = ast["by_month"]
        fig, ax = plt.subplots(figsize=(8.6, 1.9), dpi=150)
        xs = range(len(bm))
        ax.bar(xs, [v["up"] for _, v in bm], color="#15803d", label="Upgrades")
        ax.bar(xs, [-v["down"] for _, v in bm], color="#b91c1c", label="Downgrades")
        ax.bar(xs, [v["other"] for _, v in bm], bottom=[v["up"] for _, v in bm], color="#c9cedb", label="Other actions")
        ax.set_xticks(list(xs))
        ax.set_xticklabels([m for m, _ in bm], fontsize=6.5, rotation=45)
        ax.axhline(0, color="#14161d", lw=0.6)
        ax.legend(fontsize=6.5, frameon=False, ncol=3)
        ax.set_title("Analyst rating actions per month", fontsize=8.5, loc="left")
        _style_ax(ax)
        X.sp(4)
        X.fig(fig)
    rows = ast.get("rows") or []
    if rows:
        X.sp(6)
        X.add(Paragraph(f"<b>All rating changes in the last 12 months ({len(rows)})</b>", BODY))
        X.sp(2)
        t = [["Date", "Firm", "Action", "Grade", "Price target", "Prior target"]]
        for h in rows:
            act = {"up": "Upgrade", "down": "Downgrade", "init": "Initiated", "main": "Maintained", "reit": "Reiterated"}.get(h.get("action"), (h.get("action") or "").title())
            col = "#15803d" if h.get("action") == "up" else "#b91c1c" if h.get("action") == "down" else "#14161d"
            t.append([dt.datetime.fromtimestamp(h["epochGradeDate"]).strftime("%d %b %y"), _s((h.get("firm") or "")[:30]), f"<font color='{col}'><b>{act}</b></font>",
                      _s(f"{h.get('fromGrade') or ''} > {h.get('toGrade') or ''}".strip(" >")[:32]), _price(h.get("currentPriceTarget")) if h.get("currentPriceTarget") else "-",
                      _price(h.get("priorPriceTarget")) if h.get("priorPriceTarget") else "-"])
        X.add(_dtable(t, [18 * mm, 48 * mm, 24 * mm, 46 * mm, 19 * mm, 19 * mm], right=4, esc=False))
        X.data("Analyst actions", ["Date", "Firm", "Action", "From", "To", "Target", "Prior target"],
               [[dt.datetime.fromtimestamp(h["epochGradeDate"]).date().isoformat(), h.get("firm"), h.get("action"), h.get("fromGrade"), h.get("toGrade"), h.get("currentPriceTarget"), h.get("priorPriceTarget")] for h in rows])
