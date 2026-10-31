"""Deep analytics for one account: performance & risk statistics, breakdowns, a Monte Carlo of the Apex evaluation,
a position-size sweep, and plain-language findings.

Everything is computed from the account's own closed trades. The Monte Carlo resamples trade P&Ls with replacement
(a bootstrap): it answers "if the next trades look like these ones, in random order, how often does the account reach the
profit target before the trailing drawdown floor catches it?". That is only as good as the sample, so every result is
returned with the sample size and a confidence label, and an account with too few trades borrows the pooled trades of
every account running the same strategy+symbol (flagged as such) rather than pretending 3 trades mean something.

Pure numpy; no network. The evaluation rules are the ones in apex_rules (trailing floor that locks at the start balance).
"""
from __future__ import annotations

import datetime as dt
import math
import time
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from app import db
from app.accounts.apex_rules import TIERS, compute_floor

NY = ZoneInfo("America/New_York")
MIN_OWN_TRADES = 20            # below this the Monte Carlo borrows the strategy's pooled trades
MIN_CELL = 5                   # a breakdown bucket needs this many trades before it can drive advice
HORIZON = 300                  # trades simulated per path (a path stops early on pass/breach)
FAN_POINTS = 120
SCALES = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}


def _dt(iso: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(iso).astimezone(NY) if iso else None
    except (ValueError, TypeError):
        return None


def _r(x: float | None, d: int = 2) -> float | None:
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), d)


def _streaks(pnls: list[float]) -> dict[str, Any]:
    best_w = best_l = cur = 0
    cur_sign = 0
    for p in pnls:
        s = 1 if p > 0 else -1 if p < 0 else 0
        cur = cur + 1 if s == cur_sign and s != 0 else (1 if s != 0 else 0)
        cur_sign = s
        if s > 0:
            best_w = max(best_w, cur)
        elif s < 0:
            best_l = max(best_l, cur)
    return {"max_win": best_w, "max_loss": best_l, "current": cur * cur_sign}


def _bucket(rows: list[tuple[str, float]]) -> list[dict[str, Any]]:
    g: dict[str, list[float]] = defaultdict(list)
    for k, p in rows:
        g[k].append(p)
    out = []
    for k, v in g.items():
        wins = [x for x in v if x > 0]
        losses = -sum(x for x in v if x < 0)
        out.append({"key": k, "n": len(v), "pnl": _r(sum(v)), "avg": _r(sum(v) / len(v)),
                    "win_rate": _r(100 * len(wins) / len(v), 1), "pf": _r(sum(wins) / losses) if losses else None})
    return out


# ------------------------------------------------------------------ statistics
def _performance(trades: list[dict[str, Any]], start: float) -> dict[str, Any]:
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    n = len(pnls)
    if not n:
        return {"n": 0}
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    gross_w, gross_l = float(wins.sum()), float(-losses.sum())
    equity = start + np.cumsum(pnls)
    peak = np.maximum.accumulate(np.concatenate([[start], equity]))[1:]
    dd = peak - equity
    dd_pct = dd / peak * 100
    # longest stretch (in trades) spent below a prior peak
    longest, run = 0, 0
    for d in dd:
        run = run + 1 if d > 0 else 0
        longest = max(longest, run)
    # daily series for Sharpe/Sortino
    daily: dict[dt.date, float] = defaultdict(float)
    for t in trades:
        d = _dt(t["exit_time"])
        if d:
            daily[d.date()] += t["pnl"]
    dv = np.array(list(daily.values()), dtype=float) if daily else np.array([])
    sharpe = sortino = None
    if len(dv) >= 5 and dv.std(ddof=1) > 0:
        sharpe = float(dv.mean() / dv.std(ddof=1) * math.sqrt(252))
        down = dv[dv < 0]
        if len(down) >= 2 and down.std(ddof=1) > 0:
            sortino = float(dv.mean() / math.sqrt(float((down ** 2).mean())) * math.sqrt(252))
    hold = []
    for t in trades:
        a, b = _dt(t.get("entry_time")), _dt(t.get("exit_time"))
        if a and b:
            hold.append((b - a).total_seconds() / 60)
    comm = sum(float(t.get("commission") or 0) for t in trades)
    risk = [t["pnl"] / t["risk_dollars"] for t in trades if t.get("risk_dollars")]
    max_dd = float(dd.max())
    total = float(pnls.sum())
    win_rate = len(wins) / n
    avg_w = float(wins.mean()) if len(wins) else 0.0
    avg_l = float(-losses.mean()) if len(losses) else 0.0
    return {
        "n": n, "net": _r(total), "win_rate": _r(100 * win_rate, 1), "loss_rate": _r(100 * len(losses) / n, 1),
        "profit_factor": _r(gross_w / gross_l) if gross_l else None,
        "expectancy": _r(total / n), "avg_win": _r(avg_w), "avg_loss": _r(-avg_l if avg_l else 0.0),
        "payoff": _r(avg_w / avg_l) if avg_l else None,
        "breakeven_win_rate": _r(100 * avg_l / (avg_w + avg_l), 1) if (avg_w + avg_l) else None,
        "best": _r(float(pnls.max())), "worst": _r(float(pnls.min())), "std": _r(float(pnls.std(ddof=1))) if n > 1 else None,
        "max_drawdown": _r(max_dd), "max_drawdown_pct": _r(float(dd_pct.max()), 2),
        "current_drawdown": _r(float(dd[-1])), "longest_drawdown_trades": longest,
        "recovery_factor": _r(total / max_dd) if max_dd else None, "calmar": _r(total / max_dd) if max_dd else None,
        "sharpe": _r(sharpe), "sortino": _r(sortino), "trading_days": len(daily),
        "trades_per_day": _r(n / len(daily), 2) if daily else None,
        "best_day": _r(max(daily.values())) if daily else None, "worst_day": _r(min(daily.values())) if daily else None,
        "green_days": sum(1 for v in daily.values() if v > 0), "red_days": sum(1 for v in daily.values() if v < 0),
        "avg_hold_min": _r(float(np.mean(hold)), 1) if hold else None, "commission": _r(comm),
        "commission_pct_of_gross": _r(100 * comm / gross_w, 1) if gross_w else None,
        "avg_r": _r(float(np.mean(risk)), 2) if risk else None,
        "r_ge_1_pct": _r(100 * sum(1 for x in risk if x >= 1) / len(risk), 1) if risk else None,
        "streaks": _streaks(list(pnls)),
    }


def _breakdowns(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_hour, by_dow, by_side, by_exit, by_month = [], [], [], [], []
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        ent, ex = _dt(t.get("entry_time")), _dt(t.get("exit_time"))
        p = t["pnl"]
        if ent:
            by_hour.append((f"{ent.hour:02d}", p))
            by_dow.append((str(ent.weekday()), p))
        if ex:
            by_month.append((ex.strftime("%Y-%m"), p))
            daily[ex.date().isoformat()] += p
        by_side.append((t.get("side") or "?", p))
        by_exit.append((t.get("exit_reason") or "?", p))
    hour = sorted(_bucket(by_hour), key=lambda x: x["key"])
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow = sorted(_bucket(by_dow), key=lambda x: int(x["key"]))
    for d in dow:
        d["key"] = dow_names[int(d["key"])]
    days = sorted(daily.items())[-90:]
    return {
        "hour": hour, "weekday": dow, "side": sorted(_bucket(by_side), key=lambda x: -x["n"]),
        "exit_reason": sorted(_bucket(by_exit), key=lambda x: -x["n"]), "month": sorted(_bucket(by_month), key=lambda x: x["key"]),
        "daily": [{"date": d, "pnl": _r(v)} for d, v in days],
    }


def _histogram(pnls: np.ndarray, bins: int = 18) -> dict[str, Any]:
    if len(pnls) < 3:
        return {"edges": [], "counts": []}
    lo, hi = float(pnls.min()), float(pnls.max())
    if hi - lo < 1e-9:
        hi = lo + 1
    counts, edges = np.histogram(pnls, bins=bins, range=(lo, hi))
    return {"edges": [_r(float(e)) for e in edges], "counts": [int(c) for c in counts]}


# ------------------------------------------------------------------ Monte Carlo
def _simulate(cum: np.ndarray, start: float, target: float, max_dd: float, scale: float) -> dict[str, Any]:
    """Run every path (rows of `cum`, the cumulative sum of the drawn trade P&Ls) until pass, breach or the horizon.
    Scaling every trade by `scale` scales the cumulative sum by the same factor, so one draw serves every size."""
    eq = start + cum * scale                                         # (paths, horizon)
    peak = np.maximum.accumulate(np.maximum(eq, start), axis=1)
    floor = np.minimum(peak - max_dd, start)                        # apex_rules.compute_floor, vectorised
    breach = eq <= floor
    passed = eq >= start + target
    big = cum.shape[1] + 1
    b_idx = np.where(breach.any(axis=1), breach.argmax(axis=1), big)
    p_idx = np.where(passed.any(axis=1), passed.argmax(axis=1), big)
    # breach wins a tie within the same trade (the live engine checks the floor first)
    won = p_idx < b_idx
    lost = (b_idx <= p_idx) & (b_idx < big)
    unresolved = ~(won | lost)
    return {
        "pass": float(won.mean()), "breach": float(lost.mean()), "unresolved": float(unresolved.mean()),
        "trades_to_pass": (p_idx[won] + 1) if won.any() else np.array([]),
        "trades_to_breach": (b_idx[lost] + 1) if lost.any() else np.array([]),
        "eq": eq, "paths": cum.shape[0],
    }


def _monte_carlo(pool: np.ndarray, start: float, target: float, max_dd: float, trades_per_day: float | None,
                 paths: int, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(pool), size=(paths, HORIZON))
    cum = np.cumsum(pool[draws], axis=1)
    base = _simulate(cum, start, target, max_dd, 1.0)
    tpd = trades_per_day or 1.0

    def pct(a: np.ndarray, q: float) -> float | None:
        return _r(float(np.percentile(a, q)), 1) if len(a) else None

    eq = base["eq"][:, :FAN_POINTS]
    fan = {f"p{q}": [_r(float(v), 0) for v in np.percentile(eq, q, axis=0)] for q in (5, 25, 50, 75, 95)}
    # max drawdown over the first 100 trades of every path
    e100 = base["eq"][:, :100]
    pk = np.maximum.accumulate(np.maximum(e100, start), axis=1)
    dd100 = (pk - e100).max(axis=1)
    sweep = []
    for s in SCALES:
        r = _simulate(cum, start, target, max_dd, s) if s != 1.0 else base
        sweep.append({"scale": s, "pass": _r(100 * r["pass"], 1), "breach": _r(100 * r["breach"], 1),
                      "unresolved": _r(100 * r["unresolved"], 1),
                      "median_trades": pct(r["trades_to_pass"], 50)})
    best = max(sweep, key=lambda x: (x["pass"] - x["breach"]) if x["pass"] is not None else -1e9)
    # bootstrap the mean to get the chance the edge is real (mean > 0)
    means = pool[rng.integers(0, len(pool), size=(2000, len(pool)))].mean(axis=1)
    return {
        "paths": paths, "horizon": HORIZON, "target": target, "max_drawdown": max_dd, "start": start,
        "pass_pct": _r(100 * base["pass"], 1), "breach_pct": _r(100 * base["breach"], 1),
        "unresolved_pct": _r(100 * base["unresolved"], 1),
        "median_trades_to_pass": pct(base["trades_to_pass"], 50), "p10_trades_to_pass": pct(base["trades_to_pass"], 10),
        "p90_trades_to_pass": pct(base["trades_to_pass"], 90),
        "median_days_to_pass": _r(float(np.percentile(base["trades_to_pass"], 50)) / tpd, 1) if len(base["trades_to_pass"]) else None,
        "median_trades_to_breach": pct(base["trades_to_breach"], 50),
        "fan": fan, "fan_points": FAN_POINTS,
        "dd100": {"p50": _r(float(np.percentile(dd100, 50)), 0), "p90": _r(float(np.percentile(dd100, 90)), 0),
                  "p99": _r(float(np.percentile(dd100, 99)), 0)},
        "end100": {"p5": fan["p5"][min(99, FAN_POINTS - 1)], "p50": fan["p50"][min(99, FAN_POINTS - 1)], "p95": fan["p95"][min(99, FAN_POINTS - 1)]},
        "sweep": sweep, "best_scale": best["scale"],
        "edge_probability": _r(100 * float((means > 0).mean()), 1),
        "mean_ci": [_r(float(np.percentile(means, 5))), _r(float(np.percentile(means, 95)))],
    }


# ------------------------------------------------------------------ findings
def _confidence(n: int) -> str:
    return "high" if n >= 100 else "medium" if n >= 40 else "low" if n >= 15 else "very low"


def _findings(acct: dict[str, Any], perf: dict[str, Any], brk: dict[str, Any], mc: dict[str, Any] | None,
              sample: dict[str, Any], start: float) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def add(level: str, title: str, text: str) -> None:
        out.append({"level": level, "title": title, "text": text})

    n = perf.get("n", 0)
    if n == 0:
        add("info", "No trades yet", "Nothing to analyse. Once the account closes trades this page fills with statistics and a Monte Carlo.")
        return out
    if sample["source"] != "own":
        add("warn", "Small sample — borrowed data",
            f"This account has only {n} closed trades, so the Monte Carlo below uses the {sample['n']} pooled trades of every account "
            f"running {acct.get('strategy')} on {acct.get('symbol')}. Treat it as the strategy's odds, not this account's.")
    elif n < 40:
        add("warn", "Small sample", f"{n} trades is thin — confidence in every number here is {_confidence(n)}.")

    if mc:
        ep = mc["edge_probability"]
        if ep >= 90:
            add("good", "Edge looks real", f"In {ep}% of bootstrap resamples the average trade is positive (90% range {mc['mean_ci'][0]:+} to {mc['mean_ci'][1]:+} per trade).")
        elif ep <= 30:
            add("bad", "No proven edge", f"Only {ep}% of resamples have a positive average trade (90% range {mc['mean_ci'][0]:+} to {mc['mean_ci'][1]:+} per trade). This behaves like noise or a losing system.")
        else:
            add("info", "Edge unproven", f"{ep}% of resamples have a positive average trade — could be skill, could be luck; more trades are needed.")
        add("good" if mc["pass_pct"] >= 50 and mc["breach_pct"] < 40 else "bad" if mc["breach_pct"] > mc["pass_pct"] else "info",
            "Evaluation odds",
            f"Monte Carlo ({mc['paths']:,} paths): {mc['pass_pct']}% reach the ${mc['target']:,.0f} target, {mc['breach_pct']}% hit the "
            f"${mc['max_drawdown']:,.0f} trailing drawdown first, {mc['unresolved_pct']}% neither within {mc['horizon']} trades."
            + (f" A pass takes about {mc['median_trades_to_pass']:.0f} trades (~{mc['median_days_to_pass']} trading days)." if mc["median_trades_to_pass"] else ""))
        cur = next((s for s in mc["sweep"] if s["scale"] == 1.0), None)
        best = next((s for s in mc["sweep"] if s["scale"] == mc["best_scale"]), None)
        if cur and best and best["scale"] != 1.0 and (best["pass"] - best["breach"]) > (cur["pass"] - cur["breach"]) + 4:
            add("tip", f"Size {'down' if best['scale'] < 1 else 'up'} to {best['scale']}×",
                f"Scaling every trade's size to {best['scale']}× moves the pass chance from {cur['pass']}% to {best['pass']}% and the breach chance "
                f"from {cur['breach']}% to {best['breach']}%. Position size, not the entry logic, is the biggest lever on evaluation odds.")

    pf, wr, be = perf.get("profit_factor"), perf.get("win_rate"), perf.get("breakeven_win_rate")
    if be is not None and wr is not None and n >= 15:
        if wr < be:
            add("bad", "Win rate is below break-even",
                f"With an average win of ${perf['avg_win']:,.0f} and average loss of ${abs(perf['avg_loss']):,.0f}, you need {be}% winners to break even but only win {wr}%. "
                "Either the stops are too wide relative to the targets or the entries are too weak.")
        elif wr - be > 8:
            add("good", "Comfortable win-rate cushion", f"Win rate {wr}% vs {be}% needed to break even.")
    if perf.get("commission_pct_of_gross") and perf["commission_pct_of_gross"] > 15:
        add("warn", "Commissions are eating the edge", f"Commissions are {perf['commission_pct_of_gross']}% of gross profit (${perf['commission']:,.0f}). Fewer, larger-expectancy trades would help.")
    st = perf["streaks"]
    if st["max_loss"] >= 5:
        add("warn", "Long losing streaks", f"Worst losing streak: {st['max_loss']} in a row. On a trailing-drawdown evaluation, size so that this streak cannot reach the floor.")
    if acct.get("drawdown_floor") is not None and acct["account_type"] == "apex_eval" and acct.get("eval_status") == "in_progress":
        buf = acct["balance"] - acct["drawdown_floor"]
        if acct.get("max_drawdown") and buf < 0.25 * acct["max_drawdown"]:
            add("bad", "Close to the drawdown floor", f"Only ${buf:,.0f} of buffer is left ({100 * buf / acct['max_drawdown']:.0f}% of the allowed drawdown).")

    # filters that would have helped: buckets with enough trades and a clearly negative total
    bad_hours = [h for h in brk["hour"] if h["n"] >= MIN_CELL and h["pnl"] < 0 and (h["pf"] or 0) < 0.8]
    if bad_hours and n >= 30:
        saved = -sum(h["pnl"] for h in bad_hours)
        hrs = ", ".join(f"{h['key']}:00 (${h['pnl']:,.0f} / {h['n']})" for h in sorted(bad_hours, key=lambda x: x["pnl"])[:4])
        add("tip", "Hours that lose money", f"{hrs}. Skipping these hours would have added about ${saved:,.0f} in this sample — verify on more data before filtering.")
    good_hours = [h for h in brk["hour"] if h["n"] >= MIN_CELL and h["pnl"] > 0 and (h["pf"] or 0) > 1.5]
    if good_hours and n >= 30:
        h = max(good_hours, key=lambda x: x["pnl"])
        add("info", "Best hour", f"{h['key']}:00 NY — ${h['pnl']:,.0f} over {h['n']} trades, profit factor {h['pf']}.")
    for s in brk["side"]:
        if s["n"] >= 8 and s["pnl"] < 0 and len(brk["side"]) > 1 and sum(x["pnl"] for x in brk["side"]) > -abs(s["pnl"]) * 0.5:
            add("tip", f"{s['key'].title()} trades lose", f"{s['key'].title()} side: ${s['pnl']:,.0f} over {s['n']} trades (PF {s['pf']}). The other side carries the account.")
    stops = next((e for e in brk["exit_reason"] if "stop" in e["key"]), None)
    if stops and n >= 20 and stops["n"] / n > 0.6:
        add("info", "Mostly stopped out", f"{100 * stops['n'] / n:.0f}% of trades exit at the stop. Consider whether the stop sits inside normal noise.")
    if not out:
        add("info", "Nothing stands out", "No warnings triggered on the current data.")
    return out


def _verdict(perf: dict[str, Any], mc: dict[str, Any] | None, sample: dict[str, Any]) -> dict[str, str]:
    n = perf.get("n", 0)
    if not n or not mc:
        return {"code": "none", "text": "NO DATA"}
    ep, exp = mc["edge_probability"], perf["expectancy"] or 0
    if sample["source"] != "own" or n < 15:
        return {"code": "unproven", "text": "TOO EARLY TO JUDGE"}
    if ep >= 90 and exp > 0 and (perf["profit_factor"] or 0) > 1.15:
        return {"code": "good", "text": "EDGE CONFIRMED" if n >= 60 else "PROMISING EDGE"}
    if ep <= 25 or exp < 0:
        return {"code": "bad", "text": "NEGATIVE EDGE"}
    return {"code": "unproven", "text": "EDGE UNPROVEN"}


# ------------------------------------------------------------------ entry point
def build(account_id: int, paths: int = 4000) -> dict[str, Any]:
    acct = db.get_account(account_id)
    if not acct:
        raise KeyError(account_id)
    trades = db.get_account_trades(account_id)
    key = (account_id, len(trades), acct["balance"], paths)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 45:
        return hit[1]

    start = float(acct["starting_balance"])
    perf = _performance(trades, start)
    brk = _breakdowns(trades)
    pnls = np.array([t["pnl"] for t in trades], dtype=float)

    # the pool the Monte Carlo resamples
    sample = {"source": "own", "n": len(trades), "label": "this account's trades"}
    pool = pnls
    if len(trades) < MIN_OWN_TRADES and acct.get("strategy") and acct.get("symbol"):
        with db.get_conn() as conn:
            rows = conn.execute("SELECT pnl FROM account_trades WHERE strategy = ? AND symbol = ? AND source = 'live'",
                                (acct["strategy"], acct["symbol"])).fetchall()
            # trades archived when every account was restarted from $50k still describe the strategy
            try:
                rows += conn.execute("SELECT pnl FROM account_trades_archive WHERE strategy = ? AND symbol = ? AND source = 'live'",
                                     (acct["strategy"], acct["symbol"])).fetchall()
            except Exception:  # noqa: BLE001 - the archive table only exists after a restart
                pass
        pooled = np.array([r["pnl"] for r in rows], dtype=float)
        if len(pooled) > len(trades):
            pool = pooled
            sample = {"source": "pooled", "n": len(pooled), "label": f"pooled {acct['strategy']} / {acct['symbol']} live trades, incl. those from before the $50k restart"}

    # evaluation geometry: the account's own, else the closest Apex tier, else 6% / 4% of the balance
    tier = TIERS.get(int(start))
    target = float(acct.get("profit_target") or (tier["profit_target"] if tier else 0.06 * start))
    max_dd = float(acct.get("max_drawdown") or (tier["max_drawdown"] if tier else 0.04 * start))
    mc = None
    if len(pool) >= 5:
        mc = _monte_carlo(pool, start, target, max_dd, perf.get("trades_per_day"), paths, seed=account_id)
        mc["sample"] = sample
        mc["confidence"] = _confidence(sample["n"])
        mc["hypothetical"] = acct["account_type"] != "apex_eval"

    curve = [{"i": 0, "t": None, "eq": start}]
    for i, t in enumerate(trades, 1):
        curve.append({"i": i, "t": t["exit_time"], "eq": _r(t["equity_after"] if t["equity_after"] is not None else curve[-1]["eq"] + t["pnl"])})
    if len(curve) > 400:  # keep the payload small
        step = math.ceil(len(curve) / 400)
        curve = curve[::step] + ([curve[-1]] if (len(curve) - 1) % step else [])

    out = {
        "account": {k: acct.get(k) for k in ("id", "name", "symbol", "strategy", "account_type", "status", "eval_status", "starting_balance",
                                              "balance", "profit_target", "max_drawdown", "peak_equity", "drawdown_floor",
                                              "daily_loss_limit", "eval_fail_reason", "sizing_mode", "risk_per_trade", "created_at", "is_system",
                                              "traderspost_linked", "tradovate_account_name", "payout_target", "awaiting_payout")},
        "perf": perf, "breakdown": brk, "histogram": _histogram(pnls), "curve": curve, "monte_carlo": mc,
        "sample": sample, "confidence": _confidence(len(trades)), "verdict": _verdict(perf, mc, sample),
        "findings": _findings(acct, perf, brk, mc, sample, start), "generated_at": time.time(),
    }
    _cache[key] = (time.time(), out)
    if len(_cache) > 60:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:20]:
            _cache.pop(k, None)
    return out


# ------------------------------------------------------------------ strategy level (STRATEGY LAB)
def _pooled_trades(strategy: str, symbol: str) -> list[dict[str, Any]]:
    """Every closed trade this strategy+symbol ever produced in any account (live, simulated, and archived before resets),
    de-duplicated on (entry, exit, side) so accounts running identical settings are not double counted."""
    rows: list[dict[str, Any]] = []
    with db.get_conn() as conn:
        for table in ("account_trades", "account_trades_archive"):
            try:
                rows += [dict(r) for r in conn.execute(
                    f"SELECT entry_time, exit_time, side, pnl, equity_after, exit_reason, commission, risk_dollars, source FROM {table} "
                    "WHERE strategy = ? AND symbol = ? AND source IN ('live', 'sim')", (strategy, symbol)).fetchall()]
            except Exception:  # noqa: BLE001 - archive table may not exist
                pass
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: str(r["exit_time"])):
        k = (r["entry_time"], r["exit_time"], r["side"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def strategy_board() -> list[dict[str, Any]]:
    """One row per strategy+symbol: pooled trade stats, a quick Monte Carlo, the trainer's view and its recommended risk."""
    from app.accounts import risk_sync

    recs = risk_sync.recommendations()
    with db.get_conn() as conn:
        pairs = {(r["strategy"], r["symbol"]) for r in conn.execute("SELECT DISTINCT strategy, symbol FROM account_trades WHERE strategy IS NOT NULL").fetchall()}
        try:
            pairs |= {(r["strategy"], r["symbol"]) for r in conn.execute("SELECT DISTINCT strategy, symbol FROM account_trades_archive WHERE strategy IS NOT NULL").fetchall()}
        except Exception:  # noqa: BLE001
            pass
        stats = {(r["strategy"], r["symbol"]): dict(r) for r in conn.execute("SELECT * FROM strategy_stats").fetchall()}
        champs = {(r["strategy"], r["symbol"]): r["tier"] for r in conn.execute("SELECT strategy, symbol, tier FROM champion_slots").fetchall()}
    try:
        from app.optimizer.trainer_service import TRAIN_ROSTER
    except Exception:  # noqa: BLE001
        TRAIN_ROSTER = {}
    out = []
    for strat, sym in sorted(pairs):
        tr = _pooled_trades(strat, sym)
        perf = _performance(tr, 50_000.0) if tr else {"n": 0}
        pool = np.array([t["pnl"] for t in tr], dtype=float)
        mc = _monte_carlo(pool, 50_000.0, 3000.0, 2000.0, perf.get("trades_per_day"), 1000, seed=hash((strat, sym)) % 2**31) if len(pool) >= 5 else None
        st = stats.get((strat, sym), {})
        rec = (recs.get(sym) or {}).get(strat)
        roster = TRAIN_ROSTER.get(sym)
        out.append({
            "strategy": strat, "symbol": sym, "n": perf.get("n", 0), "net": perf.get("net"), "win_rate": perf.get("win_rate"),
            "profit_factor": perf.get("profit_factor"), "expectancy": perf.get("expectancy"), "max_drawdown": perf.get("max_drawdown"),
            "sharpe": perf.get("sharpe"), "pass_pct": mc and mc["pass_pct"], "breach_pct": mc and mc["breach_pct"],
            "edge_probability": mc and mc["edge_probability"], "best_scale": mc and mc["best_scale"],
            "trainer_score": st.get("best_score"), "trainer_peak": st.get("peak_score_all_epochs"), "trainer_apex_pass": st.get("apex_last_pass_rate"),
            "champion": champs.get((strat, sym)), "in_training": roster is None or strat in roster,
            "rec_risk": rec and rec["risk"], "rec_pass": rec and rec.get("pass"), "rec_breach": rec and rec.get("breach"),
        })
    return out


def strategy_insights(strategy: str, symbol: str, paths: int = 4000) -> dict[str, Any]:
    tr = _pooled_trades(strategy, symbol)
    perf = _performance(tr, 50_000.0)
    pool = np.array([t["pnl"] for t in tr], dtype=float)
    mc = _monte_carlo(pool, 50_000.0, 3000.0, 2000.0, perf.get("trades_per_day"), paths, seed=7) if len(pool) >= 5 else None
    if mc:
        mc["sample"] = {"source": "pooled", "n": len(pool), "label": f"every {strategy} / {symbol} trade on record"}
        mc["confidence"] = _confidence(len(pool))
        mc["hypothetical"] = False
    eq, curve = 50_000.0, [{"i": 0, "t": None, "eq": 50_000.0}]
    for i, t in enumerate(tr, 1):
        eq += t["pnl"]
        curve.append({"i": i, "t": t["exit_time"], "eq": round(eq, 2)})
    if len(curve) > 400:
        step = math.ceil(len(curve) / 400)
        curve = curve[::step] + ([curve[-1]] if (len(curve) - 1) % step else [])
    return {"strategy": strategy, "symbol": symbol, "perf": perf, "breakdown": _breakdowns(tr), "histogram": _histogram(pool),
            "monte_carlo": mc, "curve": curve, "confidence": _confidence(len(tr))}
