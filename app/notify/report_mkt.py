"""Stock-only /report sections, part 2: options depth, off-exchange flow, macro, filings, news, company."""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Paragraph  # noqa: E402

from app.notify import report_calc as rc  # noqa: E402
from app.notify import report_data as rd  # noqa: E402
from app.notify import report_sec  # noqa: E402
from app.notify.report_sections import Ctx, pc  # noqa: E402
from app.notify.report_ui import BODY, W, _big, _d, _dtable, _kv, _n, _p, _pct_plain, _price, _s, _style_ax, _usd  # noqa: E402

STOP = set("the a an and or of to in on for with as at by from is are was were be been it its this that these those after before over into than will has have had new says say said stock stocks shares share "
           "inc corp co ltd company why how what when who up down more less about vs amid could may might can not no but you your our their his her they we he she out off year years week day today "
           "report reports one two three first second third quarter market".split())


# ------------------------------------------------------------------ options
def _fmt_iv(v: Any) -> str:
    return "-" if not isinstance(v, (int, float)) or v <= 0 or v > 15 else f"{v * 100:.0f}"


def _chain_table(X: Ctx, o: dict[str, Any], label: str, n: int = 24) -> None:
    rows = rc.chain_rows(o, X.price or o.get("price"), n)
    if not rows:
        return
    X.add(Paragraph(f"<b>{_s(label)}</b>", BODY))
    X.sp(2)
    t = [["Strike", "C bid", "C ask", "C vol", "C OI", "C IV%", "P bid", "P ask", "P vol", "P OI", "P IV%"]]
    raw = []
    spot = X.price or o.get("price") or 0
    for k, c, p in rows:
        c, p = c or {}, p or {}
        mark = "<b>" if spot and abs(k / spot - 1) < 0.025 else ""
        end = "</b>" if mark else ""
        t.append([f"{mark}{_price(k)}{end}", _n(c.get("bid")) if c else "-", _n(c.get("ask")) if c else "-", _big(c.get("vol")) if c.get("vol") else "-", _big(c.get("oi")) if c.get("oi") else "-",
                  _fmt_iv(c.get("iv")), _n(p.get("bid")) if p else "-", _n(p.get("ask")) if p else "-", _big(p.get("vol")) if p.get("vol") else "-", _big(p.get("oi")) if p.get("oi") else "-", _fmt_iv(p.get("iv"))])
        raw.append([label, k, c.get("bid"), c.get("ask"), c.get("vol"), c.get("oi"), c.get("iv"), p.get("bid"), p.get("ask"), p.get("vol"), p.get("oi"), p.get("iv")])
    X.add(_dtable(t, [21 * mm] + [15.3 * mm] * 10, esc=False))
    X.sp(5)
    X.data(f"Options {label[:18]}", ["Expiry", "Strike", "C bid", "C ask", "C vol", "C OI", "C IV", "P bid", "P ask", "P vol", "P OI", "P IV"], raw)


def options(X: Ctx) -> None:
    D, price = X.D, X.price
    o1, o2 = D.get("options"), D.get("opt_near")
    ov = D.get("optvol") or {}
    os_ = rd.option_stats(o1)
    if not (os_ or ov.get("expiries")):
        return
    if os_:
        X.title(f"OPTIONS POSITIONING (EXPIRY {_d(os_.get('date'))})")
        kv = [("Put/call ratio (open interest)", _n(os_.get("pc_oi"))), ("Put/call ratio (volume)", _n(os_.get("pc_vol"))), ("Call / put open interest", f"{_big(os_['call_oi'])} / {_big(os_['put_oi'])}"),
              ("Call / put volume", f"{_big(os_['call_vol'])} / {_big(os_['put_vol'])}"), ("ATM implied volatility", _pct_plain(os_.get("iv"), 1.0) if os_.get("iv") is not None else "n/a"),
              ("Implied 1-sigma move", f"+/- {_price(os_['exp_move'])} ({_pct_plain(os_['exp_move'] / price, 100)})" if os_.get("exp_move") and price else "n/a"),
              ("Max pain strike", _price(os_.get("max_pain")) if os_.get("max_pain") is not None else "n/a"), ("Days to expiry", _n(os_.get("dte"), 0))]
        if ov:
            kv += [("IV 30-day", _pct_plain(ov.get("iv30"), 1.0) if ov.get("iv30") is not None else "n/a"), ("IV rank (vs 1y realised range)", _n(ov.get("iv_rank"), 0) if ov.get("iv_rank") is not None else "n/a"),
                   ("IV percentile", _n(ov.get("iv_percentile"), 0) if ov.get("iv_percentile") is not None else "n/a"),
                   ("Realised vol 10d / 30d / 60d", " / ".join(_pct_plain((ov.get("hv") or {}).get(k), 1.0) if (ov.get("hv") or {}).get(k) is not None else "n/a" for k in ("10d", "30d", "60d")))]
        X.add(_kv(kv))
        X.data("Options summary", ["Metric", "Value"], [[k, v] for k, v in kv])
        X.sp(4)
        X.text("<font color='#6b7285'>Top call open interest</font>  <b>" + _s(", ".join(f"{_price(c['strike'])} ({_big(c['oi'])})" for c in os_["top_calls"]) or "n/a") + "</b><br/>"
               "<font color='#6b7285'>Top put open interest</font>  <b>" + _s(", ".join(f"{_price(c['strike'])} ({_big(c['oi'])})" for c in os_["top_puts"]) or "n/a") + "</b>")
        if os_.get("pc_oi"):
            if os_["pc_oi"] > 1.3:
                X.flag("info", f"Put/call open-interest ratio {os_['pc_oi']:.2f}: options positioning leans defensive", "Sentiment")
            elif os_["pc_oi"] < 0.6:
                X.flag("info", f"Put/call open-interest ratio {os_['pc_oi']:.2f}: options positioning leans bullish", "Sentiment")
        if os_.get("iv") and os_["iv"] > 80:
            X.flag("info", f"Implied volatility {os_['iv']:.0f}%: options market expects large moves", "Risk")
        if os_.get("iv") is not None:
            X.kpi("Implied vol (ATM)", f"{os_['iv']:.0f}%")
    ex = ov.get("expiries") or []
    if ex:
        X.sp(6)
        X.add(Paragraph("<b>Implied volatility term structure</b>", BODY))
        X.sp(2)
        t = [["Expiry", "Days", "ATM IV", "Skew (put - call IV)", "Implied move"]] + [[_d(e["expiry"], "%d %b %Y"), str(int(e["days"])), f"{e['atm_iv']:.1f}%" if e.get("atm_iv") is not None else "-",
                                                                                     f"{e['skew']:+.1f}" if e.get("skew") is not None else "-", f"+/- {e['exp_move_pct']:.1f}%" if e.get("exp_move_pct") else "-"] for e in ex]
        X.add(_dtable(t, [32 * mm, 20 * mm, 28 * mm, 50 * mm, 44 * mm]))
        X.data("IV term structure", t[0], t[1:])
        pts = [(e["days"], e["atm_iv"]) for e in ex if e.get("atm_iv") is not None]
        if len(pts) >= 3:
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 2.4), dpi=150)
            a1.plot([d for d, _ in pts], [v for _, v in pts], marker="o", ms=3, lw=1.3, color="#d9830c", label="ATM IV")
            hv = ov.get("hv") or {}
            if hv.get("30d") is not None:
                a1.axhline(hv["30d"] * 100 if hv["30d"] < 5 else hv["30d"], color="#3b82f6", lw=0.9, ls="--", label="30d realised vol")
            a1.set_title("IV term structure (days to expiry)", fontsize=8.5, loc="left")
            a1.legend(fontsize=6.5, frameon=False)
            sf = ov.get("surface") or []
            mn = ov.get("moneyness") or []
            for s in sf[:3]:
                a2.plot([m * 100 for m in mn], s["iv"], marker="o", ms=2.5, lw=1.1, label=f"{int(s['days'])}d")
            a2.axvline(100, color="#14161d", lw=0.6)
            a2.set_title("IV smile by moneyness (% of spot)", fontsize=8.5, loc="left")
            a2.legend(fontsize=6.5, frameon=False)
            _style_ax(a1, a2)
            X.sp(4)
            X.fig(fig)
        if len(pts) >= 2 and pts[0][1] and pts[-1][1] and pts[0][1] > pts[-1][1] * 1.25:
            X.flag("info", f"IV term structure inverted (front {pts[0][1]:.0f}% vs back {pts[-1][1]:.0f}%): near-term event risk priced in", "Risk")
    ks, co, po = rc.oi_profile(o1, price)
    if ks:
        fig, ax = plt.subplots(figsize=(8.6, 2.4), dpi=150)
        wd = (ks[-1] - ks[0]) / max(1, len(ks)) * 0.4
        ax.bar([k - wd / 2 for k in ks], co, width=wd, color="#15803d", label="Call OI")
        ax.bar([k + wd / 2 for k in ks], po, width=wd, color="#b91c1c", label="Put OI")
        if price:
            ax.axvline(price, color="#14161d", lw=0.8, ls="--")
        ax.set_title(f"Open interest by strike, expiry {_d(o1.get('date'), '%d %b %Y')}", fontsize=8.5, loc="left")
        ax.legend(fontsize=6.5, frameon=False)
        _style_ax(ax)
        X.sp(5)
        X.fig(fig)
    uoa = D.get("uoa") or []
    if uoa:
        X.sp(6)
        X.add(Paragraph("<b>Unusual options activity (volume >= 500, premium >= $75K, volume >= 1.5x open interest)</b>", BODY))
        X.sp(2)
        t = [["Type", "Strike", "Expiry", "Volume", "OI", "Vol/OI", "Premium", "IV", "vs spot"]]
        for r in sorted(uoa, key=lambda r: -r["premium"])[:12]:
            col = "#15803d" if r["type"] == "CALL" else "#b91c1c"
            t.append([f"<font color='{col}'><b>{r['type']}</b></font>", _price(r["strike"]), _d(r["expiry"], "%d %b"), _big(r["vol"]), _big(r["oi"]), f"{r['ratio']:.1f}x" if r.get("ratio") else "new",
                      _usd(r["premium"]), f"{r['iv']:.0f}%", _p(r.get("otm_pct"), 0) if r.get("otm_pct") is not None else "-"])
        X.add(_dtable(t, [16 * mm, 20 * mm, 20 * mm, 22 * mm, 20 * mm, 18 * mm, 22 * mm, 16 * mm, 20 * mm], esc=False))
        X.data("Unusual options", ["Type", "Strike", "Expiry", "Volume", "OI", "Vol/OI", "Premium", "IV %", "Vs spot %"], [[r["type"], r["strike"], _d(r["expiry"], "%Y-%m-%d"), r["vol"], r["oi"], r.get("ratio"), r["premium"], r["iv"], r.get("otm_pct")] for r in uoa])
        calls = sum(r["premium"] for r in uoa if r["type"] == "CALL")
        puts = sum(r["premium"] for r in uoa if r["type"] == "PUT")
        if calls + puts > 500_000:
            X.flag("info", f"Unusual options premium: {_usd(calls)} calls vs {_usd(puts)} puts", "Sentiment")
    chains = [(o, lab) for o, lab in ((o2, "Nearest expiry"), (o1, "Main expiry")) if o]
    if len(chains) == 2 and chains[0][0].get("date") == chains[1][0].get("date"):
        chains = chains[:1]
    for o, lab in chains:
        X.sp(6)
        _chain_table(X, o, f"{lab}: {_d(o.get('date'), '%d %b %Y')}, spot {_price(price)}")
    if chains:
        X.note("Strikes nearest the spot price; IV in percent; bold rows are within 2.5% of spot. Yahoo option data can be stale outside market hours.")


# ------------------------------------------------------------------ off-exchange flow
def flow(X: Ctx) -> None:
    ats, sv = X.D.get("ats") or {}, X.D.get("sv") or []
    if not (ats.get("venues") or sv):
        return
    X.title("OFF-EXCHANGE AND SHORT-SALE FLOW")
    if sv:
        rows = [(d, s, t) for d, s, t in sv if t]
        if rows:
            ratios = [s / t * 100 for _, s, t in rows]
            avg5 = sum(ratios[-5:]) / len(ratios[-5:])
            avg20 = sum(ratios) / len(ratios)
            X.add(_kv([("Short-sale volume share (latest)", f"{ratios[-1]:.1f}% of off-exchange volume"), ("5-day average", f"{avg5:.1f}%"), (f"{len(ratios)}-day average", f"{avg20:.1f}%"),
                       ("Latest total volume", f"{_big(rows[-1][2])} sh on {rows[-1][0].strftime('%d %b')}")]))
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 2.2), dpi=150, gridspec_kw={"width_ratios": [1.3, 1]})
            a1.plot([d for d, _, _ in rows], ratios, marker="o", ms=2.5, lw=1.2, color="#d9830c")
            a1.axhline(50, color="#9aa3b5", lw=0.7, ls="--")
            a1.set_title("FINRA short-sale volume / total off-exchange volume (%)", fontsize=8, loc="left")
            a1.tick_params(axis="x", labelrotation=30, labelsize=6.5)
            a2.bar(range(len(rows)), [t / 1e6 for _, _, t in rows], color="#9aa3b5")
            a2.set_title("Off-exchange volume (M shares)", fontsize=8, loc="left")
            _style_ax(a1, a2)
            X.sp(4)
            X.fig(fig)
            X.note("Short-sale volume is not short interest: market makers sell short to provide liquidity, so a share around 40-55% is typical.")
            X.data("Short volume", ["Date", "Short volume", "Total off-exchange volume", "Short %"], [[d.isoformat(), s, t, s / t * 100] for d, s, t in rows])
            if avg5 > 60:
                X.flag("info", f"Short-sale volume {avg5:.0f}% of off-exchange volume (5-day average)", "Sentiment")
    v = ats.get("venues") or []
    if v:
        X.sp(6)
        X.add(Paragraph(f"<b>Dark-pool (ATS) venues, week of {_s(ats.get('week'))}</b>", BODY))
        X.sp(2)
        t = [["Venue", "MPID", "Shares", "Trades", "Notional", "Avg trade", "Share"]] + [[_s((x.get("venue") or "")[:38]), x.get("mpid") or "", _big(x.get("shares")), _big(x.get("trades")), _usd(x.get("notional")),
                                                                                         f"{x.get('avg_size') or 0:,.0f}", f"{x.get('share_pct') or 0:.1f}%"] for x in v]
        X.add(_dtable(t, [60 * mm, 16 * mm, 22 * mm, 20 * mm, 22 * mm, 18 * mm, 16 * mm], right=2, esc=False))
        X.data("Dark pool venues", ["Venue", "MPID", "Shares", "Trades", "Notional", "Avg size", "Share %"], [[x.get("venue"), x.get("mpid"), x.get("shares"), x.get("trades"), x.get("notional"), x.get("avg_size"), x.get("share_pct")] for x in v])
        wk = ats.get("weeks") or []
        if wk:
            X.sp(5)
            t = [["Week", "Shares", "Trades", "Notional", "Avg trade"]] + [[str(w.get("week")), _big(w.get("shares")), _big(w.get("trades")), _usd(w.get("notional")), f"{w.get('avg_size') or 0:,.0f}"] for w in wk]
            X.add(_dtable(t, [34 * mm, 34 * mm, 30 * mm, 38 * mm, 30 * mm]))
            X.data("Dark pool weekly", t[0], t[1:])
        X.note(_s(ats.get("note") or "FINRA ATS transparency data is published with a 2-4 week lag."))
        X.source("FINRA ATS weekly", str(ats.get("week")))


# ------------------------------------------------------------------ macro
def global_macro(X: Ctx) -> None:
    rows = X.D.get("global_macro") or []
    if not rows:
        return
    X.title("GLOBAL RATES AND FX (ECB + BIS)")
    t = [["Indicator", "Latest", "As of", "Previous", "Source"]]
    raw = []
    for r in rows:
        u = r.get("unit") or ""
        t.append([_s(r["label"]), f"<b>{r['value']:,.3f}{u}</b>".replace(".000", ""), _s(str(r.get("date") or "")), f"{r['prev']:,.3f}{u}".replace(".000", "") if r.get("prev") is not None else "-", r.get("source")])
        raw.append([r["label"], r["value"], r.get("prev"), r.get("date"), r.get("source")])
    X.add(_dtable(t, [86 * mm, 28 * mm, 22 * mm, 24 * mm, 14 * mm], esc=False))
    X.note("Official ECB Data API and BIS Statistics API (free, no key). Central-bank policy rates are the BIS series; the dates show how fresh each series is.")
    X.data("Global rates and FX", ["Indicator", "Latest", "Previous", "As of", "Source"], raw)
    X.source("ECB / BIS statistics", "ECB Data API and BIS Statistics API, live at build time")


def cot(X: Ctx) -> None:
    rows = X.D.get("cot") or []
    if not rows:
        return
    X.title("INDEX FUTURES POSITIONING (CFTC COT)")
    t = [["Market", "Report date", "Leveraged funds net", "Change", "Asset managers net", "Change", "Open interest"]]
    raw = []
    for r in rows:
        dl = None if r["lev_prev"] is None else r["lev_net"] - r["lev_prev"]
        da = None if r["am_prev"] is None else r["am_net"] - r["am_prev"]
        t.append([_s(r["market"]), _s(r["date"]), f"<b>{r['lev_net']:+,}</b>", f"{dl:+,}" if dl is not None else "-", f"<b>{r['am_net']:+,}</b>", f"{da:+,}" if da is not None else "-", f"{r['oi']:,}"])
        raw.append([r["market"], r["date"], r["lev_net"], dl, r["am_net"], da, r["oi"]])
    X.add(_dtable(t, [38 * mm, 20 * mm, 28 * mm, 16 * mm, 28 * mm, 16 * mm, 24 * mm], esc=False))
    X.note("CFTC Traders in Financial Futures report, published Fridays for the prior Tuesday. Net = contracts long minus short. Leveraged funds are hedge-fund style traders; asset managers are institutions, usually net long. Market-wide context, not specific to this company.")
    X.data("CFTC COT index futures", ["Market", "Date", "Lev funds net", "Change", "Asset mgr net", "Change", "Open interest"], raw)
    X.source("CFTC COT", f"publicreporting.cftc.gov, report date {rows[0]['date']}")


def ark(X: Ctx) -> None:
    held = X.D.get("ark") or []
    if not held:
        return
    X.title("ARK INVEST HOLDINGS")
    t = [["ARK fund", "Holding", "Shares", "Market value", "Weight in fund", "As of"]]
    for h in held:
        t.append([_s(h["fund"]), _s((h.get("company") or "")[:34]), _big(h.get("shares")), _usd(h.get("value")), f"{h['weight']:.2f}%" if h.get("weight") is not None else "n/a", _s(h.get("date") or "")])
    X.add(_dtable(t, [22 * mm, 56 * mm, 24 * mm, 28 * mm, 24 * mm, 20 * mm], esc=False))
    X.note("From ARK's published daily holdings files (ARKK, ARKW, ARKG, ARKF checked). ARK publishes only the current snapshot, so there is no trade history here; a stale 'as of' date means ARK has not refreshed that file.")
    X.data("ARK holdings", ["Fund", "Company", "Shares", "Market value", "Weight %", "As of"], [[h["fund"], h.get("company"), h.get("shares"), h.get("value"), h.get("weight"), h.get("date")] for h in held])
    top = max(held, key=lambda h: h.get("weight") or 0)
    X.kpi("ARK weight", f"{top['weight']:.1f}% in {top['fund']}")
    X.flag("info", f"Held by ARK: {top['weight']:.1f}% of {top['fund']}", "Ownership")
    X.source("ARK Invest holdings", f"ark-funds.com daily CSV, as of {top.get('date')}")


def macro(X: Ctx) -> None:
    rows = (X.D.get("macro") or {}).get("rows") or []
    if not rows:
        return
    X.title("MACRO BACKDROP (FRED)")

    def fv(r: dict[str, Any]) -> str:
        v = r.get("value")
        if v is None:
            return "n/a"
        if r.get("mode") == "yoy":
            return f"{v:+.1f}% y/y"
        u = r.get("unit") or ""
        return f"{v:,.2f}{u}" if u in ("%", " pts", "") else f"{v:,.2f} {u}".strip()
    t = [["Indicator", "Latest", "As of", "Previous", "Year ago"]]
    raw = []
    for r in rows:
        pv = r.get("prev")
        ya = r.get("year_ago")
        t.append([_s(r.get("label")), f"<b>{_s(fv(r))}</b>", _s(str(r.get("date") or "")[:10]), _n(pv) if pv is not None else "-", _n(ya) if ya is not None else "-"])
        raw.append([r.get("id"), r.get("label"), r.get("value"), r.get("date"), pv, ya])
    X.add(_dtable(t, [70 * mm, 34 * mm, 26 * mm, 22 * mm, 22 * mm], esc=False))
    X.data("Macro", ["Series", "Label", "Value", "Date", "Previous", "Year ago"], raw)
    by = {r["id"]: r for r in rows}
    s = by.get("T10Y2Y")
    if s and s.get("value") is not None and s["value"] < 0:
        X.flag("info", f"Yield curve (10Y-2Y) inverted at {s['value']:.2f}", "Risk")
    hy = by.get("BAMLH0A0HYM2")
    if hy and hy.get("value") is not None and hy["value"] > 5.5:
        X.flag("bad", f"High-yield credit spread wide at {hy['value']:.1f}%", "Risk")
    X.note("Macro data is market-wide context, not specific to this company. Series update on their own release schedules.")


# ------------------------------------------------------------------ filings
def filings(X: Ctx) -> None:
    D = X.D
    fl = D.get("filings") or {}
    items = fl.get("items") or []
    S = D.get("sec") or {}
    events = S.get("events") or []
    if not (items or events):
        return
    X.title("SEC FILINGS")
    cutoff = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    dil = [i for i in items if (i.get("form") or "").upper().startswith(rd.DILUTION_FORMS) and (i.get("filed") or "") >= cutoff]
    if dil:
        X.flag("bad", f"{len(dil)} equity-offering filing(s) (S-3/S-1/424B) in the last 12 months", "Quality")
    if items:
        t = [["Filed", "Form", "Period", "What it is"]] + [[i.get("filed") or "", _s(i.get("form") or ""), i.get("period") or "", _s((i.get("note") or i.get("desc") or "")[:92])] for i in items[:24]]
        X.add(_dtable(t, [22 * mm, 20 * mm, 22 * mm, 110 * mm], right=9, esc=False))
        X.note(f"Offering filings in the last 12 months: {len(dil)}.  Fiscal year end: {_s(_fye(fl.get('fiscal_year_end')))}.  SIC: {fl.get('sic') or 'n/a'}.  CIK: {fl.get('cik') or 'n/a'}.")
        X.data("SEC filings", ["Filed", "Form", "Period", "Description", "URL"], [[i.get("filed"), i.get("form"), i.get("period"), i.get("note") or i.get("desc"), i.get("url")] for i in items])
    fc = S.get("form_counts") or {}
    if fc:
        X.sp(5)
        top = sorted(fc.items(), key=lambda kv: -kv[1])[:10]
        X.add(_kv([(f"Form {k}", f"{v} filings (last 12 months)") for k, v in top], cols=2))
    if events:
        X.sp(6)
        X.add(Paragraph("<b>Material events (Form 8-K) by item</b>", BODY))
        X.sp(2)
        t = [["Filed", "Items", "What it reports"]] + [[e.get("filed") or "", _s(", ".join(e.get("items") or [])), _s((e.get("what") or "")[:150])] for e in events[:24]]
        X.add(_dtable(t, [22 * mm, 26 * mm, 126 * mm], right=9, esc=False))
        X.data("8-K events", ["Filed", "Items", "Description", "URL"], [[e.get("filed"), ", ".join(e.get("items") or []), e.get("what"), e.get("url")] for e in events])
        cnt = Counter(i for e in events for i in e.get("items") or [])
        if cnt:
            X.note("Item frequency: " + ", ".join(f"{k} x{v} ({_s(report_sec.ITEM_TEXT.get(k, ''))[:38]})" for k, v in cnt.most_common(6)))
        if cnt.get("1.03") or cnt.get("4.02") or cnt.get("3.01"):
            X.flag("bad", "8-K items indicate bankruptcy, non-reliance on financials or a listing notice: " + ", ".join(k for k in ("1.03", "4.02", "3.01") if cnt.get(k)), "Quality")
        if cnt.get("4.01"):
            X.flag("info", "Change of auditor disclosed (8-K item 4.01)", "Quality")
        if cnt.get("5.02"):
            X.flag("info", f"{cnt['5.02']} officer/director change(s) disclosed (8-K item 5.02)", "Quality")


def _fye(v: Any) -> str:
    s = str(v or "")
    return f"{MONTHS[int(s[:2]) - 1]} {int(s[2:])}" if re.fullmatch(r"\d{4}", s) else (s or "n/a")


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ------------------------------------------------------------------ dividends
def dividends(X: Ctx) -> None:
    dv = X.D.get("divs") or {}
    if not (dv.get("yearly") or dv.get("splits")):
        return
    X.title("DIVIDENDS AND SPLITS")
    yy = dv.get("yearly") or []
    if yy:
        yy = yy[:8]
        t = [["Year"] + [str(y["year"]) for y in yy], ["Dividends / share"] + [_n(y["total"], 2) for y in yy]]
        X.add(_dtable(t, [34 * mm] + [(W - 34 * mm) / len(yy)] * len(yy), bold_first=True))
        X.data("Dividends", ["Year", "Dividends per share"], [[y["year"], y["total"]] for y in dv["yearly"]])
    sp = dv.get("splits") or []
    if sp:
        X.sp(3)
        X.note("Splits: " + ", ".join(f"{_d(s_['ts'])} ({s_['ratio']})" for s_ in sp[:8]))


# ------------------------------------------------------------------ news
def news(X: Ctx) -> None:
    items = [n for n in (X.D.get("news") or []) if n.get("title") and not re.search(r"[CP]\d{8}\)|Interactive Stock Chart|stock price, news, quote", n["title"])]
    if not items:
        return
    items = items[:30]
    pos, neg, neu = rd.news_tone(items)
    X.title(f"NEWS ({len(items)} LATEST HEADLINES)")
    X.text(f"Headline tone (keyword-based, rough): <font color='#15803d'><b>{pos} positive</b></font>, <font color='#b91c1c'><b>{neg} negative</b></font>, {neu} neutral.")
    src = Counter((n.get("source") or "unknown") for n in items)
    words = Counter(w for n in items for w in set(re.findall(r"[a-z][a-z'-]{3,}", n["title"].lower())) if w not in STOP and w != X.sym.lower())
    X.sp(3)
    X.add(_kv([("Sources", ", ".join(f"{k} ({v})" for k, v in src.most_common(5))), ("Recurring keywords", ", ".join(f"{k} ({v})" for k, v in words.most_common(8) if v >= 2) or "none")], cols=1))
    X.sp(4)
    for n in items:
        X.add(Paragraph(f"<font color='#6b7285'>{_s(_d(n.get('ts'), '%d %b %H:%M'))}  {_s(n.get('source') or '')}</font><br/>{_s(n['title'])}", BODY))
        X.sp(3)
    X.data("News", ["Time", "Source", "Headline", "Link"], [[_d(n.get("ts"), "%Y-%m-%d %H:%M"), n.get("source"), n["title"], n.get("link")] for n in items])
    if neg >= pos + 4:
        X.flag("bad", f"News flow skews negative ({neg} negative vs {pos} positive headlines)", "Sentiment")
    elif pos >= neg + 4:
        X.flag("good", f"News flow skews positive ({pos} positive vs {neg} negative headlines)", "Sentiment")
    X.kpi("News tone", f"{pos}+ / {neg}-", "good" if pos > neg + 2 else "bad" if neg > pos + 2 else None)


# ------------------------------------------------------------------ company
def company(X: Ctx) -> None:
    prof = X.sec.get("profile") or {}
    X.title("COMPANY")
    kv = [("Sector", prof.get("sector") or "n/a"), ("Industry", prof.get("industry") or "n/a"), ("Employees", f"{prof['employees']:,}" if prof.get("employees") else "n/a"),
          ("Country", prof.get("country") or "n/a"), ("Website", prof.get("website") or "n/a"), ("HQ", ", ".join(x for x in (prof.get("city"), prof.get("state")) if x) or "n/a")]
    X.add(_kv(kv))
    X.data("Company", ["Field", "Value"], [[k, v] for k, v in kv])
    offs = prof.get("officers") or []
    if offs:
        X.sp(4)
        X.add(_dtable([["Executive", "Title", "Pay"]] + [[o.get("name") or "", (o.get("title") or "")[:70], _usd(o.get("pay")) if o.get("pay") else ""] for o in offs[:10]], [56 * mm, 98 * mm, 20 * mm], right=2))
    desc = X.sec.get("description")
    if desc:
        X.sp(4)
        txt = re.sub(r"\s+", " ", desc)
        if len(txt) > 1800:
            txt = txt[:1800].rsplit(". ", 1)[0] + "."
        X.text(_s(txt))
