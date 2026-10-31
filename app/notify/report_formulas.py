"""FORMULA LAB for the /report PDF: 40 extra calculations that say something about the company, none of which repeat the ratios, DuPont, Altman Z or
Piotroski sections already in the report. Five groups (valuation, growth and efficiency, earnings quality, balance sheet, price behaviour); every row shows
the equation, the value, and a plain-language reading with a colour. A row is simply left out when its inputs are missing."""
from __future__ import annotations

import math
import statistics
from typing import Any

from reportlab.lib.units import mm

from app.notify import report_calc as rc
from app.notify import report_data as rd
from app.notify import report_thesis as rt
from app.notify.report_sections import Ctx
from app.notify.report_ui import _dtable, _s, _usd

GOOD, BAD, NEU = "#15803d", "#b91c1c", "#6b7285"


def _pc(v: float | None, d: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{d}f}%"


def _x(v: float | None, d: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{d}f}x"


def _num(v: float | None, d: int = 2) -> str:
    return "n/a" if v is None else f"{v:,.{d}f}"


def _div(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b == 0 else a / b


def compute_rows(X: Ctx) -> list[tuple[str, str, str, str, str, str]]:
    """The formula rows: (group, name, equation, value, reading, tone). Used by the PDF section and by the interactive DEEP DIVE page."""
    st = X.sec.get("stats") or {}
    ann = (X.D.get("sec") or {}).get("annual") or {}
    price = X.price
    if not price or not (ann or st):
        return []
    rev_s = ann.get("revenue") or {}
    ends = sorted(rev_s) if rev_s else sorted(next(iter(ann.values()), {}))
    if not ends:
        return []
    L = ends[-1]
    P = ends[-2] if len(ends) > 1 else None
    T3 = ends[-4] if len(ends) > 3 else None

    def a(k: str, e: str | None = L) -> float | None:
        return None if e is None else (ann.get(k) or {}).get(e)

    shares, mcap = st.get("shares_out"), X.sec.get("market_cap") or (price * st["shares_out"] if st.get("shares_out") else None)
    debt, cash = st.get("total_debt") or 0.0, st.get("total_cash") or 0.0
    ev = st.get("ev") or (mcap + debt - cash if mcap else None)
    fcf, ebitda, revenue = st.get("fcf"), st.get("ebitda"), st.get("revenue")
    tnx = ((X.D.get("h_tnx") or {}).get("candles") or [[0, 0, 0, 0, None]])[-1][4]
    rf10 = tnx / 100 if tnx else None
    inp = rt._model_inputs(X)
    wacc = inp["wacc"] if inp else 0.10
    rows: list[tuple[str, str, str, str, str, str]] = []  # group, name, equation, value, reading, tone

    def add(group: str, name: str, eq: str, val: str, reading: str, tone: str) -> None:
        rows.append((group, name, eq, val, reading, tone))

    def safe(fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass

    V, G_, Q, B, M = "VALUATION", "GROWTH AND EFFICIENCY", "EARNINGS QUALITY", "BALANCE SHEET", "PRICE BEHAVIOUR"

    # ---------------------------------------------------------------- valuation (10)
    def f1():
        eps = st.get("eps")
        if eps and eps > 0 and rf10 is not None:
            ey = eps / price
            sp = ey - rf10
            add(V, "Earnings yield vs 10-year Treasury", "EPS / price, minus the 10Y yield", f"{_pc(ey)} ({sp * 100:+.1f} pts)", "earns more than Treasuries" if sp > 0 else "earns less than risk-free bonds", "good" if sp > 0.01 else "bad" if sp < -0.02 else "neu")
    safe(f1)

    def f2():
        if fcf is not None and mcap:
            y = fcf / mcap
            add(V, "Free cash flow yield", "free cash flow / market cap", _pc(y), "strong cash return" if y > 0.05 else "thin cash return" if y < 0.02 else "moderate", "good" if y > 0.05 else "bad" if y < 0.02 else "neu")
    safe(f2)

    def f3():
        bb, dv = a("buyback"), a("div")
        if mcap and (bb is not None or dv is not None):
            y = (abs(bb or 0) + abs(dv or 0)) / mcap
            add(V, "Shareholder yield", "(dividends + buybacks last FY) / market cap", _pc(y), "returns a lot of cash" if y > 0.04 else "returns little cash" if y < 0.01 else "moderate", "good" if y > 0.04 else "neu")
    safe(f3)

    def f4():
        if ev and fcf and fcf > 0:
            v = ev / fcf
            add(V, "EV / free cash flow", "enterprise value / FCF", _x(v, 1), "cheap on cash" if v < 15 else "expensive on cash" if v > 35 else "fair", "good" if v < 15 else "bad" if v > 35 else "neu")
    safe(f4)

    def f5():
        ebit = a("opinc")
        if ev and ebit and ebit > 0:
            v = ev / ebit
            add(V, "EV / EBIT", "enterprise value / operating income", _x(v, 1), "cheap" if v < 12 else "expensive" if v > 28 else "fair", "good" if v < 12 else "bad" if v > 28 else "neu")
    safe(f5)

    def f6():
        eq, gw = a("equity"), a("goodwill") or 0.0
        if eq and shares and eq - gw > 0:
            v = price / ((eq - gw) / shares)
            add(V, "Price / tangible book", "price / ((equity - goodwill) / shares)", _x(v, 1), "asset-backed" if v < 2 else "priced on intangibles" if v > 8 else "normal", "good" if v < 2 else "neu")
    safe(f6)

    def f7():
        if shares:
            nc = (cash - debt) / shares
            add(V, "Net cash per share", "(cash - debt) / shares", f"{'-' if nc < 0 else ''}${abs(nc):,.2f} ({nc / price * 100:+.0f}% of price)", "net cash cushion" if nc > 0 else "net debt", "good" if nc > 0.1 * price else "bad" if nc < -0.3 * price else "neu")
    safe(f7)

    def f8():
        ni, da, cx = a("netinc"), a("da"), a("capex")
        if mcap and ni is not None and da is not None and cx is not None:
            oe = ni + da - abs(cx)
            add(V, "Owner earnings yield (Buffett)", "(net income + D&A - capex) / market cap", f"{_pc(oe / mcap)} ({_usd(oe)})", "positive owner earnings" if oe > 0 else "negative owner earnings", "good" if oe / mcap > 0.05 else "bad" if oe < 0 else "neu")
    safe(f8)

    def f9():
        ebit = a("opinc")
        if ebit and ebit > 0 and shares:
            epv = (ebit * 0.79 / wacc + cash - debt) / shares
            add(V, "Earnings power value per share", f"NOPAT / discount rate ({wacc * 100:.1f}%) + net cash", f"${epv:,.2f} ({(epv / price - 1) * 100:+.0f}% vs price)", "price below no-growth value" if epv > price else "price assumes growth", "good" if epv > price * 1.1 else "neu")
    safe(f9)

    def f10():
        pe, gr = st.get("pe"), st.get("earn_growth")
        if pe and pe > 0 and gr and gr > 0.02:
            peg = pe / (gr * 100)
            add(V, "PEG ratio (own calculation)", "trailing P/E / earnings growth %", _num(peg, 2), "growth is cheap" if peg < 1 else "paying up for growth" if peg > 2 else "fair", "good" if peg < 1 else "bad" if peg > 2 else "neu")
    safe(f10)

    # ---------------------------------------------------------------- growth and efficiency (8)
    def g1():
        rg = st.get("rev_growth")
        if rg is not None and fcf is not None and revenue:
            r40 = rg * 100 + fcf / revenue * 100
            add(G_, "Rule of 40", "revenue growth % + FCF margin %", f"{r40:.0f}", "healthy growth/profit balance" if r40 >= 40 else "below the software benchmark" if r40 < 20 else "acceptable", "good" if r40 >= 40 else "bad" if r40 < 10 else "neu")
    safe(g1)

    def g2():
        roe, ni, dv = st.get("roe"), a("netinc"), abs(a("div") or 0.0)
        if roe is not None and ni and ni > 0:
            payout = min(1.0, dv / ni)
            sgr = roe * (1 - payout)
            inflated = roe > 0.6
            add(G_, "Sustainable growth rate", "ROE x (1 - payout ratio)", f"{_pc(sgr)} (payout {payout * 100:.0f}%)", "not meaningful: ROE is inflated by a tiny equity base (buybacks)" if inflated else "can fund this growth internally", "neu" if inflated else ("good" if sgr > 0.1 else "neu"))
    safe(g2)

    def g3():
        o0, o1, r0, r1 = a("opinc", P), a("opinc"), a("revenue", P), a("revenue")
        if None not in (o0, o1, r0, r1) and r1 - r0 > 0:
            im = (o1 - o0) / (r1 - r0)
            add(G_, "Incremental operating margin", "change in operating income / change in revenue", _pc(im, 0), "each new $ is more profitable" if im > (o1 / r1) else "growth is diluting margins", "good" if im > o1 / r1 and im > 0 else "bad" if im < 0 else "neu")
    safe(g3)

    def g4():
        o0, o1, r0, r1 = a("opinc", P), a("opinc"), a("revenue", P), a("revenue")
        if None not in (o0, o1, r0, r1) and o0 > 0 and r0 > 0 and r1 != r0:
            dol = ((o1 - o0) / o0) / ((r1 - r0) / r0)
            add(G_, "Degree of operating leverage", "% change in EBIT / % change in revenue", _x(dol, 1), "profit swings hard with sales" if abs(dol) > 2.5 else "profit moves with sales", "neu")
    safe(g4)

    def g5():
        r0, r1 = a("revenue", T3), a("revenue")
        c = rd.cagr(r0, r1, 3) if T3 else None
        c = None if c is None else c / 100  # report_data.cagr returns percent
        if c is not None:
            add(G_, "Revenue CAGR, 3 years", "(revenue now / revenue 3 years ago)^(1/3) - 1", _pc(c), "compounding fast" if c > 0.15 else "shrinking" if c < 0 else "steady", "good" if c > 0.15 else "bad" if c < 0 else "neu")
    safe(g5)

    def g6():
        e0, e1 = a("eps", T3), a("eps")
        c = rd.cagr(e0, e1, 3) if T3 and e0 and e0 > 0 and e1 and e1 > 0 else None
        c = None if c is None else c / 100
        if c is not None:
            add(G_, "Diluted EPS CAGR, 3 years", "(EPS now / EPS 3 years ago)^(1/3) - 1", _pc(c), "earnings compounding" if c > 0.1 else "earnings shrinking" if c < 0 else "slow", "good" if c > 0.1 else "bad" if c < 0 else "neu")
    safe(g6)

    def g7():
        g0, g1_, r0, r1 = a("gross", P), a("gross"), a("revenue", P), a("revenue")
        if None not in (g0, g1_, r0, r1):
            d = (g1_ / r1 - g0 / r0) * 1e4
            add(G_, "Gross margin change", "this year's gross margin - last year's (basis points)", f"{d:+.0f} bp", "pricing power or cost wins" if d > 50 else "margin pressure" if d < -50 else "stable", "good" if d > 50 else "bad" if d < -50 else "neu")
    safe(g7)

    def g8():
        o0, o1, r0, r1 = a("opinc", P), a("opinc"), a("revenue", P), a("revenue")
        if None not in (o0, o1, r0, r1):
            d = (o1 / r1 - o0 / r0) * 1e4
            add(G_, "Operating margin change", "this year's operating margin - last year's (basis points)", f"{d:+.0f} bp", "expanding" if d > 100 else "contracting" if d < -100 else "stable", "good" if d > 100 else "bad" if d < -100 else "neu")
    safe(g8)

    # ---------------------------------------------------------------- earnings quality (8)
    def q1():
        need = ("ar", "revenue", "gross", "curassets", "ppe", "assets", "da", "sga", "curliab", "debt", "netinc", "ocf")
        if not P:
            return
        d = {k: (a(k), a(k, P)) for k in need}
        def ratio(f):
            try:
                return f()
            except Exception:  # noqa: BLE001
                return None
        rv0, rv1 = d["revenue"][1], d["revenue"][0]
        comp = {
            "DSRI": ratio(lambda: (d["ar"][0] / rv1) / (d["ar"][1] / rv0)),
            "GMI": ratio(lambda: (d["gross"][1] / rv0) / (d["gross"][0] / rv1)),
            "AQI": ratio(lambda: (1 - (d["curassets"][0] + d["ppe"][0]) / d["assets"][0]) / (1 - (d["curassets"][1] + d["ppe"][1]) / d["assets"][1])),
            "SGI": ratio(lambda: rv1 / rv0),
            "DEPI": ratio(lambda: (d["da"][1] / (d["da"][1] + d["ppe"][1])) / (d["da"][0] / (d["da"][0] + d["ppe"][0]))),
            "SGAI": ratio(lambda: (d["sga"][0] / rv1) / (d["sga"][1] / rv0)),
            "LVGI": ratio(lambda: ((d["curliab"][0] + d["debt"][0]) / d["assets"][0]) / ((d["curliab"][1] + d["debt"][1]) / d["assets"][1])),
            "TATA": ratio(lambda: (d["netinc"][0] - d["ocf"][0]) / d["assets"][0]),
        }
        have = sum(1 for v in comp.values() if v is not None and math.isfinite(v))
        if have < 6:
            return
        c = {k: (v if v is not None and math.isfinite(v) else (0.0 if k == "TATA" else 1.0)) for k, v in comp.items()}
        m = -4.84 + 0.920 * c["DSRI"] + 0.528 * c["GMI"] + 0.404 * c["AQI"] + 0.892 * c["SGI"] + 0.115 * c["DEPI"] - 0.172 * c["SGAI"] + 4.679 * c["TATA"] - 0.327 * c["LVGI"]
        add(Q, "Beneish M-score", "8-variable earnings-manipulation model (flag above -1.78)", f"{m:.2f} ({have}/8 inputs)", "pattern resembles manipulators: check the accounts" if m > -1.78 else "no manipulation pattern", "bad" if m > -1.78 else "good")
    safe(q1)

    def q2():
        ni, ocf, a1, a0 = a("netinc"), a("ocf"), a("assets"), a("assets", P)
        if None not in (ni, ocf, a1):
            avg = (a1 + a0) / 2 if a0 else a1
            sl = (ni - ocf) / avg
            add(Q, "Sloan accrual ratio", "(net income - operating cash flow) / average assets", _pc(sl), "earnings backed by cash" if sl < 0.05 else "earnings run ahead of cash" if sl > 0.10 else "normal", "good" if sl < 0.0 else "bad" if sl > 0.10 else "neu")
    safe(q2)

    def q3():
        ni, ocf = a("netinc"), a("ocf")
        if ni and ni > 0 and ocf is not None:
            v = ocf / ni
            add(Q, "Cash conversion of earnings", "operating cash flow / net income", _x(v), "profits turn into cash" if v >= 1 else "profits not fully in cash" if v < 0.8 else "adequate", "good" if v >= 1 else "bad" if v < 0.7 else "neu")
    safe(q3)

    def q4():
        cx, da = a("capex"), a("da")
        if cx and da:
            v = abs(cx) / da
            add(Q, "Capex / depreciation", "capital expenditure / D&A", _x(v), "investing above wear and tear" if v > 1.2 else "harvesting the asset base" if v < 0.7 else "maintaining", "neu")
    safe(q4)

    def q5():
        cx, rv = a("capex"), a("revenue")
        if cx is not None and rv:
            v = abs(cx) / rv
            add(Q, "Capex intensity", "capex / revenue", _pc(v), "capital heavy" if v > 0.12 else "asset light" if v < 0.04 else "moderate", "neu")
    safe(q5)

    def q6():
        sbc, rv = a("sbc"), a("revenue")
        if sbc is not None and rv:
            v = sbc / rv
            add(Q, "Stock compensation burden", "stock-based compensation / revenue", _pc(v), "heavy dilution cost" if v > 0.10 else "low" if v < 0.03 else "moderate", "bad" if v > 0.10 else "good" if v < 0.03 else "neu")
    safe(q6)

    def q7():
        sbc, f, rv = a("sbc"), a("fcf"), a("revenue")
        if sbc is not None and f is not None and rv:
            v = (f - sbc) / rv
            add(Q, "FCF margin after stock compensation", "(free cash flow - SBC) / revenue", _pc(v), "real cash profit" if v > 0.1 else "cash profit disappears after SBC" if v < 0 else "thin", "good" if v > 0.1 else "bad" if v < 0 else "neu")
    safe(q7)

    def q8():
        cx, da, ebit = a("capex"), a("da"), a("opinc")
        if None not in (cx, da, ebit) and ebit > 0:
            v = (abs(cx) - da) / (ebit * 0.79)
            add(Q, "Net reinvestment rate", "(capex - D&A) / NOPAT", _pc(v, 0), "plowing profits back" if v > 0.5 else "returning profits" if v < 0.1 else "balanced", "neu")
    safe(q8)

    # ---------------------------------------------------------------- balance sheet (6)
    def b1():
        ca, inv_, cl = a("curassets"), a("inv") or 0.0, a("curliab")
        if ca and cl:
            v = (ca - inv_) / cl
            add(B, "Quick ratio", "(current assets - inventory) / current liabilities", _x(v), "comfortable short-term cover" if v > 1.2 else "tight" if v < 0.8 else "adequate", "good" if v > 1.2 else "bad" if v < 0.8 else "neu")
    safe(b1)

    def b2():
        c, cl = a("cash"), a("curliab")
        if c is not None and cl:
            v = c / cl
            add(B, "Cash ratio", "cash / current liabilities", _x(v), "can pay bills from cash alone" if v > 1 else "relies on other assets" if v < 0.3 else "moderate", "good" if v > 1 else "neu")
    safe(b2)

    def b3():
        if ebitda and ebitda > 0:
            v = (debt - cash) / ebitda
            add(B, "Net debt / EBITDA", "(debt - cash) / EBITDA", _x(v, 1), "net cash" if v < 0 else "heavily levered" if v > 3.5 else "manageable", "good" if v < 1 else "bad" if v > 3.5 else "neu")
    safe(b3)

    def b4():
        if fcf and fcf > 0 and debt:
            v = debt / fcf
            add(B, "Years of FCF to repay debt", "total debt / free cash flow", f"{v:.1f} years", "repayable quickly" if v < 3 else "slow to repay" if v > 8 else "moderate", "good" if v < 3 else "bad" if v > 8 else "neu")
    safe(b4)

    def b5():
        gw, eq = a("goodwill"), a("equity")
        if gw is not None and eq and eq > 0:
            v = gw / eq
            add(B, "Goodwill as % of equity", "goodwill / shareholders' equity", _pc(v, 0), "acquisition-heavy balance sheet" if v > 0.6 else "little goodwill" if v < 0.15 else "moderate", "bad" if v > 0.6 else "good" if v < 0.15 else "neu")
    safe(b5)

    def b6():
        ca, cl, rv = a("curassets"), a("curliab"), a("revenue")
        if None not in (ca, cl, rv) and rv:
            v = (ca - cl) / rv
            add(B, "Working capital / revenue", "(current assets - current liabilities) / revenue", _pc(v, 0), "funds growth with supplier credit" if v < 0 else "ties up cash in operations" if v > 0.3 else "normal", "neu")
    safe(b6)

    # ---------------------------------------------------------------- price behaviour (8)
    cl = [c for c in X.cl if c]
    n = len(cl)
    bench = ((X.D.get("bench5") or {}).get("candles")) or []

    def m1():
        w = X.sec.get("week52") or [None, None]
        if w[1] and w[0]:
            hi, lo = price / w[1] - 1, price / w[0] - 1
            add(M, "Distance from 52-week high / low", "price / high - 1 and price / low - 1", f"{hi * 100:+.0f}% / {lo * 100:+.0f}%", "near its high: strength" if hi > -0.05 else "deep below its high" if hi < -0.4 else "mid-range", "good" if hi > -0.05 else "bad" if hi < -0.4 else "neu")
    safe(m1)

    def m2():
        if n >= 220:
            s200 = statistics.mean(cl[-200:])
            sd = statistics.pstdev(cl[-200:]) or 1
            z = (price - s200) / sd
            add(M, "Extension from the 200-day average", "(price - SMA200) / standard deviation of price", f"{(price / s200 - 1) * 100:+.0f}% ({z:+.1f} sd)", "stretched above trend" if z > 2 else "stretched below trend" if z < -2 else "near trend", "bad" if z > 2 else "neu")
    safe(m2)

    def m3():
        if n >= 260:
            mo = cl[-22] / cl[-253] - 1
            add(M, "12-1 month momentum", "return from 12 months ago to 1 month ago", _pc(mo, 0), "strong momentum" if mo > 0.2 else "weak momentum" if mo < -0.1 else "flat", "good" if mo > 0.2 else "bad" if mo < -0.1 else "neu")
    safe(m3)

    def m4():
        if len(bench) > 130 and n > 130:
            bc = [c[4] for c in bench if c[4]]
            rs = (cl[-1] / cl[-127]) / (bc[-1] / bc[-127]) - 1
            add(M, "Relative strength vs S&P 500 (6 months)", "(1 + stock return) / (1 + SPY return) - 1", f"{rs * 100:+.0f}%", "outperforming the market" if rs > 0.05 else "lagging the market" if rs < -0.05 else "in line", "good" if rs > 0.05 else "bad" if rs < -0.05 else "neu")
    safe(m4)

    def m5():
        if n >= 253:
            c = cl[-253:]
            peak, mdd = c[0], 0.0
            for v in c:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1)
            ret = c[-1] / c[0] - 1
            if mdd < -0.01:
                cal = ret / abs(mdd)
                add(M, "Calmar ratio (1 year)", "1-year return / maximum drawdown", f"{cal:.2f} (return {ret * 100:+.0f}%, drawdown {mdd * 100:.0f}%)", "return well above the pain" if cal > 1.5 else "pain exceeds return" if cal < 0.5 else "modest", "good" if cal > 1.5 else "bad" if cal < 0 else "neu")
    safe(m5)

    def m6():
        if len(bench) > 200 and n > 200:
            rs_ = rd.daily_returns([c for c in X.c if c[4]][-260:])
            rb_ = rd.daily_returns([c for c in bench if c[4]][-260:])
            ks = [k for k in rs_ if k in rb_]
            up = [(rs_[k], rb_[k]) for k in ks if rb_[k] > 0]
            dn = [(rs_[k], rb_[k]) for k in ks if rb_[k] < 0]
            if len(up) > 30 and len(dn) > 30:
                uc = statistics.mean(a_ for a_, _b in up) / statistics.mean(b_ for _a, b_ in up)
                dc = statistics.mean(a_ for a_, _b in dn) / statistics.mean(b_ for _a, b_ in dn)
                add(M, "Up / down capture vs S&P 500", "average stock move on up days and down days, relative to SPY", f"up {uc * 100:.0f}% / down {dc * 100:.0f}%", "captures more upside than downside" if uc > dc else "suffers more than it gains", "good" if uc > dc * 1.1 else "bad" if uc < dc * 0.9 else "neu")
    safe(m6)

    def m7():
        cd = [c for c in X.c if c[4] and c[5]][-60:]
        if len(cd) >= 40:
            am = statistics.mean(abs(cd[i][4] / cd[i - 1][4] - 1) / (cd[i][4] * cd[i][5]) for i in range(1, len(cd)))
            impact = am * 1e6 * 100
            adv = statistics.mean(c[4] * c[5] for c in cd)
            add(M, "Liquidity: price impact of a $1M trade", "Amihud illiquidity: average |daily return| / dollar volume", f"~{impact:.3f}% (avg ${adv / 1e6:,.0f}M traded per day)", "very liquid" if impact < 0.01 else "thin: large orders will move the price" if impact > 0.2 else "liquid enough", "good" if impact < 0.01 else "bad" if impact > 0.2 else "neu")
    safe(m7)

    def m8():
        at = rd.atr(X.c[-200:]) if len(X.c) > 20 else None
        if at:
            stop = 2 * at
            shares_ = 100 / stop
            add(M, "ATR position size for 1% risk on $10,000", "risk $100 / (2 x ATR14) stop distance", f"{shares_:,.0f} shares (${shares_ * price:,.0f}); ATR {at / price * 100:.1f}% of price", "daily range is wide" if at / price > 0.04 else "calm" if at / price < 0.015 else "normal", "neu")
    safe(m8)

    return rows


def formula_lab(X: Ctx) -> None:
    rows = compute_rows(X)
    if not rows:
        return
    X.title("FORMULA LAB: 40 EXTRA CALCULATIONS")
    V, G_, Q, B, M = "VALUATION", "GROWTH AND EFFICIENCY", "EARNINGS QUALITY", "BALANCE SHEET", "PRICE BEHAVIOUR"
    cur = None
    for grp in (V, G_, Q, B, M):
        gr = [r for r in rows if r[0] == grp]
        if not gr:
            continue
        t = [["#", grp.title(), "Equation", "Value", "Reading"]]
        for i, (_g, name, eq, val, reading, tone) in enumerate(gr, 1):
            col = {"good": GOOD, "bad": BAD}.get(tone, NEU)
            t.append([str(rows.index((_g, name, eq, val, reading, tone)) + 1), f"<b>{_s(name)}</b>", _s(eq), f"<b>{_s(val)}</b>", f"<font color='{col}'>{_s(reading)}</font>"])
        X.add(_dtable(t, [8 * mm, 46 * mm, 52 * mm, 38 * mm, 36 * mm], right=99, esc=False))
        X.sp(5)
        cur = grp
    good = sum(1 for r in rows if r[5] == "good")
    bad = sum(1 for r in rows if r[5] == "bad")
    X.note(f"{len(rows)} of 40 formulas could be computed for this company ({good} read favourable, {bad} read as warnings, the rest neutral); the others are left out because the filings do not report the needed inputs. Equations use the latest fiscal year from SEC filings and the current market data; thresholds in the Reading column are rules of thumb, not standards, and a formula that suits one industry can mislead in another (banks, insurers and REITs especially).")
    X.data("Formula lab", ["Group", "Formula", "Equation", "Value", "Reading", "Tone"], [list(r) for r in rows])
    X.kpi("Formula lab", f"{good} green / {bad} red", "bad" if bad > good else None)
    bm = next((r for r in rows if r[1] == "Beneish M-score"), None)
    if bm and bm[5] == "bad":
        X.flag("bad", f"Beneish M-score {bm[3].split(' ')[0]} is above the -1.78 manipulation threshold", "Quality")
    X.source("Formula lab", "computed here from SEC annual filings, Yahoo market data and price history")
