"""Portfolio analytics for the fund: live valuation, P&L, exposure, historical-simulation VaR, beta, drawdown, correlation, limit checks and the opportunity screen.

Risk numbers are estimated from the last year of daily returns of the CURRENT holdings at CURRENT weights (historical simulation), so they describe the portfolio
as it is now, not its track record. VaR 95% = the loss exceeded on 5% of past days. They are estimates, not guarantees: tail days can be much worse.
"""
from __future__ import annotations

import concurrent.futures as cf
import threading
import time
from typing import Any

import numpy as np

from app.fund import book

_screen: dict[str, Any] = {"running": False, "done": 0, "total": 0, "started": 0.0, "rows": {}, "universe": "", "error": None}
_slock = threading.Lock()


def _hist(symbol: str) -> dict[str, Any] | None:
    from app.api import invest_routes as ir

    return ir._safe_history(symbol, "1y", "1d")


def _returns(h: dict[str, Any] | None) -> dict[str, float]:
    """date -> daily return"""
    if not h:
        return {}
    c = h["candles"]
    out = {}
    for i in range(1, len(c)):
        if c[i][4] and c[i - 1][4]:
            out[time.strftime("%Y-%m-%d", time.gmtime(c[i][0]))] = c[i][4] / c[i - 1][4] - 1
    return out


def overview() -> dict[str, Any]:
    from app.api import invest_routes as ir

    pos = book.positions()
    lim = book.limits()
    cash = book.cash()
    rows, hists = [], {}
    with cf.ThreadPoolExecutor(6) as pool:
        for sym, h in zip((p["symbol"] for p in pos), pool.map(lambda p: _hist(p["symbol"]), pos)):
            hists[sym] = h
    spy = _hist("SPY")
    def build_row(p: dict[str, Any]) -> dict[str, Any]:
        h = hists.get(p["symbol"])
        price = (h["meta"].get("regularMarketPrice") if h else None) or (h["candles"][-1][4] if h and h["candles"] else None)
        prev = (h["candles"][-2][4] if h and len(h["candles"]) > 1 else None)
        cur = (h or {}).get("meta", {}).get("currency")
        mv = p["qty"] * price if price else None
        try:
            sec = ir.cached(f"sec:{p['symbol']}", 45, lambda s=p["symbol"]: ir._security(s))
            sector, name = (sec.get("profile") or {}).get("sector"), sec.get("name")
        except Exception:  # noqa: BLE001
            sector, name = None, None
        try:
            v = ir.api_verdict(p["symbol"])
            verdict = {"stance": v["stance"], "score": v["score"], "top_buy": (v["why_buy"] or [{}])[0].get("title"), "top_not": (v["why_not"] or [{}])[0].get("title")}
        except Exception:  # noqa: BLE001
            verdict = None
        return {"symbol": p["symbol"], "name": name, "qty": p["qty"], "avg_cost": p["avg_cost"], "price": price, "currency": cur, "value": mv,
                "pnl": None if mv is None else mv - p["qty"] * p["avg_cost"], "pnl_pct": None if not price else (price / p["avg_cost"] - 1) * 100,
                "day_pct": None if not (price and prev) else (price / prev - 1) * 100, "day_pnl": None if not (price and prev) else p["qty"] * (price - prev),
                "sector": sector or "Other / ETF", "sleeve": p.get("sleeve"), "thesis": p.get("thesis"), "verdict": verdict}

    with cf.ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(build_row, pos))
    invested = sum(r["value"] or 0 for r in rows)
    nav = cash + invested
    for r in rows:
        r["weight"] = (r["value"] or 0) / nav * 100 if nav else 0
    sectors: dict[str, float] = {}
    for r in rows:
        sectors[r["sector"]] = sectors.get(r["sector"], 0) + r["weight"]
    # risk from daily returns at current weights
    risk: dict[str, Any] = {}
    ret = {r["symbol"]: _returns(hists.get(r["symbol"])) for r in rows}
    dates = sorted(set.intersection(*[set(v) for v in ret.values()]) if ret and all(ret.values()) else [])
    if len(dates) >= 60 and nav > 0:
        w = np.array([(r["value"] or 0) / nav for r in rows])
        mat = np.array([[ret[r["symbol"]][d] for d in dates] for r in rows])
        port = w @ mat
        var95, var99 = -np.percentile(port, 5), -np.percentile(port, 1)
        curve = np.cumprod(1 + port)
        dd = float(((curve / np.maximum.accumulate(curve)) - 1).min())
        risk = {"days": len(dates), "vol_ann_pct": round(float(port.std() * np.sqrt(252) * 100), 1), "var95_pct": round(float(var95 * 100), 2), "var99_pct": round(float(var99 * 100), 2),
                "var95_usd": round(float(var95 * nav), 0), "max_dd_pct": round(dd * 100, 1), "worst_day_pct": round(float(port.min() * 100), 2)}
        sr = _returns(spy)
        common = [d for d in dates if d in sr]
        if len(common) >= 60:
            b = np.polyfit([sr[d] for d in common], [float(w @ np.array([ret[r["symbol"]][d] for r in rows])) for d in common], 1)[0]
            risk["beta"] = round(float(b), 2)
        if len(rows) >= 2:
            cm = np.corrcoef(mat)
            pairs = sorted(((float(cm[i, j]), rows[i]["symbol"], rows[j]["symbol"]) for i in range(len(rows)) for j in range(i + 1, len(rows))), reverse=True)
            risk["most_correlated"] = [{"a": a, "b": b_, "corr": round(c, 2)} for c, a, b_ in pairs[:3]]
    warnings = []
    for r in rows:
        if r["weight"] > lim["max_position_pct"]:
            warnings.append({"level": "high", "text": f"{r['symbol']} is {r['weight']:.1f}% of NAV (limit {lim['max_position_pct']:.0f}%)"})
        if r["currency"] and r["currency"] not in ("USD",):
            warnings.append({"level": "info", "text": f"{r['symbol']} is quoted in {r['currency']}; values here are not converted to USD"})
        if r["verdict"] and r["verdict"]["score"] <= -35:
            warnings.append({"level": "med", "text": f"{r['symbol']}: the model's case against is strong ({r['verdict']['stance']}, {r['verdict']['score']:+d})"})
    for s_, w_ in sectors.items():
        if w_ > lim["max_sector_pct"] and s_ != "Other / ETF":
            warnings.append({"level": "high", "text": f"Sector {s_} is {w_:.1f}% of NAV (limit {lim['max_sector_pct']:.0f}%)"})
    if risk and risk["var95_pct"] > lim["max_var95_pct"]:
        warnings.append({"level": "high", "text": f"1-day VaR95 is {risk['var95_pct']}% of NAV (limit {lim['max_var95_pct']}%)"})
    if risk and risk["max_dd_pct"] < -lim["max_drawdown_pct"]:
        warnings.append({"level": "med", "text": f"This mix would have drawn down {risk['max_dd_pct']}% over the last year (limit -{lim['max_drawdown_pct']:.0f}%)"})
    if nav and cash / nav * 100 < lim["min_cash_pct"]:
        warnings.append({"level": "med", "text": f"Cash is {cash / nav * 100:.1f}% of NAV (minimum {lim['min_cash_pct']:.0f}%)"})
    if nav > 0:
        book.snapshot(nav, invested)
    hist = book.nav_history(365)
    return {"nav": nav, "cash": cash, "invested": invested, "day_pnl": sum(r["day_pnl"] or 0 for r in rows), "unrealized": sum(r["pnl"] or 0 for r in rows), "realized": book.realized_total(),
            "positions": sorted(rows, key=lambda r: -(r["value"] or 0)), "sectors": sorted(([k, round(v, 1)] for k, v in sectors.items()), key=lambda x: -x[1]), "risk": risk,
            "warnings": warnings, "limits": lim, "nav_history": hist, "trades": book.trades(30),
            "note": "Valuation uses the last Yahoo price. Risk is a historical-simulation estimate of the current holdings, not a guarantee."}


# ------------------------------------------------------------------ opportunity screen
def _scan_one(sym: str) -> tuple[str, dict[str, Any] | None]:
    from app.api import invest_routes as ir

    try:
        v = ir.api_verdict(sym)
        rr = v.get("risk_reward") or {}
        return sym, {"symbol": sym, "name": v.get("name"), "score": v["score"], "stance": v["stance"], "upside_pct": rr.get("upside_pct"), "downside_pct": rr.get("downside_pct"), "rr": rr.get("ratio"),
                     "price": (v.get("levels") or {}).get("price"), "rsi": v.get("rsi"), "why_buy": [r["title"] for r in v["why_buy"][:3]], "why_not": [r["title"] for r in v["why_not"][:2]]}
    except Exception:  # noqa: BLE001
        return sym, None


def start_screen(symbols: list[str], label: str) -> dict[str, Any]:
    with _slock:
        if _screen["running"]:
            return {"started": False, **screen_status()}
        _screen.update(running=True, done=0, total=len(symbols), started=time.time(), universe=label, error=None, rows={})

    def run() -> None:
        try:
            with cf.ThreadPoolExecutor(5) as pool:
                for sym, row in pool.map(_scan_one, symbols):
                    with _slock:
                        _screen["done"] += 1
                        if row:
                            _screen["rows"][sym] = row
        except Exception as e:  # noqa: BLE001
            _screen["error"] = f"{type(e).__name__}: {e}"
        finally:
            _screen["running"] = False

    threading.Thread(target=run, daemon=True, name="fund-screen").start()
    return {"started": True, **screen_status()}


def screen_status() -> dict[str, Any]:
    with _slock:
        rows = sorted(_screen["rows"].values(), key=lambda r: -r["score"])
        return {"running": _screen["running"], "done": _screen["done"], "total": _screen["total"], "universe": _screen["universe"], "error": _screen["error"],
                "age_s": int(time.time() - _screen["started"]) if _screen["started"] else None, "rows": rows}
