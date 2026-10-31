"""NQ GAMMA WORKER: live NQ vs session VWAP vs the real gamma walls, Telegram wall alerts and PAPER auto-entries (1:2).

Every 30 s while CME futures trade:
  * NQ=F 1-minute bars (Yahoo, about 1-minute granularity, not exchange ticks); the data lag (now - last bar) is measured and shown.
  * Session VWAP from 09:30 New York with the futures' real volume.
  * Gamma walls from the QQQ option chain (app/invest/gamma.py: call wall, put wall, zero gamma, regime), converted to NQ points with the live
    NQ/QQQ ratio, so the futures basis is included. Snapshotted once a day into gamma_snaps (the forward record the backtest cannot have).
  * Telegram: NQ near a wall (once per wall per day), a setup on a closed 5-minute bar (same rules as gamma_vwap.signal_at), and its result.
  * Paper position per signal in gamma_paper: entry next price, stop from the rule, target 2R, flat at 15:55 ET.
PAPER ONLY: nothing here sends orders, touches TradersPost or any account. Not investment advice.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from app import db
from app.invest import gamma_vwap as gv

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
POLL = 30
NEAR_PCT = 0.0015          # "near the wall" = within 0.15% (about 45 NQ points at 30k)
MAX_MSGS_DAY = 12
SETUPS = ("fade", "break", "reclaim")   # all three run on paper so each builds its own record with real walls

SCHEMA = """
CREATE TABLE IF NOT EXISTS gamma_snaps (day TEXT, symbol TEXT, spot REAL, call_wall REAL, put_wall REAL, zero_gamma REAL, net_gex REAL, regime TEXT,
  nq REAL, ratio REAL, at TEXT, PRIMARY KEY (day, symbol));
CREATE TABLE IF NOT EXISTS gamma_paper (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, setup TEXT, side TEXT, signal_ts TEXT, entry REAL, stop REAL,
  target REAL, level REAL, call_wall REAL, put_wall REAL, zero_gamma REAL, regime TEXT, vwap REAL, status TEXT DEFAULT 'open', exit REAL, exit_ts TEXT,
  why TEXT, r REAL, usd_mnq REAL);
"""
STATE: dict[str, Any] = {"last": None, "error": None, "lag_s": None, "updated": None}
_mem: dict[str, Any] = {"day": None, "sent": set(), "msgs": 0, "set_count": {}, "last_bar": None}


def ensure_tables() -> None:
    with db.get_conn() as c:
        c.executescript(SCHEMA)


def _tg(html: str) -> None:
    if _mem["msgs"] >= MAX_MSGS_DAY:
        return
    _mem["msgs"] += 1
    try:
        from app.notify import telegram_bot

        telegram_bot.send_rich(html)
    except Exception:  # noqa: BLE001
        log.exception("gamma worker telegram")


def _nq_1m() -> tuple[pd.DataFrame, dict]:
    j = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/NQ=F", params={"range": "2d", "interval": "1m"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close", "volume")},
                      index=pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_convert(NY)).dropna(subset=["close"])
    df["volume"] = df["volume"].fillna(0)
    return df, r["meta"]


def _walls(nq_last: float) -> dict | None:
    from app.invest import gamma

    g = gamma.compute("QQQ")
    if not g.get("ok") or not g.get("spot"):
        return None
    ratio = nq_last / g["spot"]
    cv = lambda x: round(x * ratio / 0.25) * 0.25 if x else None  # noqa: E731
    return {"qqq": g["spot"], "ratio": ratio, "call_wall": cv(g["call_wall"]), "put_wall": cv(g["put_wall"]), "zero_gamma": cv(g.get("zero_gamma")),
            "regime": g["regime"], "net_gex": g["net_gex"], "qqq_call_wall": g["call_wall"], "qqq_put_wall": g["put_wall"], "qqq_zero": g.get("zero_gamma")}


def _snapshot(day: str, w: dict, nq: float) -> None:
    with db.get_conn() as c:
        c.execute("INSERT OR IGNORE INTO gamma_snaps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (day, "QQQ", w["qqq"], w["qqq_call_wall"], w["qqq_put_wall"], w["qqq_zero"], w["net_gex"], w["regime"], nq, w["ratio"],
                   time.strftime("%Y-%m-%d %H:%M:%S")))


def _fmt(x: float | None) -> str:
    return f"{x:,.2f}" if x is not None else "-"


def _manage_open(last_bar: pd.Series, ts) -> None:
    with db.read_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM gamma_paper WHERE status='open'")]
    for p in rows:
        long_ = p["side"] == "long"
        hit_s = last_bar["low"] <= p["stop"] if long_ else last_bar["high"] >= p["stop"]
        hit_t = last_bar["high"] >= p["target"] if long_ else last_bar["low"] <= p["target"]
        ex = why = None
        if hit_s:
            ex, why = p["stop"] - (gv.TICK if long_ else -gv.TICK), "stop"
        elif hit_t:
            ex, why = p["target"], "target"
        elif ts.time() >= gv.FLAT_AT or ts.date().isoformat() != p["day"]:
            ex, why = float(last_bar["close"]), "close 15:55"
        if ex is None:
            continue
        risk = abs(p["entry"] - p["stop"])
        pts = (ex - p["entry"]) if long_ else (p["entry"] - ex)
        r = pts / risk if risk else 0
        with db.get_conn() as c:
            c.execute("UPDATE gamma_paper SET status='closed', exit=?, exit_ts=?, why=?, r=?, usd_mnq=? WHERE id=?",
                      (ex, ts.isoformat(), why, round(r, 3), round(pts * gv.MNQ_PT - gv.COMM_RT, 2), p["id"]))
        _tg(f"{'✅' if r > 0 else '❌'} <b>GAMMA PAPER #{p['id']} closed</b> · {p['setup']} {p['side'].upper()}\n"
            f"Exit {_fmt(ex)} ({why}) · <b>{r:+.2f}R</b> · {pts:+.2f} pts · ${pts * gv.MNQ_PT - gv.COMM_RT:+,.0f} per MNQ\n<i>Paper only, no order sent.</i>")


def tick() -> dict[str, Any]:
    now = dt.datetime.now(NY)
    df, meta = _nq_1m()
    if df.empty:
        raise RuntimeError("no NQ=F bars")
    last_ts = df.index[-1]
    lag = (now - last_ts.to_pydatetime()).total_seconds() - 60  # a 1m bar is stamped at its start
    day = now.date().isoformat()
    if _mem["day"] != day:
        _mem.update(day=day, sent=set(), msgs=0, set_count={}, last_bar=None)
    nq = float(df["close"].iloc[-1])
    rth = df[(df.index.date == now.date()) & (df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]
    in_rth = dt.time(9, 30) <= now.time() < dt.time(16, 0) and now.weekday() < 5
    w = _walls(nq) if in_rth or STATE.get("walls") is None else STATE["walls"]
    out: dict[str, Any] = {"nq": nq, "last_bar": last_ts.isoformat(), "lag_s": round(max(0.0, lag)), "delayed": lag > 180,
                           "rth": in_rth, "walls": w, "vwap": None, "signal": None, "exchange_delay_min": meta.get("exchangeDataDelayedBy")}
    if w and in_rth and now.time() >= dt.time(9, 35):
        _snapshot(day, w, nq)
    if len(rth):
        vw = gv.session_vwap(rth)
        out["vwap"] = round(float(vw[-1]), 2)
        out["vs_vwap"] = round(nq - float(vw[-1]), 2)
    if w and in_rth and len(rth) >= 15:
        # wall proximity alerts, once per wall per day
        for name, lvl in (("CALL WALL", w["call_wall"]), ("PUT WALL", w["put_wall"]), ("ZERO GAMMA", w["zero_gamma"])):
            if lvl and abs(nq - lvl) / lvl <= NEAR_PCT and name not in _mem["sent"]:
                _mem["sent"].add(name)
                _tg(f"🧲 <b>NQ near the {name}</b> {_fmt(lvl)}\nNQ {_fmt(nq)} ({nq - lvl:+.1f} pts) · VWAP {_fmt(out['vwap'])} · regime <b>{w['regime']}</b> gamma\n"
                    f"Walls: put {_fmt(w['put_wall'])} · zero {_fmt(w['zero_gamma'])} · call {_fmt(w['call_wall'])} (QQQ {w['qqq_put_wall']}/{w['qqq_call_wall']} × {w['ratio']:.2f})\n"
                    "<i>Context, not advice. Walls from end-of-day open interest.</i>")
        _manage_open(df.iloc[-1], last_ts)
        # 5-minute bars of the session; act on the last CLOSED one only, once
        b5 = rth.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        if len(b5) >= 3 and b5.index[-1] + pd.Timedelta(minutes=5) > now:
            b5 = b5.iloc[:-1]  # still forming
        if len(b5) >= 3 and b5.index[-1] != _mem["last_bar"]:
            _mem["last_bar"] = b5.index[-1]
            i = len(b5) - 1
            o, h, l, c = (b5[k].values for k in ("open", "high", "low", "close"))
            vw5, a5 = gv.session_vwap(b5), gv.atr(b5)
            bar_t = b5.index[-1].time()
            sig = gv.signal_at(i, o, h, l, c, vw5, a5, w["put_wall"], w["call_wall"], SETUPS) if gv.ENTRY_FROM <= bar_t <= gv.ENTRY_TO else None
            with db.read_conn() as cn:
                open_n = cn.execute("SELECT COUNT(*) FROM gamma_paper WHERE status='open'").fetchone()[0]
            if sig and not open_n and _mem["set_count"].get(sig["setup"], 0) < gv.MAX_PER_SETUP_DAY:
                long_ = sig["side"] == "long"
                sig["stop"] = round(sig["stop"] / gv.TICK) * gv.TICK
                e = nq + (gv.TICK if long_ else -gv.TICK)
                risk = (e - sig["stop"]) if long_ else (sig["stop"] - e)
                if risk >= gv.MIN_RISK_PTS:
                    tgt = round((e + gv.RR * risk if long_ else e - gv.RR * risk) / gv.TICK) * gv.TICK
                    _mem["set_count"][sig["setup"]] = _mem["set_count"].get(sig["setup"], 0) + 1
                    with db.get_conn() as cn:
                        cur = cn.execute("INSERT INTO gamma_paper (day,setup,side,signal_ts,entry,stop,target,level,call_wall,put_wall,zero_gamma,regime,vwap) "
                                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                         (day, sig["setup"], sig["side"], b5.index[-1].isoformat(), e, sig["stop"], tgt, sig["level"], w["call_wall"],
                                          w["put_wall"], w["zero_gamma"], w["regime"], float(vw5[-1])))
                        pid = cur.lastrowid
                    out["signal"] = {**sig, "entry": e, "target": tgt, "id": pid}
                    bt = _bt_line(sig["setup"])
                    _tg(f"{'🟢' if long_ else '🔴'} <b>GAMMA SIGNAL #{pid} · {sig['setup']} {sig['side'].upper()} NQ</b>\n"
                        f"Entry {_fmt(e)} · Stop {_fmt(sig['stop'])} · Target {_fmt(tgt)} (1:2, risk {risk:.1f} pts = ${risk * gv.MNQ_PT:,.0f}/MNQ)\n"
                        f"Level {_fmt(sig['level'])} · VWAP {_fmt(float(vw5[-1]))} · {w['regime']} gamma\n{bt}\n"
                        "<i>PAPER auto-entry, no order sent. Not investment advice.</i>")
    STATE.update(last=out, error=None, lag_s=out["lag_s"], updated=time.strftime("%H:%M:%S"), walls=w)
    return out


def _bt_line(setup: str) -> str:
    try:
        rep = json.load(open(f"{gv.CACHE}/report.json"))
        key = {"WALL FADE": "fade", "WALL BREAK": "break", "VWAP RECLAIM": "reclaim"}[setup]
        r = next(x for x in rep["runs"] if x["grid"] == 10.0 and x["setups"] == key and x["levels"] == "gamma proxy")
        a = r["all"]
        return f"Backtest (proxy walls {rep['from'][:4]}-{rep['to'][:4]}): n={a['n']}, win {a['win']}%, avg {a['avg_r']:+.2f}R, t={a['t_stat']}"
    except Exception:  # noqa: BLE001
        return "Backtest: not available"


def paper_stats() -> dict[str, Any]:
    with db.read_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM gamma_paper ORDER BY id DESC LIMIT 300")]
        snaps = [dict(r) for r in c.execute("SELECT * FROM gamma_snaps ORDER BY day DESC LIMIT 60")]
    closed = [r for r in rows if r["status"] == "closed"]
    by: dict[str, dict] = {}
    for r in closed:
        d = by.setdefault(r["setup"], {"n": 0, "wins": 0, "r": 0.0})
        d["n"] += 1
        d["wins"] += r["r"] > 0
        d["r"] += r["r"]
    summ = {k: {"n": v["n"], "win": round(100 * v["wins"] / v["n"], 1), "avg_r": round(v["r"] / v["n"], 3)} for k, v in by.items()}
    return {"trades": rows, "by_setup": summ, "snaps": snaps, "n_closed": len(closed),
            "total_r": round(sum(r["r"] for r in closed), 2), "usd_mnq": round(sum(r["usd_mnq"] or 0 for r in closed), 2)}


async def worker_loop() -> None:
    await asyncio.to_thread(ensure_tables)
    while True:
        now = dt.datetime.now(NY)
        # CME Globex: closed Friday 17:00 -> Sunday 18:00 New York, plus the daily 17:00-18:00 break
        closed = now.weekday() == 5 or (now.weekday() == 4 and now.hour >= 17) or (now.weekday() == 6 and now.hour < 18) or now.hour == 17
        if not closed:
            try:
                await asyncio.to_thread(tick)
            except Exception as e:  # noqa: BLE001
                STATE["error"] = str(e)[:200]
                log.warning("gamma worker tick failed: %s", e)
        await asyncio.sleep(POLL if not closed else 300)
