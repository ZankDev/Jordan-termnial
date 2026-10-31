"""Polymarket opportunities on price-threshold markets (Bitcoin / Ethereum / Solana / oil / gold / silver / S&P / Nasdaq), alerts + PAPER ledger only.

Fair probability from the current price and implied volatility (Deribit DVOL for BTC/ETH; VIX / VXN / OVX / GVZ for the index, oil and gold; 30-day
realised volatility otherwise), zero drift, lognormal:
  * "above K on <date>"          P = N(d2),  d2 = (ln(S/K) - s^2 T / 2) / (s sqrt T)
  * "between A-B on <date>"       P = N(d2(A)) - N(d2(B))
  * "reach / hit K by <date>"     P = 2 * (1 - N(|ln(K/S)| / (s sqrt T)))      (reflection principle, continuous barrier)
Edge = fair - ask for buying YES, bid - fair for buying NO; flagged at >= 7 points with >= $20k liquidity.

Known limits, said out loud: real price moves have fatter tails than a lognormal (far-away strikes are UNDER-estimated by the model, so a "NO" on a
far strike can look better than it is), the resolution source (Binance / an exchange print) differs slightly from Yahoo, and implied vol changes.
That is why every flagged opportunity goes into a paper ledger and is settled at resolution: the hit rate, not the model, decides if this is an edge.
Nothing here is ever sent to the real Polymarket trader in app/polymarket/.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import time
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
MIN_EDGE, MIN_LIQ, MAX_DAYS = 0.07, 20_000, 35
UNDERLYING: list[tuple[str, str, str]] = [  # regex on event title, yahoo symbol, vol source
    (r"bitcoin|\bbtc\b", "BTC-USD", "dvol:BTC"), (r"ethereum|\beth\b", "ETH-USD", "dvol:ETH"), (r"solana|\bsol\b", "SOL-USD", "rv"),
    (r"wti|crude oil", "CL=F", "^OVX"), (r"\bgold\b", "GC=F", "^GVZ"), (r"silver", "SI=F", "rv"), (r"s&p 500|\bspx\b", "^GSPC", "^VIX"), (r"nasdaq|\bndx\b", "^NDX", "^VXN"),
]
SCHEMA = ("CREATE TABLE IF NOT EXISTS pm_opps (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, market_id TEXT, slug TEXT, title TEXT, question TEXT, side TEXT, "
          "price REAL, fair REAL, edge REAL, kind TEXT, underlying TEXT, spot REAL, strike TEXT, sigma REAL, end_ts INTEGER, liquidity REAL, status TEXT DEFAULT 'open', "
          "outcome REAL, pnl REAL, alerted INTEGER DEFAULT 0, UNIQUE(market_id, side))")
_cache: dict[str, tuple[float, Any]] = {}


def ensure() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)


def _N(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        v = fn()
    except Exception:  # noqa: BLE001
        v = None
    _cache[key] = (time.time(), v)
    return v


def spot(sym: str) -> float | None:
    from app.api import invest_routes as ir

    def go():
        h = ir._history(sym, "1d", "1m", prepost=True)
        return float([c for c in h["candles"] if c[4]][-1][4])
    return _cached("spot:" + sym, 60, go)


def sigma(sym: str, src: str) -> float | None:
    """Annualised volatility (decimal)."""
    from app.api import invest_routes as ir

    if src.startswith("dvol:"):
        def go():
            now = int(time.time() * 1000)
            r = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data", params={"currency": src[5:], "resolution": "3600",
                             "start_timestamp": now - 4 * 3600 * 1000, "end_timestamp": now}, headers=UA, timeout=15)
            return float(r.json()["result"]["data"][-1][4]) / 100
        return _cached("vol:" + src, 600, go)
    if src.startswith("^"):
        def go():
            h = ir._history(src, "5d", "1d")
            return float([c for c in h["candles"] if c[4]][-1][4]) / 100
        return _cached("vol:" + src, 600, go)

    def rv():
        h = ir._history(sym, "3mo", "1d")
        px = [c[4] for c in h["candles"] if c[4]][-31:]
        r = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
        m = sum(r) / len(r)
        return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(365 if "-USD" in sym else 252)
    return _cached("vol:rv:" + sym, 3600, rv)


def _strike(label: str) -> tuple[str, float | None, float | None]:
    """('up'|'down'|'above'|'between', K or low, high)."""
    t = (label or "").replace(",", "").replace("$", "")
    nums = [float(x) * (1000 if k.lower() == "k" else 1) for x, k in re.findall(r"(\d+(?:\.\d+)?)\s*([kK]?)", t)]
    if not nums:
        return "", None, None
    if t.strip().startswith("<"):
        return "below", nums[0], None
    if t.strip().startswith(">"):
        return "above", nums[0], None
    if "↑" in label or (re.search(r"reach|high|above|hit", t.lower()) and "↓" not in label and "dip" not in t.lower()):
        return "up", nums[0], None
    if "↓" in label or "dip" in t.lower() or "low" in t.lower():
        return "down", nums[0], None
    if len(nums) >= 2 and ("-" in t or "–" in t):
        return "between", nums[0], nums[1]
    return "above", nums[0], None


def fair_prob(kind: str, S: float, k1: float, k2: float | None, s: float, T: float) -> float | None:
    if T <= 0 or s <= 0 or S <= 0 or k1 <= 0:
        return None
    v = s * math.sqrt(T)

    def above(K):
        return _N((math.log(S / K) - 0.5 * s * s * T) / v)
    if kind == "above":
        return above(k1)
    if kind == "below":
        return 1 - above(k1)
    if kind == "between" and k2:
        return max(0.0, above(k1) - above(k2))
    if kind == "up":
        return 1.0 if S >= k1 else min(1.0, 2 * (1 - _N(math.log(k1 / S) / v)))
    if kind == "down":
        return 1.0 if S <= k1 else min(1.0, 2 * (1 - _N(math.log(S / k1) / v)))
    return None


def scan() -> list[dict[str, Any]]:
    """All price-threshold markets ending within MAX_DAYS with model fair value, price and edge (best side)."""
    r = requests.get("https://gamma-api.polymarket.com/events", params={"active": "true", "closed": "false", "order": "volume24hr", "ascending": "false", "limit": 400},
                     headers=UA, timeout=25)
    r.raise_for_status()
    now = time.time()
    out = []
    for ev in r.json():
        title = str(ev.get("title", ""))
        und = next(((sym, src) for rx, sym, src in UNDERLYING if re.search(rx, title.lower())), None)
        if not und or not re.search(r"above|between|price|hit|reach|dip", title.lower()):
            continue
        S, s = spot(und[0]), sigma(*und)
        if not S or not s:
            continue
        for m in ev.get("markets") or []:
            if m.get("closed"):
                continue
            try:
                end = dt.datetime.fromisoformat(str(m.get("endDate") or ev.get("endDate")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            days = (end - now) / 86400
            if days <= 0.25 or days > MAX_DAYS:  # under 6 hours the Yahoo vs exchange price gap dominates
                continue
            label = m.get("groupItemTitle") or m.get("question") or ""
            kind, k1, k2 = _strike(label if re.search(r"\d", label) else m.get("question", ""))
            if not kind or not k1:
                continue
            if kind == "above" and "between" in title.lower():
                kind = "between"
            fair = fair_prob(kind, S, k1, k2, s, days / 365)
            bid, ask = m.get("bestBid"), m.get("bestAsk")
            liq = float(m.get("liquidityNum") or 0)
            if fair is None or ask is None or liq < MIN_LIQ:
                continue
            bid = float(bid) if bid is not None else 0.0
            ask = float(ask)
            e_yes, e_no = fair - ask, bid - fair
            side, edge, price = ("YES", e_yes, ask) if e_yes >= e_no else ("NO", e_no, 1 - bid)
            out.append({"market_id": str(m.get("id")), "slug": ev.get("slug"), "title": title, "question": m.get("question"), "label": label, "kind": kind,
                        "underlying": und[0], "spot": round(S, 4), "strike": f"{k1:g}" + (f"-{k2:g}" if k2 else ""), "sigma": round(s, 4), "days": round(days, 2),
                        "end_ts": int(end), "bid": bid, "ask": ask, "fair": round(fair, 4), "side": side, "price": round(price, 4), "edge": round(edge, 4),
                        "liquidity": round(liq)})
    out.sort(key=lambda x: -x["edge"])
    return out


def record(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Paper-ledger every flagged opportunity once per market and side. Returns the new ones."""
    ensure()
    new = []
    with db.get_conn() as conn:
        for o in opps:
            if o["edge"] < MIN_EDGE:
                continue
            cur = conn.execute("INSERT OR IGNORE INTO pm_opps (ts, market_id, slug, title, question, side, price, fair, edge, kind, underlying, spot, strike, sigma, end_ts, liquidity) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (int(time.time()), o["market_id"], o["slug"], o["title"], o["question"], o["side"], o["price"],
                                                                            o["fair"], o["edge"], o["kind"], o["underlying"], o["spot"], o["strike"], o["sigma"], o["end_ts"], o["liquidity"]))
            if cur.rowcount:
                new.append(o)
    return new


def settle() -> int:
    """Close paper positions whose market resolved: outcome 1/0 for the side taken, P&L per share = outcome - price."""
    ensure()
    with db.get_conn() as conn:
        open_ = [dict(r) for r in conn.execute("SELECT id, market_id, side, price FROM pm_opps WHERE status='open' AND end_ts < ?", (int(time.time()) - 600,)).fetchall()]
    n = 0
    for o in open_:
        try:
            m = requests.get(f"https://gamma-api.polymarket.com/markets/{o['market_id']}", headers=UA, timeout=15).json()
            if not m.get("closed"):
                continue
            p = m.get("outcomePrices")
            p = json.loads(p) if isinstance(p, str) else p
            yes = float(p[0])
            if 0.02 < yes < 0.98:
                continue  # not settled cleanly yet
            win = (yes > 0.5) if o["side"] == "YES" else (yes < 0.5)
            with db.get_conn() as conn:
                conn.execute("UPDATE pm_opps SET status='settled', outcome=?, pnl=? WHERE id=?", (1.0 if win else 0.0, round((1.0 if win else 0.0) - o["price"], 4), o["id"]))
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def scorecard() -> dict[str, Any]:
    ensure()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM pm_opps ORDER BY ts DESC LIMIT 300").fetchall()]
    done = [r for r in rows if r["status"] == "settled"]
    return {"open": [r for r in rows if r["status"] == "open"], "settled": done[:50], "n_settled": len(done),
            "hit_rate": round(sum(r["outcome"] for r in done) / len(done), 3) if done else None,
            "avg_expected": (round(sum(r["fair"] if r["side"] == "YES" else 1 - r["fair"] for r in priced) / len(priced), 3) if (priced := [r for r in done if r["fair"] is not None]) else None),
            "pnl_per_share": round(sum(r["pnl"] for r in done), 3) if done else 0.0,
            "note": "Paper only. hit_rate vs avg_expected shows whether the model is calibrated; P&L is per $1-payout share at the price shown."}


def card(o: dict[str, Any]) -> tuple[str, list[list[dict]]]:
    from html import escape

    L = [f"🎯 <b>POLYMARKET OPPORTUNITY · {escape(o['side'])}</b>   <code>paper</code>", "", f"<b>{escape(str(o['question'] or o['title']))}</b>", "",
         f"<code>Price {o['price'] * 100:5.1f}¢   Model {(o['fair'] if o['side'] == 'YES' else 1 - o['fair']) * 100:5.1f}%   Edge +{o['edge'] * 100:.1f}pt</code>",
         f"<i>{escape(o['underlying'])} {o['spot']:,} · strike {escape(o['strike'])} · vol {o['sigma'] * 100:.0f}% · {o['days']:.1f} days · liquidity ${o['liquidity']:,}</i>", "",
         "<i>Lognormal model (thin tails: far strikes are underestimated). Tracked on paper to resolution, not a trade.</i>"]
    return "\n".join(L), [[{"text": "🎲 Open market", "url": f"https://polymarket.com/event/{o['slug']}"}]]


def tick(push: bool = True) -> dict[str, int]:
    """Scan, paper-record, settle, and push up to 3 new opportunities a day with edge >= 10 points."""
    from app.notify import telegram_bot

    try:
        opps = scan()
    except Exception:  # noqa: BLE001
        logger.exception("pm edge scan failed")
        return {"scanned": 0, "new": 0, "settled": settle()}
    new = record(opps)
    pushed = 0
    day = time.strftime("%Y-%m-%d")
    sent_today = int(db.meta_get(f"pm_edge_pushed:{day}") or 0)
    for o in sorted(new, key=lambda x: -x["edge"]):
        if push and o["edge"] >= 0.10 and sent_today < 3:
            html, b = card(o)
            telegram_bot.send_rich(html, b)
            sent_today += 1
            pushed += 1
    db.meta_set(f"pm_edge_pushed:{day}", str(sent_today))
    return {"scanned": len(opps), "new": len(new), "pushed": pushed, "settled": settle()}
