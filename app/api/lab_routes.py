"""NOSTRO LAB API: alerts (ALRT), event blackout, trade journal, execution statistics, regime analysis and walk-forward optimisation."""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import alerts, db, event_blackout
from app.notify import telegram_bot

router = APIRouter(prefix="/api")
NY = ZoneInfo("America/New_York")
JOURNAL_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache" / "journal"


# ================================================================== alerts
class AlertIn(BaseModel):
    kind: str  # price | pct_move | volume | news | econ
    symbol: str | None = None
    op: str | None = None
    value: float | None = None
    keyword: str | None = None
    query: str | None = None
    minutes_before: float | None = None
    note: str | None = None
    once: bool = True
    cooldown: int = 3600


@router.get("/alerts")
def list_alerts():
    alerts.ensure()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY active DESC, id DESC").fetchall()]
    for r in rows:
        r["extra"] = json.loads(r["extra"] or "{}")
        r.pop("state", None)
    from app.notify import telegram_bot

    return {"alerts": rows, "telegram": telegram_bot._config() is not None}


@router.post("/alerts")
def create_alert(a: AlertIn):
    alerts.ensure()
    if a.kind not in ("price", "pct_move", "volume", "news", "econ"):
        raise HTTPException(status_code=400, detail="unknown alert kind")
    sym = (a.symbol or "").strip().upper() or None
    if a.kind in ("price", "pct_move", "volume") and (not sym or a.value is None or a.value <= 0):
        raise HTTPException(status_code=400, detail="symbol and a positive value are required")
    if a.kind == "price" and a.op not in ("above", "below"):
        raise HTTPException(status_code=400, detail="price alerts need above or below")
    if a.kind == "news" and not (a.keyword or "").strip():
        raise HTTPException(status_code=400, detail="news alerts need a keyword")
    if a.kind == "news" and not (sym or a.query):
        raise HTTPException(status_code=400, detail="news alerts need a symbol or a search query")
    if a.kind == "econ" and not (a.keyword or "").strip():
        raise HTTPException(status_code=400, detail="economic alerts need an event keyword (e.g. CPI)")
    extra = {"keyword": (a.keyword or "").strip(), "query": (a.query or "").strip(), "minutes_before": a.minutes_before or 30}
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO alerts (kind, symbol, op, value, extra, note, once, cooldown, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (a.kind, sym, a.op, a.value, json.dumps(extra), a.note, 1 if a.once else 0, max(60, a.cooldown), time.time()))
        return {"id": cur.lastrowid}


@router.post("/alerts/{alert_id}/toggle")
def toggle_alert(alert_id: int):
    alerts.ensure()
    with db.get_conn() as conn:
        conn.execute("UPDATE alerts SET active = 1 - active WHERE id = ?", (alert_id,))
    return {"ok": True}


@router.delete("/alerts/{alert_id}")
def delete_alert(alert_id: int):
    alerts.ensure()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
    return {"ok": True}


@router.get("/alerts/log")
def alert_log(since_id: int = 0, limit: int = 50):
    alerts.ensure()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM alert_log WHERE id > ? ORDER BY id DESC LIMIT ?", (since_id, min(limit, 200))).fetchall()]
    return {"log": rows}


@router.post("/alerts/test")
def alert_test():
    telegram_bot.send_text("NOSTRO alert test: Telegram delivery is working.")
    return {"sent": True}


@router.post("/alerts/run-now")
def alerts_run_now():
    return {"fired": alerts.evaluate()}


# ================================================================== blackout
class BlackoutIn(BaseModel):
    enabled: bool = True
    before: int = 15
    after: int = 30
    keywords: str = "CPI,FOMC,NON-FARM,FEDERAL FUNDS,POWELL"


@router.get("/nostro/blackout")
def get_blackout():
    cur = event_blackout.current()
    up = event_blackout.upcoming(96)
    now = time.time()
    return {"settings": event_blackout.settings(), "active": cur, "next": next((e for e in up if e["ts"] > now), None), "upcoming": up[:12], "now": now}


@router.put("/nostro/blackout")
def put_blackout(b: BlackoutIn):
    event_blackout.save(b.enabled, b.before, b.after, b.keywords)
    return get_blackout()


# ================================================================== journal
_JSCHEMA = "CREATE TABLE IF NOT EXISTS trade_journal (trade_id INTEGER PRIMARY KEY, note TEXT, tags TEXT, images TEXT, updated_at REAL)"


class JournalIn(BaseModel):
    note: str = ""
    tags: list[str] = []


@router.get("/journal/{trade_id}")
def get_journal(trade_id: int):
    with db.get_conn() as conn:
        conn.execute(_JSCHEMA)
        r = conn.execute("SELECT * FROM trade_journal WHERE trade_id = ?", (trade_id,)).fetchone()
        t = conn.execute("SELECT t.*, a.name AS account_name FROM account_trades t JOIN accounts a ON a.id = t.account_id WHERE t.id = ?", (trade_id,)).fetchone()
    if not t:
        raise HTTPException(status_code=404, detail="trade not found")
    return {"trade": dict(t), "note": (r["note"] if r else "") or "", "tags": json.loads(r["tags"]) if r and r["tags"] else [], "images": json.loads(r["images"]) if r and r["images"] else []}


@router.put("/journal/{trade_id}")
def put_journal(trade_id: int, j: JournalIn):
    tags = sorted({re.sub(r"[^\w \-]", "", t).strip().lower()[:24] for t in j.tags if t.strip()})
    with db.get_conn() as conn:
        conn.execute(_JSCHEMA)
        old = conn.execute("SELECT images FROM trade_journal WHERE trade_id = ?", (trade_id,)).fetchone()
        conn.execute("INSERT INTO trade_journal (trade_id, note, tags, images, updated_at) VALUES (?,?,?,?,?) ON CONFLICT(trade_id) DO UPDATE SET note=excluded.note, tags=excluded.tags, updated_at=excluded.updated_at",
                     (trade_id, j.note[:4000], json.dumps(tags), old["images"] if old else "[]", time.time()))
    return {"ok": True, "tags": tags}


class ImageIn(BaseModel):
    data_url: str


@router.post("/journal/{trade_id}/image")
def add_journal_image(trade_id: int, img: ImageIn):
    m = re.match(r"^data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$", img.data_url, re.S)
    if not m:
        raise HTTPException(status_code=400, detail="expected a PNG/JPEG/WebP/GIF data URL")
    raw = base64.b64decode(m.group(2))
    if len(raw) > 6 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image too large (6 MB max)")
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{trade_id}_{int(time.time() * 1000)}.{'jpg' if m.group(1) == 'jpeg' else m.group(1)}"
    (JOURNAL_DIR / name).write_bytes(raw)
    with db.get_conn() as conn:
        conn.execute(_JSCHEMA)
        r = conn.execute("SELECT images FROM trade_journal WHERE trade_id = ?", (trade_id,)).fetchone()
        imgs = (json.loads(r["images"]) if r and r["images"] else []) + [name]
        conn.execute("INSERT INTO trade_journal (trade_id, note, tags, images, updated_at) VALUES (?, '', '[]', ?, ?) ON CONFLICT(trade_id) DO UPDATE SET images=excluded.images, updated_at=excluded.updated_at",
                     (trade_id, json.dumps(imgs), time.time()))
    return {"name": name}


@router.get("/journal-image/{name}")
def journal_image(name: str):
    if not re.fullmatch(r"\d+_\d+\.(png|jpg|webp|gif)", name) or not (JOURNAL_DIR / name).exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(JOURNAL_DIR / name)


@router.delete("/journal/{trade_id}/image/{name}")
def delete_journal_image(trade_id: int, name: str):
    with db.get_conn() as conn:
        conn.execute(_JSCHEMA)
        r = conn.execute("SELECT images FROM trade_journal WHERE trade_id = ?", (trade_id,)).fetchone()
        imgs = [i for i in (json.loads(r["images"]) if r and r["images"] else []) if i != name]
        conn.execute("UPDATE trade_journal SET images = ? WHERE trade_id = ?", (json.dumps(imgs), trade_id))
    (JOURNAL_DIR / name).unlink(missing_ok=True)
    return {"ok": True}


@router.get("/journal")
def journal_overview(limit: int = 80):
    """Recent live trades with their journal entry (if any) + mistake analysis grouped by tag."""
    with db.get_conn() as conn:
        conn.execute(_JSCHEMA)
        trades = [dict(r) for r in conn.execute(
            "SELECT t.id, t.exit_time, t.side, t.symbol, t.strategy, t.pnl, t.exit_reason, t.risk_dollars, a.name AS account_name, j.note, j.tags, j.images "
            "FROM account_trades t JOIN accounts a ON a.id = t.account_id LEFT JOIN trade_journal j ON j.trade_id = t.id "
            "WHERE t.source = 'live' ORDER BY t.id DESC LIMIT ?", (limit,)).fetchall()]
        tagged = [dict(r) for r in conn.execute(
            "SELECT t.id, t.pnl, t.risk_dollars, j.tags FROM trade_journal j JOIN account_trades t ON t.id = j.trade_id WHERE j.tags IS NOT NULL AND j.tags != '[]'").fetchall()]
    for t in trades:
        t["tags"] = json.loads(t["tags"]) if t["tags"] else []
        t["images"] = json.loads(t["images"]) if t["images"] else []
        t["r"] = round(t["pnl"] / t["risk_dollars"], 2) if t.get("risk_dollars") else None
    by: dict[str, dict[str, Any]] = {}
    for t in tagged:
        for tag in json.loads(t["tags"]):
            b = by.setdefault(tag, {"tag": tag, "n": 0, "net": 0.0, "wins": 0, "r_sum": 0.0, "r_n": 0})
            b["n"] += 1; b["net"] += t["pnl"]; b["wins"] += 1 if t["pnl"] > 0 else 0
            if t.get("risk_dollars"):
                b["r_sum"] += t["pnl"] / t["risk_dollars"]; b["r_n"] += 1
    analysis = sorted(({"tag": b["tag"], "trades": b["n"], "net": round(b["net"], 2), "win_rate": round(b["wins"] / b["n"] * 100, 1), "avg_r": round(b["r_sum"] / b["r_n"], 2) if b["r_n"] else None,
                        "avg_pnl": round(b["net"] / b["n"], 2)} for b in by.values()), key=lambda x: x["net"])
    return {"trades": trades, "analysis": analysis, "suggested_tags": ["fomo", "moved stop", "oversized", "revenge", "early exit", "late entry", "followed plan", "news spike", "no setup", "hesitated"]}


# ================================================================== execution stats
@router.get("/nostro/exec")
def exec_stats(since: str = "all"):
    from app.backtest.engine import STOP_SLIPPAGE_TICKS, TICK_SIZE
    from app.backtest.sizing import point_value

    days = {"7d": 7, "30d": 30, "90d": 90}.get(since)
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM account_trades WHERE source = 'live' ORDER BY id").fetchall()]
    if days:
        cutoff = dt.datetime.now(NY) - dt.timedelta(days=days)
        rows = [r for r in rows if r.get("exit_time") and dt.datetime.fromisoformat(r["exit_time"]).astimezone(NY) >= cutoff]
    if not rows:
        return {"n": 0}

    def bucket(keyfn) -> list[dict[str, Any]]:
        b: dict[Any, dict[str, Any]] = {}
        for r in rows:
            k = keyfn(r)
            if k is None:
                continue
            x = b.setdefault(k, {"key": k, "n": 0, "net": 0.0, "wins": 0, "r_sum": 0.0, "r_n": 0})
            x["n"] += 1; x["net"] += r["pnl"]; x["wins"] += 1 if r["pnl"] > 0 else 0
            if r.get("risk_dollars"):
                x["r_sum"] += r["pnl"] / r["risk_dollars"]; x["r_n"] += 1
        return [{"key": k, "trades": v["n"], "net": round(v["net"], 2), "win_rate": round(v["wins"] / v["n"] * 100, 1), "avg_r": round(v["r_sum"] / v["r_n"], 2) if v["r_n"] else None} for k, v in sorted(b.items(), key=lambda kv: str(kv[0]))]

    def entry_dt(r: dict[str, Any]) -> dt.datetime | None:
        try:
            return dt.datetime.fromisoformat(r["entry_time"]).astimezone(NY)
        except Exception:  # noqa: BLE001
            return None

    hold = []
    comm = slip = gross = 0.0
    for r in rows:
        e = entry_dt(r)
        try:
            x = dt.datetime.fromisoformat(r["exit_time"]).astimezone(NY)
            if e:
                hold.append((x - e).total_seconds() / 60)
        except Exception:  # noqa: BLE001
            pass
        qty = r.get("qty") or 1
        pv = point_value(r.get("symbol") or "NQ", r.get("contract") or "mini")
        c = r.get("commission") or 0.0
        comm += c
        gross += r["pnl"] + c
        if r.get("exit_reason") == "stop":
            slip += STOP_SLIPPAGE_TICKS * TICK_SIZE.get((r.get("symbol") or "NQ").upper(), 0.25) * pv * qty
    wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "n": len(rows), "net": round(sum(r["pnl"] for r in rows), 2), "gross": round(gross, 2), "commission": round(comm, 2), "modeled_slippage": round(slip, 2),
        "cost_pct_of_gross": round((comm + slip) / abs(gross) * 100, 1) if gross else None, "avg_hold_min": round(sum(hold) / len(hold), 1) if hold else None,
        "by_hour": bucket(lambda r: (entry_dt(r).hour if entry_dt(r) else None)), "by_weekday": [{**b, "key": wd[b["key"]]} for b in bucket(lambda r: (entry_dt(r).weekday() if entry_dt(r) else None))],
        "by_exit": bucket(lambda r: r.get("exit_reason")), "by_side": bucket(lambda r: r.get("side")), "by_strategy": bucket(lambda r: r.get("strategy")), "by_symbol": bucket(lambda r: r.get("symbol")),
        "note": "Fills are simulated: slippage is the engine's assumption (1 tick on stop exits), not measured against a broker. P&L by hour uses entry time (New York).",
    }


# ================================================================== background jobs: regimes + walk-forward
_JOBS: dict[str, dict[str, Any]] = {"regimes": {"state": "idle"}, "walkforward": {"state": "idle"}}
_JOB_LOCK = threading.Lock()


def _start(name: str, fn, *args) -> dict[str, Any]:
    with _JOB_LOCK:
        if _JOBS[name].get("state") == "running":
            return _JOBS[name]
        _JOBS[name] = {"state": "running", "started": time.time(), "progress": 0.0, "message": "starting"}

    def run() -> None:
        try:
            _JOBS[name] = {"state": "done", "finished": time.time(), "result": fn(_JOBS[name], *args)}
        except Exception as e:  # noqa: BLE001
            _JOBS[name] = {"state": "error", "error": f"{type(e).__name__}: {e}"}

    threading.Thread(target=run, daemon=True, name=f"lab-{name}").start()
    return _JOBS[name]


def _deep_data(symbol: str, months: int):
    from app.optimizer import promotion

    m, _cov, _r = promotion.deep_plan(symbol)
    if not m:
        raise RuntimeError(f"No verified multi-month history for {symbol} yet (Dukascopy repair is still filling in).")
    return promotion._deep_df(symbol, min(months, m)), min(months, m)


def _regimes_job(job: dict[str, Any], symbol: str, months: int) -> dict[str, Any]:
    import pandas as pd

    from app.backtest.engine import run_backtest
    from app.backtest.sizing import default_config
    from app.optimizer.regime import classify_days
    from app.strategies import registry

    df, used = _deep_data(symbol, months)
    trend = classify_days(df)
    ranges = {d: float(g["high"].max() - g["low"].min()) for d, g in df.groupby(df.index.date)}
    vals = sorted(ranges.values())
    lo_cut, hi_cut = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    vol = {d: ("high vol" if r >= hi_cut else "low vol" if r <= lo_cut else "mid vol") for d, r in ranges.items()}
    strategies = registry.list_strategies()
    out = []
    for i, s in enumerate(strategies):
        job.update(progress=i / len(strategies), message=f"backtesting {s.name}")
        try:
            res = run_backtest(df, s, s.default_params(), symbol, 50_000.0, sizing=default_config(50_000.0))
        except Exception:  # noqa: BLE001
            continue
        cells: dict[str, dict[str, Any]] = {}
        for t in res.trades:
            d = pd.Timestamp(t.get("entry_time") or t["exit_time"]).date()
            for dim, label in (("trend", trend.get(d)), ("vol", vol.get(d))):
                if not label:
                    continue
                c = cells.setdefault(label, {"n": 0, "net": 0.0, "wins": 0, "gw": 0.0, "gl": 0.0})
                c["n"] += 1; c["net"] += t["pnl"]; c["wins"] += 1 if t["pnl"] > 0 else 0
                c["gw" if t["pnl"] > 0 else "gl"] += abs(t["pnl"])
        out.append({"strategy": s.name, "cells": {k: {"trades": v["n"], "net": round(v["net"], 2), "win_rate": round(v["wins"] / v["n"] * 100, 1), "pf": round(v["gw"] / v["gl"], 2) if v["gl"] else None} for k, v in cells.items()}})
    counts: dict[str, int] = {}
    for d in trend:
        counts[trend[d]] = counts.get(trend[d], 0) + 1
        counts[vol.get(d, "?")] = counts.get(vol.get(d, "?"), 0) + 1
    return {"symbol": symbol, "months": used, "days": len(trend), "day_counts": counts, "strategies": out,
            "regimes": ["trending", "choppy", "high vol", "mid vol", "low vol"], "note": "Default parameters on real Dukascopy history. Trend/chop = intraday efficiency ratio (regime.py); vol = daily range tercile."}


def _walkforward_job(job: dict[str, Any], symbol: str, strategy: str, train_m: int, test_m: int, iters: int) -> dict[str, Any]:
    import pandas as pd

    from app.backtest.engine import run_backtest
    from app.backtest.sizing import default_config
    from app.optimizer.search import _search_given_folds, build_folds
    from app.strategies import registry

    df, used = _deep_data(symbol, 36)
    strat = registry.get(strategy)
    start, end = df.index.min(), df.index.max()
    windows, cur = [], start
    while cur + pd.DateOffset(months=train_m + test_m) <= end + pd.Timedelta(days=3):
        windows.append((cur, cur + pd.DateOffset(months=train_m), cur + pd.DateOffset(months=train_m + test_m)))
        cur = cur + pd.DateOffset(months=test_m)
    if not windows:
        raise RuntimeError(f"Only {used} months of verified history: need at least {train_m + test_m} for one window.")
    rows, oos_trades = [], []
    for i, (a, b, c) in enumerate(windows):
        job.update(progress=i / len(windows), message=f"window {i + 1}/{len(windows)}: optimising {strategy}")
        train, test = df[(df.index >= a) & (df.index < b)], df[(df.index >= b) & (df.index < c)]
        if len(train) < 500 or len(test) < 100:
            continue
        folds = build_folds(train, k_folds=2)
        best = _search_given_folds(folds, strat, symbol, iters, 50_000.0, 10, top_n=1, seed=i + 7, burn_gate=False)
        if not best:
            rows.append({"train": [str(a.date()), str(b.date())], "test": [str(b.date()), str(c.date())], "params": None, "note": "no candidate cleared the trade-count bar in-sample"})
            continue
        params = best[0]["params"]
        sz = default_config(50_000.0)
        is_res = run_backtest(train, strat, params, symbol, 50_000.0, sizing=sz)
        oos = run_backtest(test, strat, params, symbol, 50_000.0, sizing=sz)
        is_pm = is_res.metrics["net_pnl"] / train_m
        oos_pm = oos.metrics["net_pnl"] / test_m
        oos_trades.extend(oos.trades)
        rows.append({"train": [str(a.date()), str(b.date())], "test": [str(b.date()), str(c.date())], "params": params, "is_score": best[0]["score"], "is_net": is_res.metrics["net_pnl"],
                     "oos_net": oos.metrics["net_pnl"], "oos_trades": oos.metrics["total_trades"], "oos_pf": oos.metrics["profit_factor"], "oos_win_rate": oos.metrics["win_rate_pct"],
                     "efficiency": (oos_pm / is_pm) if is_pm > 0 else None})
    eq, curve = 0.0, []
    for t in sorted(oos_trades, key=lambda x: str(x["exit_time"])):
        eq += t["pnl"]
        curve.append([str(t["exit_time"])[:10], round(eq, 2)])
    done = [r for r in rows if r.get("oos_net") is not None]
    profitable = sum(1 for r in done if r["oos_net"] > 0)
    effs = [r["efficiency"] for r in done if r.get("efficiency") is not None]
    return {"symbol": symbol, "strategy": strategy, "train_months": train_m, "test_months": test_m, "iterations": iters, "months_available": used, "windows": rows, "oos_curve": curve,
            "summary": {"windows": len(done), "profitable_windows": profitable, "oos_net": round(sum(r["oos_net"] for r in done), 2), "median_efficiency": sorted(effs)[len(effs) // 2] if effs else None,
                        "oos_trades": sum(r["oos_trades"] for r in done)},
            "note": "Each window re-optimises on the train span and is judged only on the following unseen test span, then the window rolls forward. Efficiency = OOS monthly P&L / in-sample monthly P&L."}


class RegimeReq(BaseModel):
    symbol: str = "NQ"
    months: int = 24


class WfReq(BaseModel):
    symbol: str = "NQ"
    strategy: str = "orb_retest"
    train_months: int = 9
    test_months: int = 3
    iterations: int = 20


@router.post("/nostro/regimes/run")
def regimes_run(r: RegimeReq):
    return _start("regimes", _regimes_job, r.symbol.upper(), max(6, min(r.months, 96)))


@router.get("/nostro/regimes")
def regimes_get():
    return _JOBS["regimes"]


@router.post("/nostro/walkforward/run")
def wf_run(r: WfReq):
    return _start("walkforward", _walkforward_job, r.symbol.upper(), r.strategy, max(3, min(r.train_months, 24)), max(1, min(r.test_months, 12)), max(5, min(r.iterations, 60)))


@router.get("/nostro/walkforward")
def wf_get():
    return _JOBS["walkforward"]
