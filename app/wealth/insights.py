"""WEALTH insights — the extra data behind the four WEALTH pages.

outlook()   MONEY page: 12-month main-balance forecast (recurring items + typical card spend), the next 60 days of money events,
            emergency runway, currency exposure, net-worth composition over time and a 0-100 financial health score with the
            reason for every point lost.
holdings()  INVESTMENTS page: per holding 1M / 3M / YTD / 1Y return, distance from the 52-week high and low, volatility, beta
            vs the S&P 500, share of the portfolio's risk and a 90-day sparkline.
All amounts in USD unless the key ends in _ils.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any

import numpy as np

from app import db
from app.wealth import budget, cashflow, core


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _next_on(day: int, today: dt.date) -> dt.date:
    d = min(28, max(1, day))
    cand = today.replace(day=d)
    if cand < today:
        cand = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=d)
    return cand


def outlook() -> dict[str, Any]:
    nw = core.networth()
    fx = nw["usdils"]
    today = dt.datetime.now(core.IL).date()
    cf = cashflow.overview()
    b = budget.summary(6)
    rec = [r for r in cf["recurring"] if r["active"]]
    income_rec = sum(r["amount"] for r in rec if r["amount"] > 0)
    expense_rec = -sum(r["amount"] for r in rec if r["amount"] < 0)
    cc = [h["amount"] for h in cf["cc_history"][:3]]
    cc_avg = sum(cc) / len(cc) if cc else 0.0
    # monthly burn: the bank/card export when there is one, else the fixed items + the typical card answer
    burn = b["avg_expense"] if b.get("count") and b.get("avg_expense") else expense_rec + cc_avg
    income = max(income_rec, b.get("avg_income") or 0.0)
    net_month = income - burn
    bal0 = float(cf["main_balance"]["value"])

    forecast, bal = [], bal0
    for i in range(1, 13):
        m0 = (today.replace(day=1) + dt.timedelta(days=32 * i)).replace(day=1)
        bal += income_rec - expense_rec - cc_avg
        forecast.append({"month": m0.strftime("%Y-%m"), "balance_ils": round(bal, 0), "in_ils": round(income_rec), "out_ils": round(expense_rec + cc_avg)})

    events = []
    for r in rec:
        d = _next_on(int(r["day"] or 1), today)
        for k in range(2):
            dd = d if k == 0 else (d.replace(day=1) + dt.timedelta(days=32)).replace(day=d.day)
            if (dd - today).days <= 60:
                events.append({"day": dd.isoformat(), "title": r["name"], "amount_ils": r["amount"], "kind": "income" if r["amount"] > 0 else "bill"})
    first = _next_on(1, today)
    events.append({"day": first.isoformat(), "title": "Telegram asks: credit-card spend last month", "amount_ils": -round(cc_avg) if cc_avg else None, "kind": "card"})
    events.append({"day": first.isoformat(), "title": "Monthly money report (Telegram)", "amount_ils": None, "kind": "report"})
    sun = today + dt.timedelta(days=(6 - today.weekday()) % 7)
    events.append({"day": sun.isoformat(), "title": "Weekly money report (Telegram)", "amount_ils": None, "kind": "report"})
    events.sort(key=lambda e: (e["day"], e["kind"]))

    liquid_ils = nw["liquid_usd"] * fx
    safe_ils = sum(c["usd"] for c in nw["by_category"] if c["category"] in ("cash", "bank")) * fx
    runway = (safe_ils / burn) if burn else None

    cur: dict[str, float] = {}
    for i in nw["items"]:
        if i["usd"] <= 0:
            continue
        c = i.get("currency") or ("USD" if i["source"] != "manual" else "ILS")
        cur[c] = cur.get(c, 0.0) + i["usd"]
    tot = sum(cur.values()) or 1.0
    currency = [{"currency": k, "usd": round(v, 2), "pct": round(100 * v / tot, 1)} for k, v in sorted(cur.items(), key=lambda kv: -kv[1])]

    with db.get_conn() as conn:
        rows = conn.execute("SELECT day, total_usd, breakdown_json FROM wealth_snapshots ORDER BY day DESC LIMIT 400").fetchall()
    composition = []
    for r in reversed(rows):
        try:
            bd = json.loads(r["breakdown_json"] or "{}")
        except ValueError:
            bd = {}
        composition.append({"day": r["day"], "total": r["total_usd"], **{k: round(v, 2) for k, v in bd.items()}})

    # health score: five parts, 20 points each
    assets = nw["assets_usd"] or 1.0
    risky = [i for i in nw["items"] if i["usd"] > 0 and core.CATEGORIES.get(i["category"], ("", "medium"))[1] != "safe"]
    top = max(risky, key=lambda i: i["usd"], default=None)
    top_pct = 100 * top["usd"] / assets if top else 0.0
    crypto_pct = 100 * sum(c["usd"] for c in nw["by_category"] if c["category"] == "crypto") / assets
    lim = core.settings()["risk"]
    sr = (100 * net_month / income) if income else None
    parts = [
        {"key": "emergency", "label": "Emergency fund", "score": round(20 * _clip((runway or 0) / 6)),
         "value": f"{runway:.1f} months" if runway is not None else "set main balance",
         "tip": "Cash + bank ÷ monthly spending. 6 months = full marks."},
        {"key": "savings", "label": "Savings rate", "score": round(20 * _clip((sr or 0) / 20)) if sr is not None else 0,
         "value": f"{sr:.0f}%" if sr is not None else "add income", "tip": "Income left after spending. 20%+ = full marks."},
        {"key": "diversify", "label": "Diversification", "score": round(20 * _clip((80 - top_pct) / 60)),
         "value": f"top: {top['name']} {top_pct:.0f}%" if top else "—", "tip": "Largest single risky position. ≤20% = full marks, ≥80% = zero."},
        {"key": "debt", "label": "Debt", "score": round(20 * _clip(1 - (nw["debts_usd"] / assets) / 0.5)),
         "value": f"{100 * nw['debts_usd'] / assets:.0f}% of assets", "tip": "Debts ÷ assets. None = full marks, 50%+ = zero."},
        {"key": "crypto", "label": "Speculation", "score": round(20 * _clip(1 - max(0.0, crypto_pct - lim["max_crypto_pct"]) / max(1.0, 100 - lim["max_crypto_pct"]))),
         "value": f"crypto {crypto_pct:.0f}% (limit {lim['max_crypto_pct']:.0f}%)", "tip": "Crypto above your own limit costs points."},
    ]
    score = sum(p["score"] for p in parts)
    return {"usdils": fx, "main_balance_ils": bal0, "income_month_ils": round(income), "burn_month_ils": round(burn), "net_month_ils": round(net_month),
            "cc_avg_ils": round(cc_avg), "savings_rate": round(sr, 1) if sr is not None else None, "runway_months": round(runway, 1) if runway is not None else None,
            "liquid_ils": round(liquid_ils), "safe_ils": round(safe_ils), "forecast": forecast, "events": events[:14], "currency": currency,
            "composition": composition, "health": {"score": score, "grade": "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "E", "parts": parts},
            "burn_source": "bank/card import" if b.get("count") else "fixed items + card answers"}


def holdings() -> dict[str, Any]:
    from app.api import invest_routes as inv
    from app.api.capital_routes import _yahoo_symbol

    pf = core._portfolio()
    hold = [h for h in pf.get("holdings", []) if h.get("value")]
    spy = inv._safe_history("SPY", "1y", "1d")
    spy_c = {dt.datetime.fromtimestamp(c[0], dt.timezone.utc).date(): c[4] for c in (spy or {}).get("candles", [])}
    today = dt.date.today()
    rows, rets = [], {}
    for h in hold:
        hist = inv._safe_history(_yahoo_symbol(h["kind"], h["symbol"]), "1y", "1d")
        cs = [(dt.datetime.fromtimestamp(c[0], dt.timezone.utc).date(), c[4]) for c in (hist or {}).get("candles", []) if c[4]]
        if len(cs) < 20:
            rows.append({"symbol": h["symbol"], "kind": h["kind"], "value": h["value"], "price": h.get("price"), "day_pct": h.get("day_pct")})
            continue
        px = h.get("price") or cs[-1][1]

        def ret(days: int) -> float | None:
            ref = [c for d, c in cs if d <= today - dt.timedelta(days=days)]
            return round(100 * (px / ref[-1] - 1), 2) if ref else None

        ytd_ref = [c for d, c in cs if d < today.replace(month=1, day=1)]
        closes = np.array([c for _, c in cs], dtype=float)
        lr = np.diff(np.log(closes))
        rets[h["symbol"]] = {d: r for (d, _), r in zip(cs[1:], lr)}
        hi, lo = float(closes.max()), float(closes.min())
        beta = None
        common = [(r, math.log(spy_c[d] / spy_c[p])) for (p, _), (d, _), r in zip(cs[:-1], cs[1:], lr) if d in spy_c and p in spy_c and spy_c[p]]
        if len(common) > 30:
            a = np.array(common)
            vs = float(np.var(a[:, 1]))
            beta = round(float(np.cov(a[:, 0], a[:, 1])[0, 1] / vs), 2) if vs else None
        rows.append({"symbol": h["symbol"], "kind": h["kind"], "value": h["value"], "price": px, "day_pct": h.get("day_pct"),
                     "pnl": h.get("pnl"), "pnl_pct": h.get("pnl_pct"),
                     "r1m": ret(30), "r3m": ret(91), "r1y": ret(365), "ytd": round(100 * (px / ytd_ref[-1] - 1), 2) if ytd_ref else None,
                     "from_high": round(100 * (px / hi - 1), 1), "from_low": round(100 * (px / lo - 1), 1), "high": hi, "low": lo,
                     "vol": round(100 * float(np.std(lr[-120:])) * math.sqrt(365 if h["kind"] == "crypto" else 252), 1), "beta": beta,
                     "spark": [round(float(x), 4) for x in closes[-90:]]})
    # share of portfolio risk (marginal contribution to variance of today's mix)
    names = [r["symbol"] for r in rows if r["symbol"] in rets]
    if names:
        days = sorted(set.intersection(*[set(rets[n]) for n in names]))
        if len(days) > 30:
            mat = np.array([[rets[n][d] for d in days] for n in names])
            w = np.array([next(r["value"] for r in rows if r["symbol"] == n) for n in names], dtype=float)
            w = w / w.sum()
            cov = np.cov(mat) if len(names) > 1 else np.array([[float(np.var(mat))]])
            pv = float(w @ cov @ w)
            if pv > 0:
                contrib = w * (cov @ w) / pv
                for n, c in zip(names, contrib):
                    next(r for r in rows if r["symbol"] == n)["risk_share"] = round(100 * float(c), 1)
    tot = sum(r["value"] for r in rows) or 1.0
    for r in rows:
        r["weight"] = round(100 * r["value"] / tot, 1)
    return {"rows": sorted(rows, key=lambda r: -r["value"]), "total_usd": round(tot, 2)}
