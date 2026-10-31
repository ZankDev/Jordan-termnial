"""WEALTH — one place for all of the user's money: net worth, risk, performance vs the market, goals & rebalancing.

Sources pulled together (USD internally, shown in USD and ILS):
  portfolio     CAPITAL › PORTFOLIO holdings (capital_holdings) valued live (capital_routes.get_portfolio)
  polymarket    the real Polymarket account balance (real_trading.balance)
  tradovate     real futures account cash balances when the read-only link is connected (accounts.tradovate)
  manual        everything else the user enters: cash, bank, pension/provident funds, real estate, private business, debts
                (wealth_assets; value in its own currency, converted with the live USD/ILS rate)

A snapshot of the total is stored once a day (wealth_snapshots) so net worth has a history.
Nothing here trades or moves money; it reads, adds up and warns.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from app import db

IL = ZoneInfo("Asia/Jerusalem")
CATEGORIES = {  # category -> (label, risk bucket, liquid)
    "cash": ("Cash", "safe", True), "bank": ("Bank / deposits", "safe", True), "bonds": ("Bonds", "low", True),
    "pension": ("Pension / provident", "medium", False), "stocks": ("Stocks & ETFs", "high", True), "crypto": ("Crypto", "very high", True),
    "real_estate": ("Real estate", "medium", False), "business": ("Private business", "high", False),
    "trading": ("Trading accounts", "very high", True), "other": ("Other", "medium", False), "debt": ("Debt / loans", "debt", True),
}
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS wealth_assets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
       currency TEXT NOT NULL DEFAULT 'ILS', value REAL NOT NULL, notes TEXT, updated_at TEXT NOT NULL DEFAULT (datetime('now')))""",
    """CREATE TABLE IF NOT EXISTS wealth_snapshots (day TEXT PRIMARY KEY, total_usd REAL NOT NULL, usdils REAL, breakdown_json TEXT NOT NULL,
       created_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wealth_flows (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, amount_usd REAL NOT NULL,
       note TEXT)""",
]
DEFAULT_SETTINGS = {
    "targets": {"stocks": 50, "crypto": 15, "bank": 15, "pension": 10, "cash": 10},  # target allocation %, of positive assets
    "drift_alert_pct": 5.0,
    "risk": {"max_position_pct": 25.0, "max_crypto_pct": 30.0, "daily_loss_alert_pct": 3.0, "real_daily_loss_usd": 25.0},
    "goals": [],  # [{"name", "target_usd", "monthly_usd", "years"}]
}


def ensure() -> None:
    with db.get_conn() as conn:
        for s in SCHEMA:
            conn.execute(s)


def settings() -> dict[str, Any]:
    try:
        saved = json.loads(db.meta_get("wealth_settings") or "{}")
    except ValueError:
        saved = {}
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    cur = settings()
    for k, v in patch.items():
        if k in DEFAULT_SETTINGS:
            if isinstance(v, dict) and isinstance(cur.get(k), dict) and k != "targets":
                cur[k].update(v)
            else:
                cur[k] = v
    db.meta_set("wealth_settings", json.dumps(cur))
    return cur


# ------------------------------------------------------------------ FX + manual assets
_fx: dict[str, Any] = {"ts": 0.0, "usdils": 3.7}


def usdils() -> float:
    if time.time() - _fx["ts"] < 600:
        return _fx["usdils"]
    try:
        from app.api import invest_routes as inv

        r = inv._mon_row("ILS=X", "USD/ILS")
        if r and r.get("price"):
            _fx.update(ts=time.time(), usdils=float(r["price"]))
    except Exception:  # noqa: BLE001
        pass
    return _fx["usdils"]


def to_usd(value: float, currency: str) -> float:
    c = (currency or "USD").upper()
    if c == "USD":
        return value
    if c == "ILS":
        return value / usdils()
    if c == "EUR":
        return value * 1.08
    return value


def list_assets() -> list[dict[str, Any]]:
    ensure()
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM wealth_assets ORDER BY category, name").fetchall()]


def upsert_asset(a: dict[str, Any]) -> dict[str, Any]:
    ensure()
    cat = a.get("category") if a.get("category") in CATEGORIES else "other"
    val = float(a["value"])
    if cat == "debt":
        val = -abs(val)
    with db.get_conn() as conn:
        if a.get("id"):
            conn.execute("UPDATE wealth_assets SET name=?, category=?, currency=?, value=?, notes=?, updated_at=datetime('now') WHERE id=?",
                         (a["name"], cat, (a.get("currency") or "ILS").upper(), val, a.get("notes"), int(a["id"])))
            aid = int(a["id"])
        else:
            aid = conn.execute("INSERT INTO wealth_assets (name, category, currency, value, notes) VALUES (?,?,?,?,?)",
                               (a["name"], cat, (a.get("currency") or "ILS").upper(), val, a.get("notes"))).lastrowid
        return dict(conn.execute("SELECT * FROM wealth_assets WHERE id=?", (aid,)).fetchone())


def delete_asset(aid: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM wealth_assets WHERE id=?", (aid,))


# ------------------------------------------------------------------ net worth
def _portfolio() -> dict[str, Any]:
    try:
        from app.api.capital_routes import get_portfolio

        return get_portfolio()
    except Exception:  # noqa: BLE001
        return {"holdings": [], "totals": {"value": 0.0}}


_pm_cache: dict[str, tuple[float, float | None]] = {}


def _polymarket() -> float | None:
    try:
        from app.polymarket import real_trading as rt

        if not rt.is_connected():
            return None
        st = rt.status(check_balance=False)  # the engine keeps the balance fresh while armed
        if st.get("balance") is not None:
            return st.get("balance")
        hit = _pm_cache.get("v")
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        bal = rt.balance()  # disarmed: read it directly, at most every 10 minutes
        _pm_cache["v"] = (time.time(), bal)
        return bal
    except Exception:  # noqa: BLE001
        return None


def _tradovate() -> list[dict[str, Any]]:
    try:
        from app.accounts import tradovate

        b = tradovate.balances()
        return [a for a in b.get("accounts", []) if a.get("cash_balance") is not None]
    except Exception:  # noqa: BLE001
        return []


_nw_memo: dict[str, Any] = {}


def networth(max_age: float = 60.0) -> dict[str, Any]:
    """Everything owned minus owed. Reused for `max_age` seconds: risk, rebalance, goals and the insights all build on it,
    and a cold read (portfolio quotes, Polymarket, FX) takes ~15 s."""
    hit = _nw_memo.get("v")
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    out = _networth()
    _nw_memo["v"] = (time.time(), out)
    return out


def _networth() -> dict[str, Any]:
    fx = usdils()
    items: list[dict[str, Any]] = []
    pf = _portfolio()
    for h in pf.get("holdings", []):
        if h.get("value") is None:
            continue
        items.append({"source": "portfolio", "name": h["symbol"], "category": "crypto" if h["kind"] == "crypto" else "stocks",
                      "usd": h["value"], "pnl": h.get("pnl"), "day_pnl": h.get("day_pnl"), "symbol": h["symbol"], "kind": h["kind"]})
    pm = _polymarket()
    if pm is not None:
        items.append({"source": "polymarket", "name": "Polymarket (real)", "category": "trading", "usd": pm})
    for a in _tradovate():
        items.append({"source": "tradovate", "name": f"Tradovate {a.get('name')}", "category": "trading", "usd": a["cash_balance"],
                      "day_pnl": a.get("day_pnl")})
    for a in list_assets():
        items.append({"source": "manual", "id": a["id"], "name": a["name"], "category": a["category"], "usd": to_usd(a["value"], a["currency"]),
                      "native": a["value"], "currency": a["currency"], "updated_at": a["updated_at"], "notes": a.get("notes")})
    total = sum(i["usd"] for i in items)
    assets = sum(i["usd"] for i in items if i["usd"] > 0)
    debts = -sum(i["usd"] for i in items if i["usd"] < 0)
    by_cat: dict[str, float] = {}
    for i in items:
        by_cat[i["category"]] = by_cat.get(i["category"], 0.0) + i["usd"]
    by_risk: dict[str, float] = {}
    for c, v in by_cat.items():
        b = CATEGORIES.get(c, ("", "medium", False))[1]
        by_risk[b] = by_risk.get(b, 0.0) + v
    liquid = sum(v for c, v in by_cat.items() if CATEGORIES.get(c, ("", "", False))[2] and v > 0)
    for i in items:
        i["weight"] = round(100 * i["usd"] / assets, 2) if assets and i["usd"] > 0 else 0.0
    day_pnl = sum(i.get("day_pnl") or 0.0 for i in items)
    hist = history()
    change = {}
    for label, days in (("1d", 1), ("7d", 7), ("30d", 30), ("365d", 365)):
        ref = _snapshot_before(hist, days)
        if ref:
            change[label] = {"usd": round(total - ref["total_usd"], 2), "pct": round(100 * (total / ref["total_usd"] - 1), 2) if ref["total_usd"] else None}
    return {"at": time.time(), "usdils": fx, "total_usd": round(total, 2), "total_ils": round(total * fx, 2), "assets_usd": round(assets, 2),
            "debts_usd": round(debts, 2), "liquid_usd": round(liquid, 2), "day_pnl_usd": round(day_pnl, 2),
            "by_category": [{"category": c, "label": CATEGORIES.get(c, (c,))[0], "usd": round(v, 2), "pct": round(100 * v / assets, 2) if assets and v > 0 else 0.0}
                            for c, v in sorted(by_cat.items(), key=lambda kv: -kv[1])],
            "by_risk": {k: round(v, 2) for k, v in by_risk.items()}, "items": sorted(items, key=lambda i: -i["usd"]), "change": change,
            "categories": {k: v[0] for k, v in CATEGORIES.items()}}


def history(days: int = 730) -> list[dict[str, Any]]:
    ensure()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT day, total_usd, usdils FROM wealth_snapshots ORDER BY day DESC LIMIT ?", (days,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def _snapshot_before(hist: list[dict[str, Any]], days: int) -> dict[str, Any] | None:
    cutoff = (dt.datetime.now(IL).date() - dt.timedelta(days=days)).isoformat()
    older = [h for h in hist if h["day"] <= cutoff]
    return older[-1] if older else None


def snapshot() -> dict[str, Any]:
    ensure()
    nw = networth(0)
    day = dt.datetime.now(IL).date().isoformat()
    with db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO wealth_snapshots (day, total_usd, usdils, breakdown_json, created_at) VALUES (?,?,?,?,?)",
                     (day, nw["total_usd"], nw["usdils"], json.dumps({c["category"]: c["usd"] for c in nw["by_category"]}), time.time()))
    return nw


# ------------------------------------------------------------------ risk
def _returns(symbols: list[tuple[str, str]], days: int = 250) -> dict[str, np.ndarray]:
    from app.api import invest_routes as inv
    from app.api.capital_routes import _yahoo_symbol

    out: dict[str, np.ndarray] = {}
    for kind, sym in symbols:
        h = inv._safe_history(_yahoo_symbol(kind, sym), "1y", "1d")
        if not h or len(h["candles"]) < 30:
            continue
        closes = np.array([c[4] for c in h["candles"]][-days:], dtype=float)
        out[sym] = np.diff(np.log(closes))
    return out


def risk() -> dict[str, Any]:
    s = settings()["risk"]
    nw = networth()
    assets = nw["assets_usd"] or 1.0
    warnings: list[dict[str, str]] = []
    positive = [i for i in nw["items"] if i["usd"] > 0 and CATEGORIES.get(i["category"], ("", "medium"))[1] != "safe"]  # a bank deposit is not a bet
    top = max(positive, key=lambda i: i["usd"], default=None)
    if top and 100 * top["usd"] / assets > s["max_position_pct"]:
        warnings.append({"level": "bad", "title": f"Concentration: {top['name']} is {100 * top['usd'] / assets:.0f}% of everything",
                         "text": f"Your limit is {s['max_position_pct']:.0f}%. One position this large decides your whole result."})
    crypto = sum(c["usd"] for c in nw["by_category"] if c["category"] == "crypto")
    if 100 * crypto / assets > s["max_crypto_pct"]:
        warnings.append({"level": "warn", "title": f"Crypto is {100 * crypto / assets:.0f}% of your assets",
                         "text": f"Above your {s['max_crypto_pct']:.0f}% limit. Crypto can fall 50-80% in a bear market."})
    liquid_pct = 100 * nw["liquid_usd"] / assets
    if liquid_pct < 10:
        warnings.append({"level": "warn", "title": f"Only {liquid_pct:.0f}% is liquid", "text": "Little money you could reach quickly in an emergency."})

    # market risk of the priced part (portfolio holdings): correlation, volatility, VaR / CVaR by historical simulation
    hold = [i for i in nw["items"] if i["source"] == "portfolio"]
    rets = _returns([(i["kind"], i["symbol"]) for i in hold])
    corr, var = None, None
    if rets:
        n = min(len(v) for v in rets.values())
        names = list(rets)
        mat = np.array([rets[k][-n:] for k in names])
        w = np.array([next(i["usd"] for i in hold if i["symbol"] == k) for k in names])
        value = float(w.sum())
        w = w / w.sum()
        port = w @ mat                                            # daily log returns of today's mix
        cm = np.corrcoef(mat) if len(names) > 1 else np.array([[1.0]])
        corr = {"names": names, "matrix": [[round(float(x), 2) for x in row] for row in cm]}
        vol = float(np.std(port) * math.sqrt(252))
        rng = np.random.default_rng(3)
        # 21-day paths from resampled days (block of the whole cross-section keeps correlation)
        idx = rng.integers(0, n, size=(5000, 21))
        month = np.exp(port[idx].sum(axis=1)) - 1
        var95 = float(-np.percentile(month, 5))
        cvar95 = float(-month[month <= np.percentile(month, 5)].mean())
        pk = np.maximum.accumulate(np.exp(np.cumsum(port)))
        mdd = float((1 - np.exp(np.cumsum(port)) / pk).max())
        avg_corr = float((cm.sum() - len(names)) / (len(names) ** 2 - len(names))) if len(names) > 1 else 1.0
        var = {"value_usd": round(value, 2), "vol_annual_pct": round(100 * vol, 1), "var95_month_pct": round(100 * var95, 1),
               "var95_month_usd": round(value * var95, 2), "cvar95_month_usd": round(value * cvar95, 2), "max_drawdown_1y_pct": round(100 * mdd, 1),
               "avg_correlation": round(avg_corr, 2), "days": int(n)}
        if avg_corr > 0.6 and len(names) > 2:
            warnings.append({"level": "warn", "title": f"Your holdings move together (avg correlation {avg_corr:.2f})",
                             "text": "Diversification is weaker than the number of positions suggests — it behaves more like one bet."})
    # real-money trading exposure
    real = {}
    try:
        from app.polymarket import real_trading as rt

        st = rt.status(check_balance=False)
        real = {"polymarket_armed": (st.get("settings") or {}).get("armed"), "polymarket_today": st.get("today"),
                "polymarket_daily_limit": (st.get("settings") or {}).get("daily_loss_limit")}
    except Exception:  # noqa: BLE001
        pass
    if not warnings:
        warnings.append({"level": "good", "title": "No risk limit breached", "text": "Concentration, crypto share and liquidity are within your limits."})
    return {"networth": {k: nw[k] for k in ("total_usd", "assets_usd", "liquid_usd", "usdils")}, "limits": s, "warnings": warnings,
            "market": var, "correlation": corr, "real": real, "by_risk": nw["by_risk"]}


def kill_switch(reason: str = "WEALTH kill switch") -> dict[str, Any]:
    """Stop every real-money trading path at once (Polymarket real engine). Paper accounts are left alone."""
    done = []
    try:
        from app.polymarket import real_engine

        real_engine.panic()
        done.append("polymarket: disarmed and open positions sold")
    except Exception as e:  # noqa: BLE001
        done.append(f"polymarket: {e}")
    try:
        from app.notify import telegram_bot

        telegram_bot.send_text(f"🛑 KILL SWITCH — {reason}\n" + "\n".join(done))
    except Exception:  # noqa: BLE001
        pass
    return {"done": done}


# ------------------------------------------------------------------ performance vs the market
def performance() -> dict[str, Any]:
    """Portfolio holdings since each was added (at its cost basis) vs putting the SAME dollars into SPY / BTC on the SAME days.
    Money-weighted comparison: the only fair one without a full transaction ledger. Also the estimated capital-gains tax
    if everything were sold today (Israel 25% on real gains — an estimate to check with an accountant)."""
    from app.api import invest_routes as inv
    from app.api.capital_routes import _yahoo_symbol

    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM capital_holdings").fetchall()]
    if not rows:
        return {"lots": [], "curve": []}
    bench = {b: inv._safe_history(b, "2y", "1d") for b in ("SPY", "BTC-USD")}
    bclose = {b: {dt.datetime.fromtimestamp(c[0], dt.timezone.utc).date().isoformat(): c[4] for c in (h or {}).get("candles", [])} for b, h in bench.items()}

    def px_on(series: dict[str, float], day: str) -> float | None:
        ks = [k for k in series if k <= day]
        return series[max(ks)] if ks else (series[min(series)] if series else None)

    pf = _portfolio()
    price_now = {h["symbol"]: h.get("price") for h in pf.get("holdings", [])}
    lots, cost_total, value_total, spy_total, btc_total = [], 0.0, 0.0, 0.0, 0.0
    start = None
    for r in rows:
        day = str(r["added_at"])[:10]
        start = min(start or day, day)
        cost = r["qty"] * r["cost_basis"]
        now = (price_now.get(r["symbol"].upper()) or 0) * r["qty"]
        spy0, btc0 = px_on(bclose["SPY"], day), px_on(bclose["BTC-USD"], day)
        spy_last = bclose["SPY"][max(bclose["SPY"])] if bclose["SPY"] else None
        btc_last = bclose["BTC-USD"][max(bclose["BTC-USD"])] if bclose["BTC-USD"] else None
        spy_now = cost * spy_last / spy0 if spy0 and spy_last else None
        btc_now = cost * btc_last / btc0 if btc0 and btc_last else None
        lots.append({"symbol": r["symbol"], "kind": r["kind"], "added": day, "qty": r["qty"], "cost": round(cost, 2), "value": round(now, 2),
                     "pnl": round(now - cost, 2), "pnl_pct": round(100 * (now / cost - 1), 2) if cost else None,
                     "spy_value": round(spy_now, 2) if spy_now else None, "btc_value": round(btc_now, 2) if btc_now else None,
                     "vs_spy": round(now - spy_now, 2) if spy_now else None})
        cost_total += cost; value_total += now; spy_total += spy_now or cost; btc_total += btc_now or cost
    gains = sum(max(0.0, l["pnl"]) for l in lots)
    losses = -sum(min(0.0, l["pnl"]) for l in lots)
    taxable = max(0.0, gains - losses)
    # curve: holdings value by day (price history x qty) vs the same cash in SPY
    curve = []
    try:
        series = {}
        for r in rows:
            h = inv._safe_history(_yahoo_symbol(r["kind"], r["symbol"]), "2y", "1d")
            series[r["id"]] = {dt.datetime.fromtimestamp(c[0], dt.timezone.utc).date().isoformat(): c[4] for c in (h or {}).get("candles", [])}
        days = sorted(d for d in bclose["SPY"] if d >= (start or "9999"))
        for d in days:
            val = spy = cost = 0.0
            for r in rows:
                a = str(r["added_at"])[:10]
                if a > d:
                    continue
                p = px_on(series[r["id"]], d)
                c0 = r["qty"] * r["cost_basis"]
                s0 = px_on(bclose["SPY"], a)
                val += (p or r["cost_basis"]) * r["qty"]
                spy += c0 * bclose["SPY"][d] / s0 if s0 else c0
                cost += c0
            curve.append({"day": d, "value": round(val, 2), "spy": round(spy, 2), "cost": round(cost, 2)})
    except Exception:  # noqa: BLE001
        curve = []
    return {"since": start, "cost": round(cost_total, 2), "value": round(value_total, 2), "pnl": round(value_total - cost_total, 2),
            "pnl_pct": round(100 * (value_total / cost_total - 1), 2) if cost_total else None,
            "spy_value": round(spy_total, 2), "btc_value": round(btc_total, 2), "vs_spy": round(value_total - spy_total, 2), "vs_btc": round(value_total - btc_total, 2),
            "tax": {"gains": round(gains, 2), "losses": round(losses, 2), "taxable": round(taxable, 2), "rate": 0.25, "estimated_usd": round(0.25 * taxable, 2),
                    "note": "Estimate only: Israeli capital-gains tax is 25% on the REAL (inflation-adjusted) gain, losses offset gains, "
                            "foreign-currency effects and your personal situation apply — check with an accountant."},
            "lots": sorted(lots, key=lambda l: -l["value"]), "curve": curve}


# ------------------------------------------------------------------ goals + rebalancing
EXPECTED = {  # category -> (annual expected return, annual volatility) — long-run assumptions, shown on the page
    "cash": (0.03, 0.005), "bank": (0.04, 0.01), "bonds": (0.045, 0.06), "pension": (0.06, 0.10), "stocks": (0.08, 0.17),
    "crypto": (0.12, 0.70), "real_estate": (0.05, 0.10), "business": (0.10, 0.30), "trading": (0.0, 0.40), "other": (0.03, 0.10), "debt": (-0.06, 0.0),
}


def rebalance() -> dict[str, Any]:
    st = settings()
    nw = networth(600)
    targets = {k: float(v) for k, v in st["targets"].items() if float(v) > 0}
    tsum = sum(targets.values()) or 1.0
    targets = {k: 100 * v / tsum for k, v in targets.items()}
    pos = {c["category"]: c["usd"] for c in nw["by_category"] if c["usd"] > 0}
    base = sum(pos.get(k, 0.0) for k in set(pos) | set(targets))
    rows = []
    for k in sorted(set(pos) | set(targets), key=lambda k: -targets.get(k, 0)):
        cur = pos.get(k, 0.0)
        cur_pct = 100 * cur / base if base else 0.0
        tgt = targets.get(k, 0.0)
        want = base * tgt / 100
        rows.append({"category": k, "label": CATEGORIES.get(k, (k,))[0], "usd": round(cur, 2), "pct": round(cur_pct, 1), "target_pct": round(tgt, 1),
                     "drift": round(cur_pct - tgt, 1), "action_usd": round(want - cur, 2),
                     "alert": abs(cur_pct - tgt) >= st["drift_alert_pct"]})
    return {"base_usd": round(base, 2), "rows": rows, "drift_alert_pct": st["drift_alert_pct"],
            "note": "Suggested moves to get back to your target mix. Nothing is traded — you decide and act."}


def project(goal: dict[str, Any], paths: int = 4000) -> dict[str, Any]:
    """Monte Carlo of reaching a goal: today's assets (current mix, long-run return/vol assumptions) + a monthly contribution."""
    nw = networth(600)
    start = max(0.0, nw["total_usd"]) if goal.get("start_usd") is None else float(goal["start_usd"])
    mix = {c["category"]: c["usd"] for c in nw["by_category"] if c["usd"] > 0}
    tot = sum(mix.values()) or 1.0
    mu = sum(EXPECTED.get(k, (0.04, 0.1))[0] * v / tot for k, v in mix.items()) if mix else 0.05
    sig = math.sqrt(sum((EXPECTED.get(k, (0.04, 0.1))[1] * v / tot) ** 2 for k, v in mix.items())) if mix else 0.1
    years = max(1, int(goal.get("years") or 10))
    monthly = float(goal.get("monthly_usd") or 0)
    target = float(goal.get("target_usd") or 0)
    rng = np.random.default_rng(5)
    m = years * 12
    r = rng.normal(mu / 12 - 0.5 * (sig ** 2) / 12, sig / math.sqrt(12), size=(paths, m))
    val = np.full(paths, start)
    track = np.zeros((paths, m))
    for t in range(m):
        val = val * np.exp(r[:, t]) + monthly
        track[:, t] = val
    reach = (track >= target).any(axis=1) if target else np.zeros(paths, bool)
    first = np.where(reach, (track >= target).argmax(axis=1) + 1, -1)
    yearly = [{"year": y, "p10": round(float(np.percentile(track[:, y * 12 - 1], 10))), "p50": round(float(np.percentile(track[:, y * 12 - 1], 50))),
               "p90": round(float(np.percentile(track[:, y * 12 - 1], 90)))} for y in range(1, years + 1)]
    return {"name": goal.get("name"), "start_usd": round(start), "monthly_usd": monthly, "years": years, "target_usd": target,
            "assumed_return_pct": round(100 * mu, 1), "assumed_vol_pct": round(100 * sig, 1),
            "probability": round(100 * float(reach.mean()), 1) if target else None,
            "median_months": int(np.median(first[first > 0])) if (first > 0).any() else None, "yearly": yearly,
            "contributed_usd": round(start + monthly * m), "note": "Assumptions per asset class are long-run averages, not predictions."}
