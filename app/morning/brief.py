"""The morning brief: 3 hot stocks + the NOSTRO report, sent to Telegram at 8:30 New York time every day.

HOT STOCKS
  Universe = the screener snapshot (market_routes.UNIVERSE, ~300 liquid US names, refreshed every 30 min). For each name:
      gap        pre-market % move (or the last session's move when there is no pre-market, e.g. weekends)
      volume     relative volume of the last session (x its 30-day average)
      news       headlines in the last 18 h (48 h on weekends) mentioning the ticker
      momentum   1-month % change
      analysts   target upside % scaled by how bullish the consensus rating is
  Every input is winsorised and turned into a cross-sectional z-score; the heat score is a fixed weighted sum
  (gap 0.35, volume 0.20, news 0.20, momentum 0.15, analysts 0.10) on |gap| so big DOWN moves are hot too.
  Filters: price >= $5, 30-day average dollar volume >= $20M. Top 3, at most one per sector when possible.
  This is a "what is moving and why" list, not a buy list — the brief says so, and the track record keeps it honest.

TRACK RECORD
  Each brief stores its picks with the reference price. track() later fills 1/3/5-trading-day returns (in the direction of the
  morning move) so the brief can show the running hit rate.

NOSTRO REPORT
  Yesterday's closed trades and P&L across every account, evaluation status, the accounts with the best Monte Carlo pass odds,
  trainer champion changes in the last 24 h, and the Polymarket real-money P&L.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import math
import statistics
import time
from typing import Any
from zoneinfo import ZoneInfo

from app import db

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
WEIGHTS = {"gap": 0.35, "volume": 0.20, "news": 0.20, "momentum": 0.15, "analysts": 0.10}
MIN_PRICE, MIN_DOLLAR_VOL = 5.0, 20e6
NEWS_CANDIDATES = 40          # news is only fetched for the top names by the cheap factors (it costs an HTTP call each)
SCHEMA = """CREATE TABLE IF NOT EXISTS morning_briefs (
    day TEXT PRIMARY KEY, created_at REAL NOT NULL, kind TEXT NOT NULL, picks_json TEXT NOT NULL,
    nostro_json TEXT NOT NULL, text TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0)"""


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)
        db._ensure_column(conn, "morning_briefs", "catalysts_json", "TEXT NOT NULL DEFAULT '{}'")


# ------------------------------------------------------------------ data
def _quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Yahoo v7 batch quote: pre/post-market fields that the chart endpoint does not carry."""
    from app.api import invest_routes as inv

    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(symbols), 50):
        chunk = symbols[i:i + 50]
        try:
            j = inv._yget(f"{inv.Y2}/v7/finance/quote", {"symbols": ",".join(chunk)})
            for q in (j.get("quoteResponse") or {}).get("result") or []:
                out[q["symbol"]] = q
        except Exception:  # noqa: BLE001 - a failed chunk just loses its pre-market data
            logger.warning("quote chunk failed: %s", chunk[:3])
    return out


def _news_count(sym: str, hours: float) -> tuple[int, list[dict[str, Any]]]:
    from app.api import invest_routes as inv

    try:
        items = inv._news(sym, True)
    except Exception:  # noqa: BLE001
        return 0, []
    cutoff = time.time() - hours * 3600
    recent = [i for i in items if (i.get("ts") or 0) >= cutoff]
    return len(recent), recent


def _z(vals: dict[str, float | None]) -> dict[str, float]:
    xs = [v for v in vals.values() if v is not None and math.isfinite(v)]
    if len(xs) < 5:
        return {k: 0.0 for k in vals}
    lo, hi = sorted(xs)[int(0.02 * len(xs))], sorted(xs)[int(0.98 * len(xs)) - 1]
    clip = [min(hi, max(lo, x)) for x in xs]
    mu, sd = statistics.mean(clip), statistics.pstdev(clip) or 1.0
    return {k: ((min(hi, max(lo, v)) - mu) / sd if v is not None and math.isfinite(v) else 0.0) for k, v in vals.items()}


def hot_stocks(n: int = 3) -> dict[str, Any]:
    from app.api import market_routes as mr

    mr._load_snapshot()
    snap = dict(mr._SNAP)
    now = dt.datetime.now(NY)
    weekend = now.weekday() >= 5
    news_hours = 48 if weekend or now.weekday() == 0 else 18
    rows = [dict(r) for r in snap.values() if (r.get("price") or 0) >= MIN_PRICE and (r.get("avg_volume") or 0) * (r.get("price") or 0) >= MIN_DOLLAR_VOL]
    quotes = _quotes([r["symbol"] for r in rows])

    gap: dict[str, float | None] = {}
    for r in rows:
        q = quotes.get(r["symbol"], {})
        pm = q.get("preMarketChangePercent")
        if pm is None and q.get("marketState") in ("POST", "POSTPOST", "CLOSED") and q.get("postMarketChangePercent") is not None and not weekend:
            pm = q.get("postMarketChangePercent")
        r["_gap_src"] = "pre-market" if q.get("preMarketChangePercent") is not None else "after-hours" if pm is not None else "last session"
        r["_gap"] = pm if pm is not None else (q.get("regularMarketChangePercent") if q.get("regularMarketChangePercent") is not None else r.get("pct"))
        r["_px"] = q.get("preMarketPrice") or q.get("postMarketPrice") or q.get("regularMarketPrice") or r.get("price")
        gap[r["symbol"]] = abs(r["_gap"]) if r["_gap"] is not None else None
    zg = _z(gap)
    zv = _z({r["symbol"]: math.log(r["rel_volume"]) if r.get("rel_volume") and r["rel_volume"] > 0 else None for r in rows})
    zm = _z({r["symbol"]: abs(r["p1m"]) if r.get("p1m") is not None else None for r in rows})
    za = _z({r["symbol"]: (r.get("target_upside") or 0) * (max(0.0, 3.2 - r["rating"]) if r.get("rating") else 0.5) for r in rows})
    pre = {r["symbol"]: WEIGHTS["gap"] * zg[r["symbol"]] + WEIGHTS["volume"] * zv[r["symbol"]] + WEIGHTS["momentum"] * zm[r["symbol"]]
           + WEIGHTS["analysts"] * za[r["symbol"]] for r in rows}

    # news is expensive: only for the best candidates by the cheap factors
    cand = sorted(rows, key=lambda r: -pre[r["symbol"]])[:NEWS_CANDIDATES]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        news = dict(zip([r["symbol"] for r in cand], pool.map(lambda r: _news_count(r["symbol"], news_hours), cand)))
    zn = _z({s: float(c) for s, (c, _) in news.items()})
    scored = []
    for r in cand:
        s = r["symbol"]
        f = {"gap": zg[s], "volume": zv[s], "news": zn.get(s, 0.0), "momentum": zm[s], "analysts": za[s]}
        heat = sum(WEIGHTS[k] * v for k, v in f.items())
        scored.append((heat, r, f))
    scored.sort(key=lambda x: -x[0])

    picks, sectors = [], set()
    for heat, r, f in scored:  # one per sector first
        if len(picks) >= n:
            break
        if r.get("sector") in sectors:
            continue
        picks.append((heat, r, f)); sectors.add(r.get("sector"))
    for heat, r, f in scored:  # fill up if there were not enough sectors
        if len(picks) >= n:
            break
        if all(p[1]["symbol"] != r["symbol"] for p in picks):
            picks.append((heat, r, f))

    out = []
    for heat, r, f in picks:
        cnt, heads = news.get(r["symbol"], (0, []))
        out.append({
            "symbol": r["symbol"], "name": r.get("name"), "sector": r.get("sector"), "heat": round(heat, 2),
            "factors": {k: round(v, 2) for k, v in f.items()}, "move_pct": round(r["_gap"], 2) if r.get("_gap") is not None else None,
            "move_src": r["_gap_src"], "ref_price": r.get("_px"), "prev_close": r.get("price"), "rel_volume": r.get("rel_volume"),
            "p1m": r.get("p1m"), "rsi14": r.get("rsi14"), "from_high": r.get("from_high"), "target_upside": r.get("target_upside"),
            "rating": r.get("rating"), "n_news": cnt, "headlines": [{"title": h["title"], "source": h.get("source"), "link": h.get("link")} for h in heads[:4]],
            "why": _why(r, heads),
        })
    return {"kind": "weekend watchlist" if weekend else "pre-market", "picks": out, "universe": len(rows), "news_hours": news_hours,
            "snapshot_age_min": round((time.time() - mr._snap_state.get("at", 0)) / 60) if mr._snap_state.get("at") else None}


def _why(r: dict[str, Any], heads: list[dict[str, Any]]) -> str:
    """One line on why it is moving: the LLM when configured (short prompt), otherwise the top headline + the numbers."""
    import os

    move = r.get("_gap")
    bits = [f"{'up' if (move or 0) >= 0 else 'down'} {abs(move or 0):.1f}% {r.get('_gap_src', '')}".strip()]
    if r.get("rel_volume"):
        bits.append(f"{r['rel_volume']:.1f}x volume")
    if os.environ.get("AI_API_KEY") and heads:
        try:
            import requests

            base = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            model = os.environ.get("AI_MODEL", "gpt-4o-mini")
            prompt = (f"In one sentence (max 25 words), say why {r['symbol']} is moving ({bits[0]}), using only these headlines. "
                      "If they do not explain it, say so. No advice.\n" + "\n".join(f"- {h['title']}" for h in heads[:8]))
            resp = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {os.environ['AI_API_KEY']}"},
                                 json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 70}, timeout=25)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:  # noqa: BLE001 - fall through to the rule-based line
            pass
    if heads:
        return f"{', '.join(bits)}. Top headline: {heads[0]['title']}"
    return f"{', '.join(bits)}. No fresh headlines — technical/flow move."


# ------------------------------------------------------------------ NOSTRO report
def nostro_report() -> dict[str, Any]:
    from app.accounts import insights

    now = dt.datetime.now(NY)
    since = now - dt.timedelta(hours=24)
    accts = db.list_accounts()
    with db.get_conn() as conn:
        trades = [dict(r) for r in conn.execute("SELECT account_id, exit_time, pnl, strategy, symbol, source FROM account_trades WHERE source IN ('live','sim')").fetchall()]
        champs = [dict(r) for r in conn.execute("SELECT ts, symbol, tier, strategy, score, reason FROM champion_history WHERE ts >= datetime('now', '-1 day') ORDER BY ts DESC").fetchall()]
    recent = []
    for t in trades:
        try:
            if t["source"] == "live" and dt.datetime.fromisoformat(t["exit_time"]).astimezone(NY) >= since:
                recent.append(t)
        except (ValueError, TypeError):
            pass
    by_strat: dict[str, float] = {}
    for t in recent:
        by_strat[f"{t['strategy']} {t['symbol']}"] = by_strat.get(f"{t['strategy']} {t['symbol']}", 0.0) + t["pnl"]
    evals = [a for a in accts if a["account_type"] == "apex_eval"]
    closest = sorted([a for a in evals if a.get("eval_status") == "in_progress" and a.get("profit_target")],
                     key=lambda a: -(a["balance"] - a["starting_balance"]) / a["profit_target"])[:3]
    odds = []
    for a in accts:
        if not a.get("strategy"):
            continue
        try:
            mc = insights.build(a["id"], paths=1500).get("monte_carlo")
        except Exception:  # noqa: BLE001
            mc = None
        if mc:
            odds.append({"id": a["id"], "name": a["name"], "strategy": a["strategy"], "pass": mc["pass_pct"], "breach": mc["breach_pct"], "n": mc["sample"]["n"]})
    odds.sort(key=lambda o: -(o["pass"] - o["breach"]))
    pm = {}
    try:
        from app.polymarket import real_trading as rt

        s = rt.status(check_balance=False)
        pm = {"today": s.get("today"), "armed": (s.get("settings") or {}).get("armed"), "balance": s.get("balance")}
    except Exception:  # noqa: BLE001
        pass
    return {
        "trades_24h": len(recent), "pnl_24h": round(sum(t["pnl"] for t in recent), 2),
        "by_strategy": sorted(({"k": k, "pnl": round(v, 2)} for k, v in by_strat.items()), key=lambda x: -x["pnl"]),
        "accounts": len(accts), "live": sum(1 for a in accts if a["status"] == "live"),
        "passed": sum(1 for a in evals if a.get("eval_status") == "passed"), "failed": sum(1 for a in evals if a.get("eval_status") == "failed"),
        "in_eval": sum(1 for a in evals if a.get("eval_status") == "in_progress"),
        "closest": [{"id": a["id"], "name": a["name"], "profit": round(a["balance"] - a["starting_balance"], 2), "target": a["profit_target"],
                     "buffer": round(a["balance"] - a["drawdown_floor"], 2) if a.get("drawdown_floor") is not None else None} for a in closest],
        "best_odds": odds[:3], "champions": champs[:5], "polymarket": pm,
    }


# ------------------------------------------------------------------ track record
def track() -> dict[str, Any]:
    """Fill realised returns for past picks (direction-adjusted: a pick that gapped down 'wins' if it kept falling)."""
    from app.api import invest_routes as inv

    ensure_schema()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT day, picks_json FROM morning_briefs ORDER BY day DESC LIMIT 40").fetchall()]
    for row in rows:
        picks = json.loads(row["picks_json"])
        changed = False
        for p in picks:
            if p.get("r5") is not None or not p.get("ref_price"):
                continue
            try:
                h = inv._history(p["symbol"], "1mo", "1d")
            except Exception:  # noqa: BLE001
                continue
            closes = [(dt.datetime.fromtimestamp(c[0], NY).date().isoformat(), c[4]) for c in h["candles"]]
            after = [c for d, c in closes if d >= row["day"]]
            sign = 1 if (p.get("move_pct") or 0) >= 0 else -1
            for k, key in ((1, "r1"), (3, "r3"), (5, "r5")):
                if len(after) >= k and p.get(key) is None:
                    p[key] = round(sign * (after[k - 1] / p["ref_price"] - 1) * 100, 2)
                    changed = True
        if changed:
            with db.get_conn() as conn:
                conn.execute("UPDATE morning_briefs SET picks_json = ? WHERE day = ?", (json.dumps(picks), row["day"]))
    return record()


def record() -> dict[str, Any]:
    ensure_schema()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT day, picks_json FROM morning_briefs ORDER BY day DESC LIMIT 60").fetchall()]
    r1 = [p["r1"] for r in rows for p in json.loads(r["picks_json"]) if p.get("r1") is not None]
    r5 = [p["r5"] for r in rows for p in json.loads(r["picks_json"]) if p.get("r5") is not None]
    return {"n1": len(r1), "hit1": round(100 * sum(1 for x in r1 if x > 0) / len(r1), 1) if r1 else None, "avg1": round(statistics.mean(r1), 2) if r1 else None,
            "n5": len(r5), "hit5": round(100 * sum(1 for x in r5 if x > 0) / len(r5), 1) if r5 else None, "avg5": round(statistics.mean(r5), 2) if r5 else None}


# ------------------------------------------------------------------ compose / send
def _fmt(n: float | None, d: int = 0, sign: bool = False) -> str:
    if n is None:
        return "—"
    s = f"{abs(n):,.{d}f}"
    return (("+" if n > 0 else "-" if n < 0 else "") if sign else ("-" if n < 0 else "")) + "$" + s


def _fmt_catalyst(r: dict[str, Any]) -> list[str]:
    cat = r["category"]
    label = {"earnings": "EARNINGS", "fda": "FDA/CLINICAL", "mna": "M&A", "management": "MGMT CHANGE", "legal": "LEGAL/REG"}.get(cat, cat.upper())
    head = f"{r['symbol']} — {label}"
    if r.get("observed_move_pct") is not None:
        head += f" · {r['observed_move_pct']:+.1f}% ({r.get('observed_src')})"
    elif r.get("direction") == "pending":
        head += " · reports after today's close"
    lines = [head, f"   typical range for this: {r.get('typical_range', '—')}", f"   {r['why'][:200]}"]
    return lines


def compose_text(hot: dict[str, Any], rep: dict[str, Any], rec: dict[str, Any], day: str, cat: dict[str, Any] | None = None) -> str:
    L = [f"☀️ JORDAN MORNING BRIEF — {day}", ""]
    L.append(f"🔥 3 HOT STOCKS ({hot['kind']}, from {hot['universe']} liquid names)")
    for i, p in enumerate(hot["picks"], 1):
        mv = f"{p['move_pct']:+.1f}%" if p.get("move_pct") is not None else "—"
        extra = []
        if p.get("rel_volume"):
            extra.append(f"vol {p['rel_volume']:.1f}x")
        if p.get("n_news"):
            extra.append(f"{p['n_news']} headlines")
        if p.get("target_upside") is not None:
            extra.append(f"target {p['target_upside']:+.0f}%")
        L.append(f"{i}. {p['symbol']} {mv} ({p['move_src']}) · {p.get('sector') or ''}")
        L.append(f"   {' · '.join(extra)}")
        L.append(f"   {p['why'][:220]}")
    if rec.get("n1"):
        L.append(f"   Track record: next-day hit rate {rec['hit1']}% over {rec['n1']} picks (avg {rec['avg1']:+.2f}%)"
                 + (f", 5-day {rec['hit5']}% (avg {rec['avg5']:+.2f}%)" if rec.get("n5") else ""))
    L.append("   A 'what is moving' list, not advice.")
    L.append("")
    if cat and (cat.get("top") or cat.get("macro")):
        L.append(f"🎯 TODAY'S CATALYSTS (earnings, FDA, M&A, management, legal — scanned {len(cat.get('top', []))} whole-market hits)")
        for r in cat.get("top", [])[:8]:
            L.extend(_fmt_catalyst(r))
        for m in cat.get("macro", [])[:3]:
            L.append(f"{m['country']} {m['title']} at {m['time']} NY ({m['impact']}) — typical: {m['typical_range']}")
        L.append("   Reference ranges only, from how each event type has historically moved stocks — not a forecast for this stock.")
        L.append("")
    L.append("📊 NOSTRO REPORT")
    L.append(f"Last 24h: {rep['trades_24h']} trades, {_fmt(rep['pnl_24h'], 0, True)}")
    for s in rep["by_strategy"][:4]:
        L.append(f"   {s['k']}: {_fmt(s['pnl'], 0, True)}")
    L.append(f"Evaluations: {rep['in_eval']} in progress · {rep['passed']} passed · {rep['failed']} failed ({rep['live']}/{rep['accounts']} accounts live)")
    for a in rep["closest"]:
        L.append(f"   closest: {a['name'][:26]} {_fmt(a['profit'], 0, True)} of {_fmt(a['target'])} (buffer {_fmt(a['buffer'])})")
    if rep["best_odds"]:
        L.append("Best Monte Carlo odds:")
        for o in rep["best_odds"]:
            L.append(f"   {o['name'][:26]} ({o['strategy']}): pass {o['pass']}% / breach {o['breach']}%")
    new_champs = [c for c in rep["champions"] if not str(c.get("reason", "")).startswith("superseded")]
    L.append("Trainer (24h): " + ("; ".join(f"{c['symbol']} {c['tier']} → {c['strategy']}" for c in new_champs[:3]) if new_champs
                                 else f"{len(rep['champions'])} champion changes" if rep["champions"] else "no champion changes"))
    pm = rep.get("polymarket") or {}
    if pm.get("today") is not None:
        t = pm["today"]
        L.append(f"Polymarket real: {'ARMED' if pm.get('armed') else 'disarmed'} · all-time {_fmt(t.get('all_time_pnl'), 2, True)} over {t.get('all_time_trades')} trades")
    return "\n".join(L)


def render_card(hot: dict[str, Any]) -> bytes | None:
    """One image: a 3-panel card with a 1-month daily chart per pick."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from app.api import invest_routes as inv
    except Exception:  # noqa: BLE001
        return None
    picks = hot["picks"]
    if not picks:
        return None
    fig, axes = plt.subplots(1, len(picks), figsize=(4.2 * len(picks), 3.2), facecolor="#000")
    if len(picks) == 1:
        axes = [axes]
    for ax, p in zip(axes, picks):
        ax.set_facecolor("#050505")
        try:
            h = inv._history(p["symbol"], "3mo", "1d")
            closes = [c[4] for c in h["candles"]][-45:]
        except Exception:  # noqa: BLE001
            closes = []
        up = (p.get("move_pct") or 0) >= 0
        col = "#2be07e" if up else "#ff4a4a"
        if closes:
            ax.plot(range(len(closes)), closes, color=col, lw=1.6)
            ax.fill_between(range(len(closes)), closes, min(closes), color=col, alpha=0.08)
            if p.get("ref_price"):
                ax.scatter([len(closes)], [p["ref_price"]], color="#ffa028", s=28, zorder=5)
        ax.set_title(f"{p['symbol']}  {p['move_pct']:+.1f}%" if p.get("move_pct") is not None else p["symbol"], color="#ffa028", fontsize=12, fontweight="bold", loc="left")
        ax.text(0.01, 0.02, f"heat {p['heat']:+.2f} · vol {p.get('rel_volume') or 0:.1f}x · {p.get('n_news', 0)} news", transform=ax.transAxes, color="#8b8b8b", fontsize=8)
        ax.tick_params(colors="#8b8b8b", labelsize=7)
        for s in ax.spines.values():
            s.set_color("#262626")
        ax.set_xticks([])
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor="#000")
    plt.close(fig)
    return buf.getvalue()


def _safe_catalysts() -> dict[str, Any]:
    try:
        from app.morning import catalysts

        return catalysts.scan()
    except Exception:  # noqa: BLE001 - the catalyst scan must never break the core brief
        logger.exception("catalyst scan failed")
        return {"top": [], "macro": []}


def intel_section(day: str) -> str:
    """Overnight critical events, today's US calendar, Polymarket shocks and the fund's theme exposure. Every part is optional and never breaks the brief."""
    L = ["🛰 INTEL"]
    try:
        from app.intel import events

        ev = events.recent(hours=16, min_sev=3)[:4]
        L.append(f"Critical events overnight: {len(ev)}" if ev else "Critical events overnight: none")
        for e in ev:
            L.append(f"   {time.strftime('%H:%M', time.localtime(e['ts']))} {(e['kind'] or '').upper()} · {e['title'][:110]} ({e['sources']} outlets)")
    except Exception:  # noqa: BLE001
        logger.exception("brief: intel events failed")
    try:
        from app.intel import calendar

        today = [e for e in calendar.week() if dt.datetime.fromtimestamp(e["ts"], NY).date().isoformat() == day]
        if today:
            L.append("Today's US calendar (NY time):")
            for e in today[:6]:
                fc = f" — forecast {e['forecast']}" if e["forecast"] else ""
                pv = f" (prev {e['previous']})" if e["previous"] else ""
                L.append(f"   {dt.datetime.fromtimestamp(e['ts'], NY):%H:%M} {calendar._name(e)} [{e['impact']}]{fc}{pv}")
        else:
            L.append("Today's US calendar: no high-impact releases")
    except Exception:  # noqa: BLE001
        logger.exception("brief: calendar failed")
    try:
        from app.intel import pm_watch

        sh = pm_watch.recent(hours=12)[:3]
        for s in sh:
            q = f" — {s['question']}" if s["question"] and s["question"] != s["title"] else ""
            L.append(f"   Polymarket shock: {s['title'][:70]}{q}: {s['p0']:.0f}% → {s['p1']:.0f}% in {s['window']}")
    except Exception:  # noqa: BLE001
        logger.exception("brief: polymarket shocks failed")
    try:
        from app.fund import book
        from app.intel import impact

        pos = book.positions()
        if pos:
            by: dict[str, float] = {}
            for p in pos:
                d = impact._daily(p["symbol"])
                v = p["qty"] * (float(d[1][-1]) if d else p["avg_cost"])
                th = impact._fund_theme(p["symbol"]) or "OTHER"
                by[th] = by.get(th, 0.0) + v
            top = sorted(by.items(), key=lambda kv: -kv[1])[:4]
            L.append("Fund themes: " + ", ".join(f"{impact.SHORT.get(k, k.title())} ${v:,.0f}" for k, v in top))
    except Exception:  # noqa: BLE001
        logger.exception("brief: fund themes failed")
    L.append("")
    return "\n".join(L) + "\n"


def build(day: str | None = None) -> dict[str, Any]:
    ensure_schema()
    day = day or dt.datetime.now(NY).date().isoformat()
    # hot_stocks / nostro_report / catalyst scan are independent reads (each already does its own heavy I/O and news
    # fetching): running them side by side rather than one after another saves the slowest one's time off the total,
    # instead of paying for all three back to back.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(3) as pool:
        f_hot = pool.submit(hot_stocks)
        f_rep = pool.submit(nostro_report)
        f_cat = pool.submit(_safe_catalysts)
        hot, rep, cat = f_hot.result(), f_rep.result(), f_cat.result()
    rec = track()
    text = compose_text(hot, rep, rec, day, cat)
    text = text.replace("📊 NOSTRO REPORT", intel_section(day) + "📊 NOSTRO REPORT", 1)
    with db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO morning_briefs (day, created_at, kind, picks_json, nostro_json, text, catalysts_json, sent) VALUES (?,?,?,?,?,?,?, "
                     "COALESCE((SELECT sent FROM morning_briefs WHERE day = ?), 0))",
                     (day, time.time(), hot["kind"], json.dumps(hot["picks"]), json.dumps(rep), text, json.dumps(cat), day))
    return {"day": day, "hot": hot, "nostro": rep, "record": rec, "catalysts": cat, "text": text}


def send(brief: dict[str, Any]) -> bool:
    from app.notify import telegram_bot

    if not telegram_bot.is_configured():
        return False
    card = render_card(brief["hot"])
    if card:
        telegram_bot.send_photo(card, f"☀️ Morning brief {brief['day']} — " + " · ".join(p["symbol"] for p in brief["hot"]["picks"]))
    telegram_bot.send_text(brief["text"])
    with db.get_conn() as conn:
        conn.execute("UPDATE morning_briefs SET sent = 1 WHERE day = ?", (brief["day"],))
    return True


def latest() -> dict[str, Any] | None:
    ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM morning_briefs ORDER BY day DESC LIMIT 1").fetchone()
    if not r:
        return None
    return {"day": r["day"], "kind": r["kind"], "picks": json.loads(r["picks_json"]), "nostro": json.loads(r["nostro_json"]),
            "catalysts": json.loads(r["catalysts_json"] or "{}"), "text": r["text"], "sent": bool(r["sent"]),
            "created_at": r["created_at"], "record": record()}


def history(limit: int = 30) -> list[dict[str, Any]]:
    ensure_schema()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT day, kind, picks_json, sent FROM morning_briefs ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
    return [{"day": r["day"], "kind": r["kind"], "picks": json.loads(r["picks_json"]), "sent": bool(r["sent"])} for r in rows]
