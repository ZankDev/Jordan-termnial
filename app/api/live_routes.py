"""Live trades page: a cursor-based feed of every live trade plus rolled-up summary stats."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from app import db
from app.accounts import ledger
from app.backtest.engine import CONTRACT_MULTIPLIER

router = APIRouter(prefix="/api")
NY = ZoneInfo("America/New_York")


def _r_multiple(t: dict[str, Any]) -> float | None:
    """P&L in units of the dollars the trade risked at its stop. Uses the stored risk when
    present, else derives it from entry/stop (legacy trades are 1 mini)."""
    risk = t.get("risk_dollars")
    if not risk and t.get("stop_price") is not None and t.get("entry_price") is not None:
        risk = abs(t["entry_price"] - t["stop_price"]) * CONTRACT_MULTIPLIER.get((t.get("symbol") or "").upper(), 20.0) * (t.get("qty") or 1)
    return round(t["pnl"] / risk, 2) if risk else None


# ------------------------------------------------------------------ trade chart image
_img_cache: dict[int, bytes] = {}


def _trade_row(trade_id: int) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        r = conn.execute(
            "SELECT t.*, a.name AS account_name, a.strategy_params_json AS params_json, COALESCE(t.symbol, a.symbol) AS sym, "
            "COALESCE(t.strategy, a.strategy) AS strat FROM account_trades t JOIN accounts a ON a.id = t.account_id WHERE t.id = ?",
            (trade_id,)).fetchone()
        if not r:  # a trade restored into the archive after an account reset
            r = conn.execute(
                "SELECT t.*, a.name AS account_name, a.strategy_params_json AS params_json, COALESCE(t.symbol, a.symbol) AS sym, "
                "COALESCE(t.strategy, a.strategy) AS strat FROM account_trades_archive t JOIN accounts a ON a.id = t.account_id "
                "WHERE t.id = ? AND t.archive_note LIKE 'restored:%'",
            (trade_id,)).fetchone()
    return dict(r) if r else None


def trade_image(trade_id: int) -> bytes:
    """The annotated chart of one trade (entry, exit, stop, target, strategy overlay, WIN/LOSS title) - the same renderer
    the Telegram alerts use. Recent trades use the Yahoo 5m cache; older ones fall back to the Dukascopy history."""
    import json as _json

    import pandas as pd

    from app.data import yfinance_source
    from app.notify import chart_overlays, chart_render
    from app.strategies import registry

    if trade_id in _img_cache:
        return _img_cache[trade_id]
    t = _trade_row(trade_id)
    if not t:
        raise KeyError(trade_id)
    sym = (t["sym"] or "NQ").upper()
    entry = pd.Timestamp(t["entry_time"])
    exit_ = pd.Timestamp(t["exit_time"])
    df = yfinance_source.fetch(sym, "5m")
    if df.empty or df.index.min() > entry - pd.Timedelta(hours=6):
        from app.data import dukascopy_source

        d0 = (entry - pd.Timedelta(days=3)).date()
        d1 = (exit_ + pd.Timedelta(days=1)).date()
        hist = dukascopy_source.fetch_bars(sym, d0, d1, "5m", max_repair_months=0)
        if not hist.empty:
            df = hist
    if df.empty:
        raise RuntimeError("no price data around this trade")
    if df.index.tz is not None and entry.tzinfo is None:
        entry, exit_ = entry.tz_localize(df.index.tz), exit_.tz_localize(df.index.tz)
    overlay = None
    try:
        params = _json.loads(t.get("params_json") or "{}")
        if t.get("strat"):
            registry.get(t["strat"])
            overlay = chart_overlays.compute_overlay(t["strat"], df, params, entry)
    except Exception:  # noqa: BLE001 - the chart is still useful without the indicator overlay
        overlay = None
    entry_px = float(t["entry_price"])
    stop = float(t["stop_price"]) if t.get("stop_price") is not None else entry_px
    target = float(t["target_price"]) if t.get("target_price") is not None else entry_px
    png = chart_render.render_setup_chart(df, entry_time=entry, entry_price=entry_px, stop_price=stop, target_price=target,
                                          side=t["side"], overlay=overlay, exit_time=exit_, exit_price=float(t["exit_price"]),
                                          exit_reason=t.get("exit_reason"), pnl=float(t["pnl"]))
    if len(_img_cache) > 200:
        _img_cache.clear()
    _img_cache[trade_id] = png
    return png


@router.get("/trades/{trade_id}/image.png")
def get_trade_image(trade_id: int, download: bool = False):
    from fastapi import HTTPException
    from fastapi.responses import Response

    try:
        png = trade_image(trade_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="trade not found")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"could not draw this trade: {e}")
    headers = {"Cache-Control": "max-age=86400"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="trade_{trade_id}.png"'
    return Response(content=png, media_type="image/png", headers=headers)


@router.post("/trades/{trade_id}/telegram")
def send_trade_image(trade_id: int):
    from fastapi import HTTPException

    from app.notify import telegram_bot

    if not telegram_bot.is_configured():
        raise HTTPException(status_code=409, detail="Telegram is not configured")
    t = _trade_row(trade_id)
    if not t:
        raise HTTPException(status_code=404, detail="trade not found")
    png = trade_image(trade_id)
    win = t["pnl"] > 0
    telegram_bot.send_photo(png, f"{'✅ WIN' if win else '❌ LOSS'} {t['account_name']} · {(t['strat'] or '').replace('_', ' ')} {t['sym']} "
                                 f"{t['side'].upper()} {t['entry_price']} → {t['exit_price']} ({t.get('exit_reason')}) · {t['pnl']:+,.2f}")
    return {"sent": True}


@router.get("/trades/feed")
def get_trades_feed(after_id: int | None = None, before_id: int | None = None, limit: int = 200):
    limit = max(1, min(limit, 500))
    trades = db.live_trades_feed(after_id=after_id, before_id=before_id, limit=limit)
    for t in trades:
        t["r_multiple"] = _r_multiple(t)
        t["archived"] = bool(t.get("archived"))
    with db.get_conn() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM account_trades WHERE source = 'live'").fetchone()
    return {
        "trades": trades,
        "last_id": row["m"],
        "open_positions": ledger._open_positions_with_unrealized(db.list_accounts(include_archived=False)),
        "server_time": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [r["pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    rs = [r["r"] for r in rows if r.get("r") is not None]
    return {
        "trades": len(rows),
        "net_pnl": round(sum(pnls), 2),
        "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (None if not wins else math.inf),
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "best": round(max(pnls), 2) if pnls else 0.0,
        "worst": round(min(pnls), 2) if pnls else 0.0,
    }


@router.get("/trades/summary")
def get_trades_summary(since: str = "today"):
    today = dt.datetime.now(NY).date()
    cutoff = {"today": today, "7d": today - dt.timedelta(days=7), "30d": today - dt.timedelta(days=30)}.get(since)
    rows = db.live_trades_since(cutoff.isoformat() if cutoff else None)
    label, fallback = {"today": f"today · {today.strftime('%a %d %b')} (NY)", "7d": "last 7 days", "30d": "last 30 days"}.get(since, "all time"), False
    if since == "today" and not rows:
        # nothing closed yet today (e.g. before the NY open): show the last session that had trades instead of a flat $0
        recent = db.live_trades_since((today - dt.timedelta(days=10)).isoformat())
        if recent:
            last_day = max(str(r["exit_time"])[:10] for r in recent)
            rows = [r for r in recent if str(r["exit_time"])[:10] == last_day]
            label, fallback = f"last session · {dt.date.fromisoformat(last_day).strftime('%a %d %b')} (nothing closed yet today)", True
    for r in rows:
        r["r"] = _r_multiple(r)

    overall = _stats(rows)
    if overall["profit_factor"] == math.inf:
        overall["profit_factor"] = None  # JSON has no infinity; None renders as "-"

    def grouped(key: str) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            buckets.setdefault(str(r.get(key) or "unknown"), []).append(r)
        out = []
        for name, items in buckets.items():
            st = _stats(items)
            if st["profit_factor"] == math.inf:
                st["profit_factor"] = None
            out.append({"name": name, **st})
        return sorted(out, key=lambda x: -x["net_pnl"])

    cum, running = [], 0.0
    for r in rows:
        running += r["pnl"]
        cum.append({"time": r["exit_time"], "pnl": round(running, 2)})
    try:  # open trades marked at the last price, so "today" shows what is at stake right now too
        opens = ledger._open_positions_with_unrealized(db.list_accounts(include_archived=False))
        open_pnl = round(sum(p.get("unrealized_pnl") or 0 for p in opens), 2)
    except Exception:  # noqa: BLE001
        opens, open_pnl = [], 0.0
    return {
        "since": since,
        "label": label,
        "fallback": fallback,
        "open_pnl": open_pnl,
        "open_count": len(opens),
        **overall,
        "by_strategy": grouped("strategy"),
        "by_account": grouped("account_name"),
        "cum_pnl": cum[-300:],
    }


@router.get("/auto")
def get_auto_status():
    """What the AUTO account follows, what it would follow next, and its swap history."""
    from app.accounts import auto_account

    return auto_account.status()
