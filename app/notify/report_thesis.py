"""Thesis-style /report sections: scenario valuation, DCF sensitivity, catalyst calendar, competitive-position
scorecard, governance, plus sections rendered from an analyst-supplied inputs file.

Two kinds of content, kept strictly apart:
  * MODEL sections (scenarios, sensitivity, catalysts, competitive position, governance) are computed only
    from data the report already fetched, with every assumption printed next to the result. They are
    illustrative scenario maths, not forecasts or price targets from an analyst.
  * INPUT sections (segment model, sum-of-the-parts, regulatory watch, channel checks, governance notes) have
    no free data source - deliveries per model, ASP, segment multiples, NHTSA status, registrations and
    surveys come from research. They render only from data_cache/thesis_inputs/<SYMBOL>.json, and a row
    without a "source" is dropped, so nothing here is ever invented. See thesis_inputs/_TEMPLATE.json.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics
from pathlib import Path
from typing import Any

from reportlab.lib.units import mm

from app.notify import report_calc as rc
from app.notify.report_sections import Ctx
from app.notify.report_ui import _big, _dtable, _s, _usd

INPUT_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache" / "thesis_inputs"
ERP, TAX, TERM_G = 0.05, 0.21, 0.03
SCENARIOS = {  # name: (revenue case, growth-fade factors for years 3-5, margin multiplier, WACC add-on, default probability)
    "Bear": ("low", (0.50, 0.30, 0.20), 0.5, 0.010, 0.25),
    "Base": ("avg", (0.75, 0.55, 0.40), 1.0, 0.000, 0.50),
    "Bull": ("high", (0.90, 0.75, 0.60), 1.4, -0.005, 0.25),
}
GREEN, RED = "#15803d", "#b91c1c"


def _inputs(sym: str) -> dict[str, Any]:
    try:
        return json.loads((INPUT_DIR / f"{sym.upper().replace('-', '_')}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sourced(rows: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], int]:
    rows = rows or []
    ok = [r for r in rows if isinstance(r, dict) and str(r.get("source") or "").strip()]
    return ok, len(rows) - len(ok)


def _omitted_note(X: Ctx, n: int) -> None:
    if n:
        X.note(f"{n} supplied row(s) were left out because they have no source.")


# ------------------------------------------------------------------ scenario model
def _street(X: Ctx) -> dict[str, dict[str, Any]]:
    out = {}
    for t in ((X.D.get("raw") or {}).get("earningsTrend") or {}).get("trend") or []:
        est = t.get("revenueEstimate") or {}
        if t.get("period") in ("0y", "+1y") and est.get("avg"):
            out[t["period"]] = {k: est.get(k) or est["avg"] for k in ("avg", "low", "high")}
    return out


def _model_inputs(X: Ctx) -> dict[str, Any] | None:
    st = X.sec.get("stats") or {}
    shares, price, rev = st.get("shares_out"), X.price, st.get("revenue")
    street = _street(X)
    if not shares or not price or not (rev or street):
        return None
    fcf_m = st["fcf"] / rev if st.get("fcf") is not None and rev and rev > 0 else None
    op_m = st.get("op_margin")
    m0 = fcf_m if fcf_m is not None and -0.5 <= fcf_m <= 0.4 else (op_m if op_m is not None else 0.0)
    m0 = max(-0.5, min(0.4, m0))
    peers = {s: rc.val_row(q) for s, q in ((X.D.get("peer_extra") or {}).get("val") or {}).items() if q}
    peer_pm = [p["pm"] / 100 for p in peers.values() if p.get("pm") is not None]
    peer_med = statistics.median(peer_pm) if peer_pm else None
    if fcf_m is not None and 0.03 < fcf_m <= 0.4:
        target, tsrc = fcf_m, "its own current free-cash-flow margin"
    elif peer_med is not None and peer_med > 0.03:
        target, tsrc = min(peer_med, 0.35), "the peer-median profit margin"
    else:
        target, tsrc = 0.10, "a 10% default (no profitable reference available)"
    tnx = (X.D.get("h_tnx") or {}).get("candles") or []
    rf = tnx[-1][4] / 100 if tnx and tnx[-1][4] else 0.045
    beta = max(0.6, min(2.5, st.get("beta") or 1.0))
    mcap = X.sec.get("market_cap") or price * shares
    debt, cash = st.get("total_debt") or 0.0, st.get("total_cash") or 0.0
    ke = rf + beta * ERP
    wacc = (mcap / (mcap + debt)) * ke + (debt / (mcap + debt)) * (rf + 0.02) * (1 - TAX)
    ov = {k: v for k, v in (_inputs(X.sym).get("scenario_overrides") or {}).items() if v is not None}  # null = not overridden
    return {"price": price, "shares": shares, "street": street, "rev_ttm": rev, "g_ttm": max(-0.2, min(1.0, st.get("rev_growth") or 0.05)), "m0": m0,
            "target": ov.get("target_margin_base", target), "tsrc": "an input override" if "target_margin_base" in ov else tsrc,
            "wacc": ov.get("wacc", max(0.07, min(0.18, wacc))), "g": ov.get("terminal_growth", TERM_G), "cash": cash, "debt": debt, "rf": rf, "beta": beta,
            "probs": ov.get("probabilities") or {}}


def _rev_path(inp: dict[str, Any], mode: str, fade: tuple[float, ...], growth_mult: float = 1.0) -> list[float]:
    s = inp["street"]
    if "0y" in s and "+1y" in s:
        r1, r2 = s["0y"][mode], max(s["+1y"][mode], 0.0)
    else:
        r1 = (inp["rev_ttm"] or 0.0) * (1 + inp["g_ttm"])
        r2 = r1 * (1 + inp["g_ttm"] * 0.7)
    g2 = r2 / r1 - 1 if r1 > 0 else 0.1
    revs = [r1, r2]
    for f in fade:
        revs.append(revs[-1] * (1 + (max(g2 * f * growth_mult, TERM_G) if g2 > TERM_G else TERM_G)))
    return revs


def _value(inp: dict[str, Any], mode: str, fade: tuple[float, ...], target: float, r: float, g: float, growth_mult: float = 1.0) -> tuple[float, list[float]]:
    revs = _rev_path(inp, mode, fade, growth_mult)
    pv = fcf5 = 0.0
    for t, rev in enumerate(revs, 1):
        fcf5 = rev * (inp["m0"] + (target - inp["m0"]) * t / 5)
        pv += fcf5 / (1 + r) ** t
    rr = max(r, g + 0.02)
    ev = pv + fcf5 * (1 + g) / (rr - g) / (1 + r) ** 5
    return max((ev + inp["cash"] - inp["debt"]) / inp["shares"], 0.0), revs


def _scenarios(inp: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, (mode, fade, mm_, wadd, p0) in SCENARIOS.items():
        target, r = inp["target"] * mm_, inp["wacc"] + wadd
        val, revs = _value(inp, mode, fade, target, r, inp["g"])
        out.append({"name": name, "revs": revs, "target": target, "r": r, "val": val, "p": inp["probs"].get(name, p0)})
    tot = sum(s["p"] for s in out) or 1.0
    for s in out:
        s["p"] /= tot
    return out


def scenarios(X: Ctx) -> None:
    inp = _model_inputs(X)
    if not inp:
        return
    sc = _scenarios(inp)
    price = inp["price"]
    pw = sum(s["val"] * s["p"] for s in sc)
    X.title("SCENARIO VALUATION: BEAR / BASE / BULL (ILLUSTRATIVE MODEL)")
    t = [["", "Bear", "Base", "Bull"]]
    by = {s["name"]: s for s in sc}
    rowf = [("Revenue, year 1 (street case)", lambda s: _usd(s["revs"][0])), ("Revenue, year 2", lambda s: _usd(s["revs"][1])), ("Revenue, year 5", lambda s: _usd(s["revs"][4])),
            ("Target free-cash-flow margin (year 5)", lambda s: f"{s['target'] * 100:.1f}%"), ("Discount rate", lambda s: f"{s['r'] * 100:.1f}%"),
            ("Value per share", lambda s: f"<b>${s['val']:,.2f}</b>"),
            ("vs current price", lambda s: f"<font color='{GREEN if s['val'] >= price else RED}'><b>{(s['val'] / price - 1) * 100:+.0f}%</b></font>"),
            ("Weight (illustrative)", lambda s: f"{s['p'] * 100:.0f}%")]
    for label, f in rowf:
        t.append([label] + [f(by[n]) for n in ("Bear", "Base", "Bull")])
    X.add(_dtable(t, [66 * mm, 36 * mm, 36 * mm, 36 * mm], esc=False, bold_first=True))
    tg = X.sec.get("target") or {}
    X.sp(4)
    X.add(_dtable([["Reference", "Value", "vs price"],
                   ["Probability-weighted model value", f"<b>${pw:,.2f}</b>", f"{(pw / price - 1) * 100:+.0f}%"],
                   ["Current price", f"${price:,.2f}", ""]] +
                  ([[f"Street price targets ({tg.get('n') or '?'} analysts): low / median / high", f"${tg['low']:,.2f} / ${tg.get('median') or tg['mean']:,.2f} / ${tg['high']:,.2f}",
                     f"{(tg['low'] / price - 1) * 100:+.0f}% / {((tg.get('median') or tg['mean']) / price - 1) * 100:+.0f}% / {(tg['high'] / price - 1) * 100:+.0f}%"]] if tg.get("low") and tg.get("high") else []),
                  [100 * mm, 44 * mm, 30 * mm], esc=False, bold_first=True))
    X.note(f"Five-year discounted-cash-flow model. Revenue for years 1-2 is the analyst consensus (low / average / high for bear / base / bull), then growth fades. Free-cash-flow margin moves "
           f"linearly from {inp['m0'] * 100:.0f}% today to the target, set from {inp['tsrc']} (bear x0.5, bull x1.4). Discount rate {inp['wacc'] * 100:.1f}% (risk-free {inp['rf'] * 100:.1f}% + beta "
           f"{inp['beta']:.2f} x 5% equity premium, blended with after-tax debt cost), terminal growth {inp['g'] * 100:.1f}%. The weights are illustrative, not probabilities anyone has estimated. "
           f"This is scenario arithmetic from the stated assumptions, not a forecast or a price target.")
    X.data("Scenario valuation", ["Scenario", "Rev Y1", "Rev Y2", "Rev Y5", "Target FCF margin", "Discount rate", "Value/share", "Weight"],
           [[s["name"], s["revs"][0], s["revs"][1], s["revs"][4], s["target"], s["r"], s["val"], s["p"]] for s in sc])
    X.kpi("Model value / price", f"{pw / price:.2f}x", "bad" if pw < price * 0.6 else "good" if pw > price * 1.2 else None)
    if price > by["Bull"]["val"] * 1.1:
        X.flag("bad", f"Price ${price:,.2f} is above the bull-case model value ${by['Bull']['val']:,.2f}", "Valuation")
    elif price < by["Bear"]["val"] * 0.9:
        X.flag("good", f"Price ${price:,.2f} is below the bear-case model value ${by['Bear']['val']:,.2f}", "Valuation")


def sensitivity(X: Ctx) -> None:
    inp = _model_inputs(X)
    if not inp:
        return
    mode, fade, _, _, _ = SCENARIOS["Base"]
    price, r0, g0, tm = inp["price"], inp["wacc"], inp["g"], inp["target"]

    def cell(v: float) -> str:
        return f"<font color='{GREEN if v >= price else RED}'><b>${v:,.2f}</b></font>"
    X.title("SENSITIVITY OF THE BASE-CASE VALUE PER SHARE")
    gs = [g0 - 0.01, g0 - 0.005, g0, g0 + 0.005, g0 + 0.01]
    t = [["Discount rate \\ terminal growth"] + [f"{g * 100:.1f}%" for g in gs]]
    raw = []
    for r in [r0 - 0.02, r0 - 0.01, r0, r0 + 0.01, r0 + 0.02]:
        vals = [_value(inp, mode, fade, tm, r, g)[0] for g in gs]
        t.append([f"{r * 100:.1f}%"] + [cell(v) for v in vals])
        raw.append([r] + vals)
    X.add(_dtable(t, [54 * mm] + [24 * mm] * 5, esc=False, bold_first=True))
    X.data("Sensitivity WACC x growth", ["Discount rate"] + [f"g={g:.3f}" for g in gs], raw)
    X.sp(6)
    ms, gm = [0.5, 0.75, 1.0, 1.25, 1.5], [0.6, 0.8, 1.0, 1.2, 1.4]
    t2 = [["Target FCF margin \\ revenue growth after year 2"] + [f"x{m:.1f}" for m in gm]]
    raw2 = []
    for m in ms:
        vals = [_value(inp, mode, fade, tm * m, r0, g0, growth_mult=x)[0] for x in gm]
        t2.append([f"{tm * m * 100:.1f}%"] + [cell(v) for v in vals])
        raw2.append([tm * m] + vals)
    X.add(_dtable(t2, [54 * mm] + [24 * mm] * 5, esc=False, bold_first=True))
    X.data("Sensitivity margin x growth", ["Target FCF margin"] + [f"growth x{x}" for x in gm], raw2)
    X.note(f"Green = above the current price (${price:,.2f}), red = below. Centre cells are the base case. Table 1 varies the discount rate and terminal growth; table 2 varies the long-run "
           f"free-cash-flow margin and how fast revenue growth fades (x1.0 = base fade).")


# ------------------------------------------------------------------ inputs-driven sections
def segment_model(X: Ctx) -> None:
    sm = _inputs(X.sym).get("segment_model") or {}
    rows, dropped = _sourced(sm.get("segments"))
    if not rows:
        return
    X.title(f"BOTTOM-UP REVENUE MODEL ({_s(sm.get('period') or 'analyst inputs')})")
    t = [["Segment", "Volume", "Avg. price / unit", "Revenue", "Mix", "Source"]]
    tot, calc = 0.0, []
    for r in rows:
        rev = r["revenue"] if r.get("revenue") is not None else (r.get("units") or 0) * (r.get("asp") or 0)
        calc.append((r, rev))
        tot += rev
    others, d2 = _sourced(sm.get("other_revenue"))
    dropped += d2
    for r in others:
        calc.append((r, r.get("value") or 0))
        tot += r.get("value") or 0
    for r, rev in calc:
        t.append([_s(r.get("name")), _big(r.get("units")) if r.get("units") else "", _usd(r.get("asp")) if r.get("asp") else "", _usd(rev), f"{rev / tot * 100:.0f}%" if tot else "", _s(str(r.get("source"))[:40])])
    t.append(["<b>Total</b>", "", "", f"<b>{_usd(tot)}</b>", "100%", ""])
    X.add(_dtable(t, [38 * mm, 22 * mm, 28 * mm, 30 * mm, 14 * mm, 42 * mm], esc=False))
    st = _street(X).get("0y")
    if st and tot:
        X.note(f"Model total {_usd(tot)} vs street revenue estimate for the current year {_usd(st['avg'])} ({(tot / st['avg'] - 1) * 100:+.0f}%).")
    _omitted_note(X, dropped)
    X.data("Bottom-up model", ["Segment", "Volume", "ASP", "Revenue", "Source"], [[r.get("name"), r.get("units"), r.get("asp"), rev, r.get("source")] for r, rev in calc])
    X.source("Bottom-up revenue model", "analyst-supplied inputs, each row sourced (data_cache/thesis_inputs)")


def sotp(X: Ctx) -> None:
    sp = _inputs(X.sym).get("sotp") or {}
    rows, dropped = _sourced(sp.get("segments"))
    if not rows:
        return
    st = X.sec.get("stats") or {}
    shares = sp.get("shares") or st.get("shares_out")
    net_debt = sp["net_debt"] if sp.get("net_debt") is not None else (st.get("total_debt") or 0) - (st.get("total_cash") or 0)
    if not shares or not X.price:
        return
    X.title("SUM-OF-THE-PARTS VALUATION (ANALYST INPUTS)")
    t = [["Business", "Metric", "Value", "Multiple", "Enterprise value", "Source"]]
    ev = 0.0
    for r in rows:
        e = (r.get("value") or 0) * (r.get("multiple") or 0)
        ev += e
        t.append([_s(r.get("name")), _s(r.get("metric")), _usd(r.get("value")), f"{r.get('multiple'):.1f}x" if r.get("multiple") else "", _usd(e), _s(str(r.get("source"))[:34])])
    eq = ev - net_debt
    ps = eq / shares
    t += [["<b>Sum of the parts</b>", "", "", "", f"<b>{_usd(ev)}</b>", ""], ["Less: net debt", "", "", "", _usd(net_debt), ""], ["<b>Equity value</b>", "", "", "", f"<b>{_usd(eq)}</b>", ""],
          ["<b>Per share</b>", "", "", "", f"<b>${ps:,.2f}</b> ({(ps / X.price - 1) * 100:+.0f}% vs price)", ""]]
    X.add(_dtable(t, [40 * mm, 24 * mm, 26 * mm, 18 * mm, 40 * mm, 26 * mm], esc=False))
    X.sp(5)
    scales = [0.6, 0.8, 1.0, 1.2, 1.4]
    vals = [(ev * s - net_debt) / shares for s in scales]
    X.add(_dtable([["All multiples scaled by"] + [f"x{s:.1f}" for s in scales], ["Value per share"] + [f"<font color='{GREEN if v >= X.price else RED}'><b>${v:,.2f}</b></font>" for v in vals]],
                  [50 * mm] + [24 * mm] * 5, esc=False, bold_first=True))
    _omitted_note(X, dropped)
    X.data("SOTP", ["Business", "Metric", "Value", "Multiple", "EV", "Source"], [[r.get("name"), r.get("metric"), r.get("value"), r.get("multiple"), (r.get("value") or 0) * (r.get("multiple") or 0), r.get("source")] for r in rows])
    X.kpi("SOTP value / price", f"{ps / X.price:.2f}x", "bad" if ps < X.price * 0.6 else "good" if ps > X.price * 1.2 else None)
    X.source("Sum-of-the-parts", "analyst-supplied segment metrics and multiples, each row sourced")


def analyst_inputs(X: Ctx) -> None:
    inp = _inputs(X.sym)
    for key, title, cols, widths in (
            ("regulatory", "REGULATORY AND POLICY WATCH", [("item", "Item"), ("status", "Status"), ("date", "As of"), ("source", "Source")], [50 * mm, 72 * mm, 20 * mm, 32 * mm]),
            ("channel_checks", "CHANNEL CHECKS AND PRIMARY RESEARCH", [("metric", "Metric"), ("value", "Reading"), ("as_of", "As of"), ("source", "Source")], [50 * mm, 62 * mm, 22 * mm, 40 * mm]),
            ("governance_notes", "GOVERNANCE AND KEY-PERSON NOTES", [("topic", "Topic"), ("note", "Note"), ("as_of", "As of"), ("source", "Source")], [36 * mm, 86 * mm, 20 * mm, 32 * mm])):
        rows, dropped = _sourced(inp.get(key))
        if not rows:
            continue
        X.title(title)
        t = [[c[1] for c in cols]] + [[_s(str(r.get(c[0]) or "")[:140]) for c in cols] for r in rows]
        X.add(_dtable(t, widths, right=99, esc=False))
        _omitted_note(X, dropped)
        X.data(title.title(), [c[1] for c in cols], [[r.get(c[0]) for c in cols] for r in rows])
        X.source(title.title(), "analyst-supplied, each row sourced")


# ------------------------------------------------------------------ catalyst calendar
EVENT_RE = re.compile(r"earnings|guidance|launch|unveil|approval|approves|\bfda\b|recall|investigation|probe|lawsuit|offering|partnership|contract|acquisition|merger|conference|investor day|"
                      r"upgrade|downgrade|price target|buyback|dividend|split|delist|bankrupt|regulat", re.I)


def _third_friday(y: int, m: int) -> dt.date:
    first = dt.date(y, m, 1)
    return first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)


def catalysts(X: Ctx) -> None:
    today = dt.date.today()
    ev: list[tuple[dt.date, str, str, str]] = []
    ne = (X.D.get("analysts") or {}).get("next_earnings") or X.sec.get("next_earnings")
    if ne and isinstance(ne[0], (int, float)):
        ev.append((dt.datetime.fromtimestamp(ne[0]).date(), "Earnings report", "Earnings", "Yahoo Finance"))
    exd = (X.sec.get("stats") or {}).get("ex_div")
    if isinstance(exd, (int, float)) and dt.datetime.fromtimestamp(exd).date() >= today:
        ev.append((dt.datetime.fromtimestamp(exd).date(), "Ex-dividend date", "Dividend", "Yahoo Finance"))
    for k in range(4):
        y, m = (today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1)
        d = _third_friday(y, m)
        if d >= today:
            ev.append((d, "Quarterly options expiry (index rebalancing)" if m in (3, 6, 9, 12) else "Monthly options expiry", "Options", "Calendar"))
    try:
        from app import event_blackout

        for e in event_blackout.upcoming(24 * 45):
            d = dt.datetime.fromtimestamp(e["ts"]).date()
            if d >= today:
                ev.append((d, f"US macro release: {e['title']}", "Macro", "Economic calendar"))
    except Exception:  # noqa: BLE001
        pass
    last_q = next((i for i in (X.D.get("filings") or {}).get("items") or [] if i.get("form") in ("10-Q", "10-K")), None)
    if last_q and last_q.get("filed"):
        d = dt.datetime.strptime(last_q["filed"], "%Y-%m-%d").date() + dt.timedelta(days=91)
        if d >= today:
            ev.append((d, f"Next 10-Q/10-K (estimate: last {last_q['form']} filed {last_q['filed']} + ~91 days)", "Filing", "SEC EDGAR (estimate)"))
    inp = _inputs(X.sym)
    rows, dropped = _sourced(inp.get("catalysts"))
    for r in rows:
        try:
            ev.append((dt.datetime.strptime(r["date"], "%Y-%m-%d").date(), str(r.get("event")), str(r.get("type") or "Company"), str(r["source"])))
        except (KeyError, ValueError):
            dropped += 1
    ev = sorted(ev)[:24]
    news = [n for n in (X.D.get("news") or []) if n.get("title") and isinstance(n.get("ts"), (int, float)) and EVENT_RE.search(n["title"])
            and (dt.datetime.now().timestamp() - n["ts"]) < 21 * 86400][:8]
    if not ev and not news:
        return
    X.title("CATALYST CALENDAR AND WATCH LIST")
    if ev:
        t = [["Date", "In", "Event", "Type", "Source"]] + [[d.strftime("%d %b %Y"), f"{(d - today).days}d", _s(e[:78]), _s(k), _s(s)] for d, e, k, s in ev]
        X.add(_dtable(t, [24 * mm, 12 * mm, 84 * mm, 18 * mm, 36 * mm], right=99, esc=False))
        X.data("Catalyst calendar", ["Date", "Days", "Event", "Type", "Source"], [[d.isoformat(), (d - today).days, e, k, s] for d, e, k, s in ev])
        nxt = next(((d, e) for d, e, k, _ in ev if k == "Earnings"), None)
        if nxt:
            X.kpi("Next earnings", f"{nxt[0].strftime('%d %b')} ({(nxt[0] - today).days}d)")
    if news:
        X.sp(6)
        X.text("<b>Catalyst-type headlines, last 3 weeks</b>")
        X.sp(2)
        X.add(_dtable([["Date", "Source", "Headline"]] + [[dt.datetime.fromtimestamp(n["ts"]).strftime("%d %b"), _s(str(n.get("source") or "")[:18]), _s(n["title"][:110])] for n in news],
                      [16 * mm, 30 * mm, 128 * mm], right=99, esc=False))
    _omitted_note(X, dropped)
    X.note("Dated events come from the data sources named in each row; company-specific catalysts (product launches, regulatory decisions) appear only when supplied with a source in the inputs file.")


# ------------------------------------------------------------------ competitive position + governance
def moat(X: Ctx) -> None:
    pv = {s: rc.val_row(q) for s, q in ((X.D.get("peer_extra") or {}).get("val") or {}).items() if q}
    subj = rc.val_row(X.D.get("subj_val"))
    if not subj or len(pv) < 2:
        return
    rows = [r for r in rc.valuation_compare(subj, pv) if r["key"] in ("gm", "pm", "rg", "roe", "mcap") and r["subject"] is not None and r["median"] is not None]
    if not rows:
        return
    X.title("COMPETITIVE POSITION VS PEERS (QUANTITATIVE PROXIES)")
    fm = lambda r, v: _usd(v) if r["fmt"] == "usd" else f"{v:.1f}%"  # noqa: E731
    t = [["Measure", X.sym, "Peer median", "Percentile among peers", "Read"]]
    for r in rows:
        p = r["pctile"]
        read = "above most peers" if p is not None and p >= 67 else "below most peers" if p is not None and p <= 33 else "in line"
        col = GREEN if read.startswith("above") else RED if read.startswith("below") else "#6b7285"
        t.append([_s(r["label"]), fm(r, r["subject"]), fm(r, r["median"]), f"{p:.0f}th" if p is not None else "-", f"<font color='{col}'><b>{read}</b></font>"])
    X.add(_dtable(t, [48 * mm, 28 * mm, 30 * mm, 36 * mm, 32 * mm], esc=False, bold_first=True))
    X.note(f"Compared with {len(pv)} peers ({', '.join(pv)}), which are Yahoo's 'similar companies', not a curated competitor set. These are margin, growth and return proxies for competitive strength; a "
           f"real moat assessment (cost advantage, technology gap, compute, distribution) is qualitative and is not something these numbers can show.")
    X.data("Competitive position", ["Measure", "Subject", "Peer median", "Percentile"], [[r["label"], r["subject"], r["median"], r["pctile"]] for r in rows])


def governance(X: Ctx) -> None:
    prof = X.sec.get("profile") or {}
    offs = prof.get("officers") or []
    if not offs:
        return
    mb = (X.D.get("holders") or {}).get("breakdown") or (X.D.get("keystats") or {}).get("majorHoldersBreakdown") or {}
    ins = mb.get("insidersPercentHeld")
    X.title("MANAGEMENT, OWNERSHIP AND KEY-PERSON INDICATORS")
    t = [["Executive", "Title", "Age", "Reported pay"]] + [[_s(o.get("name")), _s((o.get("title") or "")[:60]), str(o.get("age") or ""), _usd(o.get("pay")) if o.get("pay") else "n/a"] for o in offs[:10]]
    X.add(_dtable(t, [56 * mm, 72 * mm, 14 * mm, 32 * mm], right=2, esc=False))
    kv = []
    if ins is not None:
        kv.append(f"Insiders hold {ins * 100:.1f}% of shares")
    proxy = next((i for i in (X.D.get("filings") or {}).get("items") or [] if str(i.get("form", "")).upper().startswith("DEF 14A")), None)
    if proxy:
        kv.append(f"latest proxy statement (DEF 14A) filed {proxy['filed']} - the source for board composition and pay structure")
    if kv:
        X.sp(4)
        X.text("; ".join(kv) + ".")
    if ins is not None:
        if ins < 0.01:
            X.flag("info", f"Insiders own only {ins * 100:.1f}% of the company", "Ownership")
        elif ins > 0.20:
            X.flag("info", f"Insiders own {ins * 100:.0f}% (concentrated control / key-person exposure)", "Ownership")
    X.note("Yahoo lists executive officers only: board composition, how an executive's time is split across other companies, and pay-package structure are not in the data and need the proxy statement "
           "or analyst notes (add them as sourced rows in the inputs file).")
    X.data("Management", ["Executive", "Title", "Age", "Pay"], [[o.get("name"), o.get("title"), o.get("age"), o.get("pay")] for o in offs[:10]])


def capital_allocation(X: Ctx) -> None:
    """Where the cash goes: SBC, buybacks, dividends and share-count change per fiscal year, from SEC XBRL."""
    ann = (X.D.get("sec") or {}).get("annual") or {}
    rev, shares = ann.get("revenue") or {}, ann.get("shares") or {}
    ends = sorted(set(rev) | set(shares))[-5:]
    if len(ends) < 2 or not any(k in ann for k in ("sbc", "buyback", "div")):
        return
    X.title("CAPITAL ALLOCATION AND DILUTION")
    t = [["Fiscal year end", "SBC", "SBC % revenue", "Buybacks", "Dividends", "FCF", "Diluted shares", "Share chg"]]
    raw = []
    prev = None
    for e in ends:
        sbc, bb, dv, fcf = (ann.get(k, {}).get(e) for k in ("sbc", "buyback", "div", "fcf"))
        sh = shares.get(e)
        chg = (sh / prev - 1) * 100 if sh and prev else None
        pct = sbc / rev[e] * 100 if sbc is not None and rev.get(e) else None
        t.append([_s(e), _usd(sbc) if sbc is not None else "-", f"{pct:.1f}%" if pct is not None else "-", _usd(abs(bb)) if bb is not None else "-",
                  _usd(abs(dv)) if dv is not None else "-", _usd(fcf) if fcf is not None else "-", _big(sh) if sh else "-", f"{chg:+.1f}%" if chg is not None else "-"])
        raw.append([e, sbc, pct, bb, dv, fcf, sh, chg])
        prev = sh or prev
    X.add(_dtable(t, [24 * mm, 20 * mm, 20 * mm, 22 * mm, 22 * mm, 22 * mm, 24 * mm, 18 * mm], esc=False))
    X.note("From SEC XBRL annual filings. High stock-based compensation or a rising share count means existing holders are diluted; buybacks that only offset SBC do not return cash to owners.")
    X.data("Capital allocation", ["FY end", "SBC", "SBC % revenue", "Buybacks", "Dividends", "FCF", "Diluted shares", "Share change %"], raw)
    last = raw[-1]
    if last[2] is not None and last[2] > 10:
        X.flag("bad", f"Stock-based compensation is {last[2]:.0f}% of revenue", "Quality")
    X.source("SEC XBRL capital allocation", "data.sec.gov companyfacts")
