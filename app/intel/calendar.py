"""Economic calendar with surprise detection.

Schedule + consensus forecast: the public ForexFactory weekly JSON (US events only, High and Medium impact). It carries no actual figure, so the actual comes
from FRED (official series, published within minutes to an hour of the release) for the indicators listed in FRED_MAP, and from headlines for the FOMC rate
decision. An event whose actual has no free source shows its forecast only; nothing is guessed.

Surprise = actual - forecast, read through a sign table (a hotter CPI or stronger payrolls is hawkish; a higher unemployment rate is dovish). A release with a
real surprise is stored as a critical intel event ("... hawkish surprise"), so the existing IMPACT follow-up measures the reaction and the macro playbook learns from it.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import re
import time
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
FF = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
PRE_MIN = 35          # pre-release card window (minutes before)
POST_HOURS = 4        # how long to keep looking for the actual after the release
SCHEMA = ("CREATE TABLE IF NOT EXISTS intel_calendar (key TEXT PRIMARY KEY, ts INTEGER, title TEXT, impact TEXT, forecast TEXT, previous TEXT, actual TEXT, "
          "surprise REAL, direction TEXT, base_obs TEXT, pre_sent INTEGER DEFAULT 0, post_sent INTEGER DEFAULT 0)")

# FF title (exact) -> (FRED series, transform, unit, sign, plain name, topic word for the intel title)
# sign +1: actual above forecast is hawkish; -1: above forecast is dovish
FRED_MAP: dict[str, tuple[str, str, str, int, str]] = {
    "CPI m/m": ("CPIAUCSL", "mom", "%", 1, "US CPI m/m"),
    "Core CPI m/m": ("CPILFESL", "mom", "%", 1, "US core CPI m/m"),
    "CPI y/y": ("CPIAUCSL", "yoy", "%", 1, "US CPI y/y"),
    "Core CPI y/y": ("CPILFESL", "yoy", "%", 1, "US core CPI y/y"),
    "PPI m/m": ("PPIFIS", "mom", "%", 1, "US PPI m/m"),
    "Core PCE Price Index m/m": ("PCEPILFE", "mom", "%", 1, "US core PCE inflation m/m"),
    "Non-Farm Employment Change": ("PAYEMS", "diff", "K", 1, "US nonfarm payrolls"),
    "Unemployment Rate": ("UNRATE", "level", "%", -1, "US unemployment rate"),
    "Retail Sales m/m": ("RSAFS", "mom", "%", 1, "US retail sales m/m"),
    "Unemployment Claims": ("ICSA", "level_k", "K", -1, "US jobless claims"),
    "Advance GDP q/q": ("A191RL1Q225SBEA", "level", "%", 1, "US GDP (advance)"),
}
MACRO_THEMES = {"BONDS", "BANKS", "HOMEBUILDERS", "GROWTH TECH", "GOLD & MINERS", "CRYPTO", "DOLLAR"}  # what a data surprise actually moves
FED_TITLES = ("Federal Funds Rate", "FOMC Statement")
KEY_WORDS = ("CPI", "PPI", "PCE", "Non-Farm", "Unemployment", "Federal Funds", "FOMC", "GDP", "Retail Sales", "ISM", "Powell", "Fed Chair")
_ff: dict[str, Any] = {"ts": 0.0, "data": []}


def ensure() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)


def _num(s: str | None) -> float | None:
    """'0.3%' -> 0.3, '150K' -> 150, '-100.8B' -> -100.8 (unit kept implicit: FF and the transform below use the same unit)."""
    if not s:
        return None
    m = re.search(r"-?\d+(\.\d+)?", s.replace(",", ""))
    return float(m.group(0)) if m else None


def week() -> list[dict[str, Any]]:
    """This week's US events that matter (High impact, or Medium with a key word), with Unix time."""
    if time.time() - _ff["ts"] > 3600 or not _ff["data"]:
        try:
            r = requests.get(FF, headers=UA, timeout=20)
            r.raise_for_status()
            rows = []
            for e in r.json():
                if e.get("country") != "USD":
                    continue
                if e.get("impact") != "High" and not (e.get("impact") == "Medium" and any(k.lower() in e["title"].lower() for k in KEY_WORDS)):
                    continue
                ts = int(dt.datetime.fromisoformat(e["date"]).timestamp())
                rows.append({"key": f"{e['date'][:16]}|{e['title']}", "ts": ts, "title": e["title"], "impact": e["impact"], "forecast": e.get("forecast") or "",
                             "previous": e.get("previous") or "", "has_actual_source": e["title"] in FRED_MAP or e["title"] in FED_TITLES})
            _ff.update(ts=time.time(), data=sorted(rows, key=lambda x: x["ts"]))
        except Exception:  # noqa: BLE001 - keep the last good copy
            logger.exception("economic calendar fetch failed")
    return _ff["data"]


# ---------------------------------------------------------------- actuals
def _fred(series: str) -> list[tuple[str, float]]:
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series}, headers=UA, timeout=20)
    r.raise_for_status()
    out = []
    for line in io.StringIO(r.text).read().splitlines()[1:]:
        d, _, v = line.partition(",")
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    return out


def _transform(obs: list[tuple[str, float]], how: str) -> float | None:
    if len(obs) < 13:
        return None
    last, prev = obs[-1][1], obs[-2][1]
    if how == "mom":
        return round((last / prev - 1) * 100, 1)
    if how == "yoy":
        return round((last / obs[-13][1] - 1) * 100, 1)
    if how == "diff":
        return round(last - prev, 0)
    if how == "level_k":
        return round(last / 1000, 0)
    return round(last, 1)


def latest_obs(title: str) -> str | None:
    spec = FRED_MAP.get(title)
    if not spec:
        return None
    try:
        obs = _fred(spec[0])
        return obs[-1][0] if obs else None
    except Exception:  # noqa: BLE001
        return None


def actual_for(ev: dict[str, Any], base_obs: str | None) -> float | str | None:
    """The released figure, or None if it is not out yet. FRED: a new observation date after the pre-release snapshot. FOMC: a decision headline."""
    if ev["title"] in FRED_MAP:
        spec = FRED_MAP[ev["title"]]
        try:
            obs = _fred(spec[0])
        except Exception:  # noqa: BLE001
            return None
        if not obs or (base_obs and obs[-1][0] <= base_obs):
            return None
        return _transform(obs, spec[1])
    if ev["title"] in FED_TITLES:
        from app.intel import feeds

        for it in feeds.fetch_all(max_age=60):
            t = it["title"].lower()
            if it["ts"] >= ev["ts"] - 300 and re.search(r"\bfed\b|fomc|federal reserve", t):
                if re.search(r"\b(cuts?|lowers?|slashes)\b.*\brates?\b", t):
                    return "cut"
                if re.search(r"\b(raises?|hikes?|lifts?)\b.*\brates?\b", t):
                    return "hike"
                if re.search(r"\b(holds?|keeps?|leaves?|pauses?|unchanged|steady)\b", t):
                    return "hold"
    return None


def surprise(ev: dict[str, Any], actual: float | str) -> tuple[float | None, str]:
    """(actual - forecast, 'hawkish' | 'dovish' | 'in line')."""
    if isinstance(actual, str):  # Fed decision vs what the forecast implied
        f, p = _num(ev["forecast"]), _num(ev["previous"])
        expected = "hold" if f is None or p is None or abs(f - p) < 0.01 else ("cut" if f < p else "hike")
        if actual == expected:
            return None, "in line"
        order = {"cut": 0, "hold": 1, "hike": 2}
        return None, "hawkish" if order[actual] > order[expected] else "dovish"
    f = _num(ev["forecast"])
    if f is None:
        return None, "in line"
    diff = round(actual - f, 2)
    sign = FRED_MAP[ev["title"]][3]
    tol = 0.05 if FRED_MAP[ev["title"]][2] == "%" else max(5.0, abs(f) * 0.1)  # payrolls/claims: within 10% of forecast is "in line"
    if abs(diff) < tol:
        return diff, "in line"
    return diff, "hawkish" if diff * sign > 0 else "dovish"


# ---------------------------------------------------------------- cards
def _when(ts: int) -> str:
    return time.strftime("%a %H:%M", time.localtime(ts))


def _name(ev: dict[str, Any]) -> str:
    return FRED_MAP[ev["title"]][4] if ev["title"] in FRED_MAP else ("Fed rate decision" if ev["title"] in FED_TITLES else ev["title"])


def pre_card(ev: dict[str, Any]) -> tuple[str, list[list[dict]]]:
    from html import escape

    from app.intel import impact

    info = impact.understand(_name(ev) + " inflation fed", None) if "CPI" in ev["title"] or "PCE" in ev["title"] or "PPI" in ev["title"] else impact.understand(_name(ev) + " fed", None)
    pm = impact.polymarket_related(info, limit=2)
    mins = max(0, int((ev["ts"] - time.time()) / 60))
    L = [f"⏰ <b>IN {mins} MIN · {escape(_name(ev).upper())}</b>", f"<i>{_when(ev['ts'])} Israel time · {escape(ev['impact'])} impact</i>", ""]
    L.append(f"<code>Forecast {ev['forecast'] or '—':>8s}   Previous {ev['previous'] or '—':>8s}</code>")
    L.append("")
    hawk, dove = impact.playbook("macro_hawk"), impact.playbook("macro_dove")

    def top(pb):
        th = [t for t in pb.get("themes", []) if t["theme"] in MACRO_THEMES and t["n"] >= 3 and abs(t["avg"]) >= 0.5 and not 25 < t["hit"] < 75]
        th.sort(key=lambda t: -abs(t["avg"]))
        return ", ".join(f"{impact.SHORT.get(t['theme'], t['theme'])} {t['avg']:+.1f}%" for t in th[:3]) or "no clear pattern yet"
    L += ["📈 <b>If hotter than forecast</b> (hawkish, n=" + str(len(hawk.get("events", []))) + ")", escape(top(hawk)),
          "📉 <b>If cooler</b> (dovish, n=" + str(len(dove.get("events", []))) + ")", escape(top(dove)), ""]
    if pm:
        L.append("🎲 <b>Polymarket</b>")
        L += [f"• {escape(str(e['title'])[:55])}: <b>{e['markets'][0]['prob']:.0f}%</b> {escape(str(e['markets'][0]['question'] or '')[:30])}" for e in pm]
        L.append("")
    L.append("<i>NOSTRO Intel · day-1 moves vs SPY on past surprises</i>")
    return "\n".join(L), []


def post_card(ev: dict[str, Any], actual: float | str, diff: float | None, direction: str) -> tuple[str, list[list[dict]]]:
    from html import escape

    icon = {"hawkish": "🟥", "dovish": "🟩", "in line": "⬜"}[direction]
    unit = FRED_MAP[ev["title"]][2] if ev["title"] in FRED_MAP else ""
    a = actual if isinstance(actual, str) else f"{actual:g}{unit}"
    L = [f"{icon} <b>{escape(_name(ev).upper())} · {direction.upper()}{' SURPRISE' if direction != 'in line' else ''}</b>", ""]
    L.append(f"<code>Actual   {a:>8s}\nForecast {ev['forecast'] or '—':>8s}\nPrevious {ev['previous'] or '—':>8s}</code>")
    if diff is not None:
        L.append(f"Surprise: <b>{diff:+g}{unit}</b> vs consensus")
    L += ["", "<i>Impact update follows in ~30-90 min if the market moves. NOSTRO Intel</i>"]
    return "\n".join(L), []


# ---------------------------------------------------------------- loop step
def tick() -> dict[str, int]:
    """Called every 5 minutes from the intel loop: pre-release cards, actual detection, release cards, and a critical intel event for real surprises."""
    from app.notify import telegram_bot

    ensure()
    now = time.time()
    sent = {"pre": 0, "post": 0}
    for ev in week():
        with db.get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO intel_calendar (key, ts, title, impact, forecast, previous) VALUES (?,?,?,?,?,?)",
                          (ev["key"], ev["ts"], ev["title"], ev["impact"], ev["forecast"], ev["previous"]))
            conn.execute("UPDATE intel_calendar SET forecast=?, previous=? WHERE key=? AND actual IS NULL", (ev["forecast"], ev["previous"], ev["key"]))
            row = dict(conn.execute("SELECT * FROM intel_calendar WHERE key=?", (ev["key"],)).fetchone())
        dt_min = (ev["ts"] - now) / 60
        if 0 < dt_min <= PRE_MIN and not row["pre_sent"] and ev["impact"] == "High":
            base = latest_obs(ev["title"])
            with db.get_conn() as conn:
                conn.execute("UPDATE intel_calendar SET pre_sent=1, base_obs=? WHERE key=?", (base, ev["key"]))
            html, b = pre_card(ev)
            telegram_bot.send_rich(html, b)
            sent["pre"] += 1
        elif 0 < dt_min <= 120 and row["base_obs"] is None and ev["title"] in FRED_MAP:
            base = latest_obs(ev["title"])  # medium-impact events get the snapshot too, without a card
            with db.get_conn() as conn:
                conn.execute("UPDATE intel_calendar SET base_obs=? WHERE key=?", (base or "", ev["key"]))
        if ev["has_actual_source"] and -POST_HOURS * 60 <= dt_min <= 0 and not row["post_sent"] and row["base_obs"] is not None:
            act = actual_for(ev, row["base_obs"] or None)
            if act is None:
                continue
            diff, direction = surprise(ev, act)
            with db.get_conn() as conn:
                conn.execute("UPDATE intel_calendar SET actual=?, surprise=?, direction=?, post_sent=1 WHERE key=?", (str(act), diff, direction, ev["key"]))
            html, b = post_card(ev, act, diff, direction)
            telegram_bot.send_rich(html, b)
            sent["post"] += 1
            if direction != "in line":
                _as_event(ev, act, diff, direction)
    return sent


def _as_event(ev: dict[str, Any], act: float | str, diff: float | None, direction: str) -> None:
    """Store a real surprise as a pushed critical event: the IMPACT follow-up then measures it and the playbook learns it."""
    from app.intel import events

    events.ensure()
    unit = FRED_MAP[ev["title"]][2] if ev["title"] in FRED_MAP else ""
    a = act if isinstance(act, str) else f"{act:g}{unit}"
    title = f"{_name(ev)} {a} vs {ev['forecast'] or '?'} forecast - {direction} surprise"
    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO intel_events (ts, kind, severity, title, link, source, sources, hash, lang, extra, pushed) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                     (int(ev["ts"]), "macro", 3, title, "", "Economic calendar", 1, "cal:" + ev["key"], "en", json.dumps({"outlets": ["calendar"]})))


def view() -> dict[str, Any]:
    ensure()
    with db.get_conn() as conn:
        stored = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM intel_calendar WHERE ts > ?", (int(time.time() - 8 * 86400),)).fetchall()}
    rows = []
    for ev in week():
        s = stored.get(ev["key"], {})
        rows.append({**ev, "name": _name(ev), "actual": s.get("actual"), "surprise": s.get("surprise"), "direction": s.get("direction")})
    return {"events": rows, "now": time.time(), "source": "ForexFactory weekly calendar (schedule + consensus); actuals from FRED / headlines"}
