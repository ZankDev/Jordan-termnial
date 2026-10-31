"""Visual /report sections: fair-value football field with a price-vs-value meter, income-statement Sankey, profit funnel, cash waterfall,
peer radar and bubble chart, and a multiples-premium chart. Everything is computed from data the report already fetched; every section
returns quietly (and is dropped) when its inputs are missing, and every estimate is labelled as an estimate."""
from __future__ import annotations

import math
import statistics
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import PathPatch, Polygon  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402

from app.notify import report_calc as rc  # noqa: E402
from app.notify import report_thesis as rt  # noqa: E402
from app.notify.report_sections import Ctx  # noqa: E402
from app.notify.report_ui import _big, _dtable, _kv, _p, _price, _s, _style_ax, _usd  # noqa: E402

GREEN, RED, AMBER, BLUE, GREY, DARK = "#15803d", "#b91c1c", "#d9830c", "#1d4ed8", "#9aa3b2", "#1f2937"


def _quart(vals: list[float]) -> tuple[float, float, float]:
    v = sorted(vals)
    if len(v) >= 4:
        q = statistics.quantiles(v, n=4)
        return q[0], q[1], q[2]
    return v[0], statistics.median(v), v[-1]


# ------------------------------------------------------------------ fair value
def _methods(X: Ctx) -> list[dict[str, Any]]:
    st = X.sec.get("stats") or {}
    price = X.price
    shares, debt, cash = st.get("shares_out"), st.get("total_debt") or 0.0, st.get("total_cash") or 0.0
    out: list[dict[str, Any]] = []
    if not price or not shares:
        return out
    inp = rt._model_inputs(X)
    if inp:
        sc = {s["name"]: s["val"] for s in rt._scenarios(inp)}
        if all(k in sc for k in ("Bear", "Base", "Bull")):
            out.append({"name": "Scenario DCF (bear / base / bull)", "low": sc["Bear"], "mid": sc["Base"], "high": sc["Bull"], "w": 1.0, "basis": "5-year free-cash-flow model on street revenue estimates"})
    px = X.D.get("peer_extra") or {}
    pv = [v for v in (rc.val_row(q) for q in (px.get("val") or {}).values() if q) if v]
    eps = st.get("fwd_eps") if (st.get("fwd_eps") or 0) > 0 else (st.get("eps") if (st.get("eps") or 0) > 0 else None)
    key = "fpe" if eps is not None and eps == st.get("fwd_eps") else "pe"
    pes = [p[key] for p in pv if p.get(key) and 0 < p[key] < 150]
    if eps and len(pes) >= 3:
        lo, md, hi = _quart(pes)
        out.append({"name": f"Peer P/E x {'forward' if key == 'fpe' else 'trailing'} EPS", "low": lo * eps, "mid": md * eps, "high": hi * eps, "w": 1.0, "basis": f"peer P/E {lo:.1f} / {md:.1f} / {hi:.1f}x on EPS {eps:.2f}"})
    ebitda = st.get("ebitda")
    evs = [p["eveb"] for p in pv if p.get("eveb") and 0 < p["eveb"] < 80]
    if ebitda and ebitda > 0 and len(evs) >= 3:
        lo, md, hi = _quart(evs)
        f = lambda m: max((m * ebitda - debt + cash) / shares, 0.0)  # noqa: E731
        out.append({"name": "Peer EV/EBITDA", "low": f(lo), "mid": f(md), "high": f(hi), "w": 1.0, "basis": f"peer EV/EBITDA {lo:.1f} / {md:.1f} / {hi:.1f}x"})
    rev = st.get("revenue")
    pss = [p["ps"] for p in pv if p.get("ps") and 0 < p["ps"] < 60]
    if rev and rev > 0 and len(pss) >= 3:
        lo, md, hi = _quart(pss)
        out.append({"name": "Peer price/sales", "low": lo * rev / shares, "mid": md * rev / shares, "high": hi * rev / shares, "w": 0.7 if eps else 1.0, "basis": f"peer P/S {lo:.1f} / {md:.1f} / {hi:.1f}x on revenue {_usd(rev)}"})
    tg = X.sec.get("target") or {}
    if tg.get("low") and tg.get("high") and (tg.get("median") or tg.get("mean")):
        out.append({"name": f"Analyst price targets ({tg.get('n') or '?'})", "low": tg["low"], "mid": tg.get("median") or tg["mean"], "high": tg["high"], "w": 1.0, "basis": "street low / median / high"})
    bvps = st.get("book_value")
    if eps and bvps and bvps > 0:
        g = math.sqrt(22.5 * eps * bvps)
        out.append({"name": "Graham number", "low": g * 0.85, "mid": g, "high": g * 1.15, "w": 0.5, "basis": "sqrt(22.5 x EPS x book value per share): a conservative value-investor ceiling"})
    return [m for m in out if m["mid"] and m["mid"] > 0]


def fair_value(X: Ctx) -> dict[str, Any] | None:
    """Methods, outlier handling and the blended fair value (shared by the PDF section and the interactive page)."""
    ms = _methods(X)
    price = X.price
    if len(ms) < 2 or not price:
        return None
    med = statistics.median(m["mid"] for m in ms)
    for m in ms:  # a method far from the rest (a Graham number on an asset-light giant, a DCF on a name valued for growth) is shown but kept out of the blend
        m["out"] = (m["mid"] < 0.4 * med or m["mid"] > 2.5 * med) and not m["name"].startswith("Analyst")  # street targets are never dropped
    used = [m for m in ms if not m["out"]] or ms
    wsum = sum(m["w"] for m in used)
    fair = sum(m["mid"] * m["w"] for m in used) / wsum
    mids = [m["mid"] for m in used]
    cv = statistics.pstdev(mids) / statistics.mean(mids) if len(mids) > 1 else 0
    up = (fair / price - 1) * 100
    verdict = "Looks undervalued by this blend" if up > 20 else "Looks overvalued by this blend" if up < -20 else "Roughly in line with this blend"
    tone = "good" if up > 20 else "bad" if up < -20 else "info"
    return {"ms": ms, "used": used, "fair": fair, "cv": cv, "up": up, "verdict": verdict, "tone": tone, "price": price}


def valuation_field(X: Ctx) -> None:
    m = fair_value(X)
    if not m:
        return
    ms, used, fair, cv, up, verdict, tone, price = m["ms"], m["used"], m["fair"], m["cv"], m["up"], m["verdict"], m["tone"], m["price"]
    X.title("FAIR VALUE ESTIMATE VS CURRENT PRICE")
    fig = plt.figure(figsize=(7.4, 1.0 + 0.5 * len(ms) + 0.9))
    gs = fig.add_gridspec(2, 1, height_ratios=[len(ms) * 0.5 + 0.4, 0.9], hspace=0.55)
    ax = fig.add_subplot(gs[0])
    ys = list(range(len(ms)))[::-1]
    lo52, hi52 = (X.sec.get("week52") or [None, None])
    if lo52 and hi52:
        ax.axvspan(lo52, hi52, color="#e5e7eb", alpha=0.7, zorder=0, label="52-week range")
    for y, m in zip(ys, ms):
        ax.barh(y, m["high"] - m["low"], left=m["low"], height=0.5, color=AMBER, alpha=0.55, zorder=2)
        ax.plot([m["mid"], m["mid"]], [y - 0.28, y + 0.28], color=DARK, lw=2.2, zorder=3)
        ax.text(m["high"], y, f"  ${m['high']:,.0f}", va="center", fontsize=7, color=DARK)
        ax.text(m["low"], y, f"${m['low']:,.0f}  ", va="center", ha="right", fontsize=7, color=DARK)
    ax.axvline(price, color=BLUE, lw=1.8, zorder=4, label=f"price ${price:,.2f}")
    ax.axvline(fair, color=GREEN if up >= 0 else RED, lw=1.8, ls="--", zorder=4, label=f"blended fair value ${fair:,.2f}")
    ax.set_yticks(ys)
    import textwrap
    ax.set_yticklabels([textwrap.fill(m["name"] + (" (excluded)" if m["out"] else ""), 24) for m in ms], fontsize=7)
    fig.subplots_adjust(left=0.27, right=0.96, top=0.93, bottom=0.1)
    ax.set_title(f"{X.sym}: valuation methods (bar = low to high, tick = middle)", fontsize=8.5, loc="left")
    ax.legend(fontsize=7, frameon=False, loc="lower right", ncol=3)
    xs = [m["low"] for m in ms] + [m["high"] for m in ms] + [price, fair]
    ax.set_xlim(max(0, min(xs) * 0.8), max(xs) * 1.12)
    _style_ax(ax)
    # price-vs-fair-value meter: how far the price sits from the blend
    a2 = fig.add_subplot(gs[1])
    r = max(-0.6, min(0.6, price / fair - 1))
    a2.axvspan(-0.6, -0.2, color="#bbf7d0")
    a2.axvspan(-0.2, 0.2, color="#fef3c7")
    a2.axvspan(0.2, 0.6, color="#fecaca")
    a2.plot([r], [0.5], marker="v", ms=11, color=DARK, zorder=5)
    a2.text(-0.4, 0.15, "cheap vs blend", ha="center", fontsize=7, color=GREEN)
    a2.text(0.0, 0.15, "fair", ha="center", fontsize=7, color=AMBER)
    a2.text(0.4, 0.15, "expensive vs blend", ha="center", fontsize=7, color=RED)
    a2.set_xlim(-0.6, 0.6)
    a2.set_ylim(0, 1)
    a2.set_yticks([])
    a2.set_xticks([-0.5, -0.25, 0, 0.25, 0.5])
    a2.set_xticklabels(["-50%", "-25%", "price = value", "+25%", "+50%"], fontsize=7)
    a2.set_title(f"Price is {price / fair * 100 - 100:+.0f}% from the blended fair value", fontsize=8.5, loc="left")
    for sp in ("top", "right", "left"):
        a2.spines[sp].set_visible(False)
    X.fig(fig)
    t = [["Method", "Low", "Middle", "High", "Middle vs price", "Basis"]]
    for m in ms:
        t.append([_s(m["name"] + (" (excluded from blend: far from the others)" if m["out"] else "")), f"${m['low']:,.2f}", f"<b>${m['mid']:,.2f}</b>", f"${m['high']:,.2f}",
                  f"<font color='{GREEN if m['mid'] >= price else RED}'><b>{(m['mid'] / price - 1) * 100:+.0f}%</b></font>", _s(m["basis"][:60])])
    t.append(["<b>Blended (weighted)</b>", "", f"<b>${fair:,.2f}</b>", "", f"<font color='{GREEN if up >= 0 else RED}'><b>{up:+.0f}%</b></font>", f"{len(used)} methods; spread of middles {cv * 100:.0f}%"])
    X.add(_dtable(t, [48 * mm, 18 * mm, 20 * mm, 18 * mm, 24 * mm, 46 * mm], esc=False, bold_first=False))
    X.sp(3)
    X.text(f"<b>{_s(verdict)}.</b> The blended estimate is <b>${fair:,.2f}</b> against a price of <b>${price:,.2f}</b> ({up:+.0f}%). "
           + ("The methods agree closely, which raises confidence in the range." if cv < 0.25 else "The methods disagree widely, so treat the blend as a rough centre of a wide range, not a precise value.")
           + (" Earnings-based methods do not apply to a loss-making company, so this rests on revenue multiples, a cash-flow model and analyst targets: low confidence." if not any(m["name"].startswith(("Peer P/E", "Peer EV/EBITDA", "Graham")) for m in ms) else ""))
    X.note("An estimate, not advice. It averages the middle of each available method (scenario DCF, peer P/E, peer EV/EBITDA, peer price/sales, analyst targets, Graham number); the peer methods use Yahoo's 'similar companies', which are not a curated comp set, and a method is left out when its inputs are missing or negative. Models cannot see news, management quality or regulation.")
    X.data("Fair value methods", ["Method", "Low", "Middle", "High", "Weight", "Basis"], [[m["name"], m["low"], m["mid"], m["high"], m["w"], m["basis"]] for m in ms] + [["Blended", None, fair, None, None, f"{len(ms)} methods"]])
    X.kpi("Blended fair value", f"${fair:,.2f} ({up:+.0f}%)", "bad" if up < -20 else None)
    X.flag(tone, f"Blended fair-value estimate ${fair:,.2f} is {up:+.0f}% vs the price ({len(ms)} methods)", "Valuation")
    X.source("Fair value blend", "computed here from peers, analyst targets, a scenario DCF and fundamentals")


# ------------------------------------------------------------------ Sankey
def _sankey(ax, cols: list[list[dict[str, Any]]], links: list[tuple[str, str, float, str]]) -> None:
    tot = [sum(n["v"] for n in c) for c in cols]
    mx = max(tot)
    gap = mx * 0.05
    height = [t + gap * (len(c) - 1) for t, c in zip(tot, cols)]
    top = max(height)
    pos: dict[str, tuple[float, float, float]] = {}  # id -> (x, y_top, value)
    for ci, c in enumerate(cols):
        cur = top - (top - height[ci]) / 2
        for n in c:
            pos[n["id"]] = (ci, cur, n["v"])
            cur -= n["v"] + gap
    nw = 0.045
    out_off = {k: 0.0 for k in pos}
    in_off = {k: 0.0 for k in pos}
    for s, d, v, col in links:
        if v <= 0:
            continue
        sx, sy, _sv = pos[s]
        dx, dy, _dv = pos[d]
        y0t, y1t = sy - out_off[s], dy - in_off[d]
        out_off[s] += v
        in_off[d] += v
        x0, x1 = sx + nw, dx
        xm = (x0 + x1) / 2
        verts = [(x0, y0t), (xm, y0t), (xm, y1t), (x1, y1t), (x1, y1t - v), (xm, y1t - v), (xm, y0t - v), (x0, y0t - v), (x0, y0t)]
        codes = [MPath.MOVETO, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.LINETO, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MPath(verts, codes), facecolor=col, edgecolor="none", alpha=0.42))
    for c in cols:
        prev_y = None
        for n in c:
            x, y, v = pos[n["id"]]
            ax.add_patch(Polygon([(x, y), (x + nw, y), (x + nw, y - v), (x, y - v)], closed=True, facecolor=n["color"], edgecolor="none"))
            last = x == len(cols) - 1
            ty = y - v / 2
            if prev_y is not None and prev_y - ty < top * 0.085:  # keep labels of thin neighbouring nodes from overprinting
                ty = prev_y - top * 0.085
            prev_y = ty
            ax.text(x + nw + 0.02 if not last else x - 0.02, ty, n["label"], va="center", ha="left" if not last else "right", fontsize=6.8, color=DARK, linespacing=1.15)
    ax.set_xlim(-0.05, len(cols) - 1 + 0.55)
    ax.set_ylim(0, top * 1.02)
    ax.axis("off")


def _fy(X: Ctx) -> tuple[dict[str, dict[str, float]], str] | None:
    ann = (X.D.get("sec") or {}).get("annual") or {}
    rev = ann.get("revenue") or {}
    if not rev:
        return None
    end = max(rev)
    return ann, end


def sankey_model(X: Ctx) -> dict[str, Any] | None:
    """Nodes and links of the income-statement Sankey for the latest fiscal year."""
    got = _fy(X)
    if not got:
        return None
    ann, end = got
    g = lambda k: (ann.get(k) or {}).get(end)  # noqa: E731
    rev = g("revenue")
    gp = g("gross") if g("gross") is not None else (rev - g("cogs") if g("cogs") is not None else None)
    opinc = g("opinc")
    if not rev or rev <= 0 or gp is None or opinc is None:
        return None
    gp = max(0.0, min(gp, rev))
    cogs = rev - gp
    rnd, sga = max(0.0, g("rnd") or 0.0), max(0.0, g("sga") or 0.0)
    opex = gp - opinc  # total operating expenses implied by the reported operating income
    if opex <= 0:
        return None
    other = max(0.0, opex - rnd - sga)
    if rnd + sga > opex:
        s = opex / (rnd + sga)
        rnd, sga, other = rnd * s, sga * s, 0.0
    pct = lambda v: f"{v / rev * 100:.0f}%"  # noqa: E731
    lbl = lambda name, v: f"{name}\n{_usd(v)}  ({pct(v)})"  # noqa: E731
    loss = opinc < 0
    gain = max(opinc, 0.0)
    burn = max(-opinc, 0.0)
    c0 = [{"id": "rev", "v": rev, "color": BLUE, "label": lbl("Revenue", rev)}]
    c1 = [{"id": "gp", "v": gp, "color": GREEN, "label": lbl("Gross profit", gp)}]
    if cogs > 0:
        c1.append({"id": "cogs", "v": cogs, "color": RED, "label": lbl("Cost of revenue", cogs)})
    c2: list[dict[str, Any]] = []
    links: list[tuple[str, str, float, str]] = []
    if cogs > 0:
        links.append(("rev", "cogs", cogs, RED))
    links.append(("rev", "gp", gp, GREEN))
    if loss:
        c1.append({"id": "burn", "v": burn, "color": RED, "label": lbl("Operating loss (funded from cash)", burn)})
    for k, v, name in (("rnd", rnd, "R&D"), ("sga", sga, "Sales, general & admin"), ("oth", other, "Other operating costs")):
        if v > 0:
            c2.append({"id": k, "v": v, "color": AMBER, "label": lbl(name, v)})
            links.append(("gp", k, v * (gp / opex if loss else 1.0), AMBER))
            if loss:
                links.append(("burn", k, v - v * (gp / opex), RED))
    if not loss:
        c2.append({"id": "oi", "v": gain, "color": GREEN, "label": lbl("Operating income", gain)})
        links.append(("gp", "oi", gain, GREEN))
    cols = [c0, c1, c2]
    ni, tax, pretax = g("netinc"), g("tax"), g("pretax")
    if not loss and gain > 0 and ni is not None and 0 <= ni <= gain:
        tx = max(0.0, min(tax or 0.0, gain - ni))
        nonop = max(0.0, gain - ni - tx)
        c3 = [{"id": "ni", "v": ni, "color": GREEN, "label": lbl("Net income", ni)}]
        links.append(("oi", "ni", ni, GREEN))
        if tx > 0:
            c3.append({"id": "tax", "v": tx, "color": RED, "label": lbl("Income tax", tx)})
            links.append(("oi", "tax", tx, RED))
        if nonop > 0:
            c3.append({"id": "nonop", "v": nonop, "color": GREY, "label": lbl("Interest and other", nonop)})
            links.append(("oi", "nonop", nonop, GREY))
        cols.append(c3)
    # the gross-profit node feeds only the part of opex it can cover; sanity: every node must be fed
    cols = [c for c in cols if c]
    return {"cols": cols, "links": links, "end": end, "loss": loss, "rev": rev, "cogs": cogs, "gp": gp, "rnd": rnd, "sga": sga, "other": other, "opinc": opinc, "ni": ni}


def profit_sankey(X: Ctx) -> None:
    m = sankey_model(X)
    if not m:
        return
    cols, links, end, loss, rev, cogs, gp, rnd, sga, other, opinc, ni = m["cols"], m["links"], m["end"], m["loss"], m["rev"], m["cogs"], m["gp"], m["rnd"], m["sga"], m["other"], m["opinc"], m["ni"]
    X.title("WHERE THE REVENUE GOES: INCOME STATEMENT FLOW")
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    try:
        _sankey(ax, cols, links)
    except KeyError:
        plt.close(fig)
        return
    ax.set_title(f"{X.sym}: fiscal year ending {end} (each ribbon is a share of revenue)", fontsize=8.5, loc="left")
    X.fig(fig)
    X.note("Sankey diagram of the latest fiscal-year income statement from SEC filings: revenue splits into the cost of making the product and gross profit; gross profit is then spent on R&D, selling and administration and other costs; what is left is operating income, which pays tax and interest before it becomes net income." + (" This company's operating costs exceed its gross profit, so the shortfall (red) is an operating loss funded from cash or financing." if loss else ""))
    X.data("Income statement flow", ["Item", "USD", "% of revenue"], [["Revenue", rev, 1.0], ["Cost of revenue", cogs, cogs / rev], ["Gross profit", gp, gp / rev], ["R&D", rnd, rnd / rev], ["SG&A", sga, sga / rev], ["Other operating costs", other, other / rev], ["Operating income", opinc, opinc / rev], ["Net income", ni, (ni or 0) / rev]])
    X.source("Income statement flow", f"SEC XBRL, fiscal year ending {end}")


def funnel_stages(X: Ctx) -> dict[str, Any] | None:
    """Revenue-to-free-cash-flow stages for the profit funnel."""
    got = _fy(X)
    if not got:
        return None
    ann, end = got
    g = lambda k: (ann.get(k) or {}).get(end)  # noqa: E731
    rev = g("revenue")
    if not rev or rev <= 0:
        return None
    gp = g("gross") if g("gross") is not None else (rev - g("cogs") if g("cogs") is not None else None)
    stages = [("Revenue", rev)]
    for name, v in (("Gross profit", gp), ("Operating income", g("opinc")), ("Net income", g("netinc")), ("Free cash flow", g("fcf"))):
        if v is not None:
            stages.append((name, v))
    if len(stages) < 3:
        return None
    return {"stages": stages, "end": end, "rev": rev}


def profit_funnel(X: Ctx) -> None:
    m = funnel_stages(X)
    if not m:
        return
    stages, end, rev = m["stages"], m["end"], m["rev"]
    X.title("PROFIT FUNNEL: FROM EACH $1 OF REVENUE")
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    n = len(stages)
    h = 0.8
    for i, (name, v) in enumerate(stages):
        w = max(abs(v) / rev, 0.012)
        nxt = max(abs(stages[i + 1][1]) / rev, 0.012) if i + 1 < n else w
        y = n - 1 - i
        col = BLUE if i == 0 else (GREEN if v >= 0 else RED)
        ax.add_patch(Polygon([(0.5 - w / 2, y + h / 2), (0.5 + w / 2, y + h / 2), (0.5 + nxt / 2, y - h / 2 + 0.02), (0.5 - nxt / 2, y - h / 2 + 0.02)], closed=True, facecolor=col, alpha=0.78, edgecolor="white"))
        ax.text(0.5, y, f"{name}\n{_usd(v)}  =  {v / rev * 100:.0f}c", ha="center", va="center", fontsize=7.6, color="white" if w > 0.3 else DARK, fontweight="bold")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.6, n - 0.3)
    ax.axis("off")
    ax.set_title(f"{X.sym}: how much of $1 of revenue survives at each stage (fiscal year ending {end})", fontsize=8.5, loc="left")
    X.fig(fig)
    X.note("Each band is as wide as that line is relative to revenue; red means the line is negative (a loss or cash burn). A funnel that narrows slowly is a business that keeps what it earns.")
    X.data("Profit funnel", ["Stage", "USD", "Cents per $1 revenue"], [[nm, v, v / rev * 100] for nm, v in stages])
    X.source("Profit funnel", f"SEC XBRL, fiscal year ending {end}")


def cash_steps(X: Ctx) -> dict[str, Any] | None:
    """Opening cash to closing cash steps for the waterfall."""
    got = _fy(X)
    if not got:
        return None
    ann, end = got
    cash = ann.get("cash") or {}
    ends = sorted(cash)
    if end not in cash or len(ends) < 2 or ends[ends.index(end) - 1 if ends.index(end) else 0] == end:
        return None
    prev_end = ends[ends.index(end) - 1]
    ocf = (ann.get("ocf") or {}).get(end)
    if ocf is None:
        return None
    capex = -abs((ann.get("capex") or {}).get(end) or 0.0)
    bb = -abs((ann.get("buyback") or {}).get(end) or 0.0)
    dv = -abs((ann.get("div") or {}).get(end) or 0.0)
    c0, c1 = cash[prev_end], cash[end]
    other = c1 - c0 - ocf - capex - bb - dv
    steps = [("Opening cash", c0, "total"), ("Operating cash flow", ocf, "d"), ("Capex", capex, "d"), ("Buybacks", bb, "d"), ("Dividends", dv, "d"), ("Debt, equity, acquisitions & other", other, "d"), ("Closing cash", c1, "total")]
    steps = [s for s in steps if s[2] == "total" or abs(s[1]) > 0.002 * max(abs(c0), abs(c1), abs(ocf), 1)]
    return {"steps": steps, "end": end, "prev_end": prev_end}


def cash_waterfall(X: Ctx) -> None:
    m = cash_steps(X)
    if not m:
        return
    steps, end, prev_end = m["steps"], m["end"], m["prev_end"]
    X.title("WHERE THE CASH WENT: CASH WATERFALL")
    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    run = 0.0
    for i, (name, v, kind) in enumerate(steps):
        if kind == "total":
            ax.bar(i, v / 1e6, color=BLUE, alpha=0.85)
            run = v
            ax.text(i, v / 1e6, f"{_usd(v)}", ha="center", va="bottom" if v >= 0 else "top", fontsize=7)
        else:
            ax.bar(i, v / 1e6, bottom=run / 1e6, color=GREEN if v >= 0 else RED, alpha=0.85)
            ax.text(i, (run + v) / 1e6 if v >= 0 else run / 1e6, f"{'+' if v >= 0 else '-'}{_usd(abs(v))}", ha="center", va="bottom", fontsize=7)
            run += v
    ax.set_xticks(range(len(steps)))
    ax.set_xticklabels([s[0].replace(" & ", " &\n").replace("Operating ", "Operating\n").replace("Debt, equity, ", "Debt, equity,\n") for s in steps], fontsize=6.6)
    ax.axhline(0, color=GREY, lw=0.8)
    ax.set_ylabel("$ millions", fontsize=7)
    ax.set_title(f"{X.sym}: change in cash, fiscal year ending {end} (green adds, red spends)", fontsize=8.5, loc="left")
    _style_ax(ax)
    X.fig(fig)
    X.note("Bridge from last year's cash balance to this year's using the cash-flow statement from SEC filings. The last step is whatever else changed cash (borrowing and repaying debt, share issuance, acquisitions, currency): it is the balancing figure, not a reported line.")
    X.data("Cash waterfall", ["Step", "USD"], [[s[0], s[1]] for s in steps])
    X.source("Cash waterfall", f"SEC XBRL cash-flow statements, fiscal years ending {prev_end} and {end}")


# ------------------------------------------------------------------ peers
def peer_visuals(X: Ctx) -> None:
    xr = X.D.get("xrank")
    px = X.D.get("peer_extra") or {}
    subj = rc.val_row(X.D.get("subj_val"))
    pv = {s: rc.val_row(q) for s, q in (px.get("val") or {}).items() if q}
    have_radar = bool(xr and len(xr["metrics"]) >= 5)
    pts = [(s, v) for s, v in [(X.sym, subj)] + list(pv.items()) if v.get("gm") is not None and v.get("rg") is not None and v.get("mcap")]
    have_bub = len(pts) >= 4
    if not have_radar and not have_bub:
        return
    X.title("COMPARISON CHARTS: SEC UNIVERSE AND PEER BUBBLES")
    fig = plt.figure(figsize=(7.4, 3.5))
    if have_radar:
        ms = [m for m in xr["metrics"] if m["metric"] != "Revenue per $ of assets"]
        labels = [m["metric"].replace(" margin", "\nmargin").replace("Return on ", "ROE " if "equity" in m["metric"] else "ROA ").replace("equity", "").replace("assets", "").replace("Operating cash flow", "Op. cash flow").strip() for m in ms]
        labels = [("Low debt" if m["metric"] == "Debt / equity" else lb) for m, lb in zip(ms, labels)]
        va = [100 - m["pct_all"] if m["metric"] == "Debt / equity" else m["pct_all"] for m in ms]
        vb = [(100 - m["pct_band"] if m["metric"] == "Debt / equity" else m["pct_band"]) if m.get("pct_band") is not None else None for m in ms]
        ang = np.linspace(0, 2 * np.pi, len(ms), endpoint=False).tolist()
        ax = fig.add_subplot(1, 2 if have_bub else 1, 1, polar=True)
        ax.set_theta_offset(np.pi / 2)
        ax.set_theta_direction(-1)
        ax.plot(ang + ang[:1], va + va[:1], color=AMBER, lw=1.8, label="vs all SEC filers")
        ax.fill(ang + ang[:1], va + va[:1], color=AMBER, alpha=0.25)
        if all(v is not None for v in vb):
            ax.plot(ang + ang[:1], vb + vb[:1], color=BLUE, lw=1.4, ls="--", label="vs similar size")
        ax.set_xticks(ang)
        ax.set_xticklabels(labels, fontsize=6.3)
        ax.set_ylim(0, 100)
        ax.set_yticks([25, 50, 75])
        ax.set_yticklabels(["25", "50", "75"], fontsize=5.5, color=GREY)
        ax.set_title("Percentile rank (outer = better)", fontsize=8, pad=14)
        ax.legend(fontsize=6.3, frameon=False, loc="lower left", bbox_to_anchor=(-0.18, -0.2))
    if have_bub:
        ax2 = fig.add_subplot(1, 2 if have_radar else 1, 2 if have_radar else 1)
        mc = np.array([v["mcap"] for _s_, v in pts], dtype=float)
        size = 80 + 900 * (np.sqrt(mc) / np.sqrt(mc.max()))
        for (s, v), sz in zip(pts, size):
            me = s == X.sym
            ax2.scatter(v["rg"], v["gm"], s=sz, color=AMBER if me else BLUE, alpha=0.75 if me else 0.4, edgecolor=DARK if me else "none", linewidth=1.2, zorder=3 if me else 2)
            ax2.annotate(s, (v["rg"], v["gm"]), fontsize=7, ha="center", va="center", fontweight="bold" if me else "normal")
        ax2.set_xlabel("revenue growth, % (year over year)", fontsize=7)
        ax2.set_ylabel("gross margin, %", fontsize=7)
        ax2.set_title("Peers: growth vs margin (bubble = market cap)", fontsize=8)
        _style_ax(ax2)
    fig.tight_layout()
    X.fig(fig)
    notes = []
    if have_radar:
        notes.append("The radar plots this company's percentile rank on eight measures against every SEC filer with at least $50M of revenue (solid) and against filers of similar size (dashed); a larger shape is better, and 'Low debt' is the inverse of debt/equity.")
    if have_bub:
        notes.append("The bubble chart places the company among Yahoo's 'similar companies' by growth and gross margin: up and to the right is the best corner.")
    X.note(" ".join(notes))
    if have_bub:
        X.data("Peer bubble data", ["Symbol", "Revenue growth %", "Gross margin %", "Market cap"], [[s, v["rg"], v["gm"], v["mcap"]] for s, v in pts])
    X.source("Comparison charts", "SEC XBRL frames and Yahoo peer data")


def multiples_chart(X: Ctx) -> None:
    subj = rc.val_row(X.D.get("subj_val"))
    px = X.D.get("peer_extra") or {}
    pv = {s: rc.val_row(q) for s, q in (px.get("val") or {}).items() if q}
    if not subj or len(pv) < 3:
        return
    rows = [r for r in (rc.valuation_compare(subj, pv) or []) if r.get("fmt") == "x" and r.get("prem") is not None]
    if len(rows) < 2:
        return
    X.title("VALUATION PREMIUM OR DISCOUNT TO PEERS")
    fig, ax = plt.subplots(figsize=(7.4, 0.7 + 0.45 * len(rows)))
    labs = [r["label"] for r in rows][::-1]
    vals = [max(-100, min(300, r["prem"])) for r in rows][::-1]
    ax.barh(labs, vals, color=[RED if v > 0 else GREEN for v in vals], alpha=0.8)
    for i, (v, r) in enumerate(zip(vals, rows[::-1])):
        ax.text(v + (6 if v >= 0 else -6), i, f"{r['prem']:+.0f}%  ({r['subject']:.1f}x vs {r['median']:.1f}x)", va="center", ha="left" if v >= 0 else "right", fontsize=7)
    ax.axvline(0, color=DARK, lw=0.9)
    ax.set_xlim(-140, 380)
    ax.set_title(f"{X.sym}: multiple vs peer median (red = pricier than peers, green = cheaper)", fontsize=8.5, loc="left")
    _style_ax(ax)
    X.fig(fig)
    X.note("Each bar is how far this company's multiple sits above or below the median of its peers (only where both are positive); bars are capped at +300% and -100%. A premium can be earned by faster growth or better margins, so read it with the comparison charts above.")
    X.source("Multiples premium", "Yahoo valuation data, peer median")
