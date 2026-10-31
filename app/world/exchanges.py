"""Every stock exchange with a free data feed: live status (open / lunch / pre-open / closed, from Yahoo's own session times),
the main index, today's move, the move in units of that market's normal daily move (z), and a 5-minute sparkline.

Where Yahoo has no local index (Chile, Colombia, Peru, Qatar, Kuwait) the US-listed country ETF stands in, labelled as a proxy;
its status comes from the local exchange hours below. Where Yahoo serves the local index but no history (Saudi, Warsaw, Thailand...)
the normal daily move is taken from the country ETF. Big moves are pushed to Telegram once per exchange per day (world_tick).
"""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# code, exchange, country code, city, region, index symbol (None = proxy only), index name, country ETF (volatility / proxy), lunch break (local)
EXCHANGES: list[tuple[str, str, str, str, str, str | None, str, str | None, str | None]] = [
    ("NYSE", "New York Stock Exchange", "US", "New York", "Americas", "^NYA", "NYSE Composite", "SPY", None),
    ("NASDAQ", "Nasdaq", "US", "New York", "Americas", "^IXIC", "Nasdaq Composite", "QQQ", None),
    ("TSX", "Toronto Stock Exchange", "CA", "Toronto", "Americas", "^GSPTSE", "S&P/TSX Composite", "EWC", None),
    ("BMV", "Bolsa Mexicana de Valores", "MX", "Mexico City", "Americas", "^MXX", "IPC", "EWW", None),
    ("B3", "B3 (Brasil Bolsa Balcão)", "BR", "São Paulo", "Americas", "^BVSP", "Ibovespa", "EWZ", None),
    ("BYMA", "Bolsas y Mercados Argentinos", "AR", "Buenos Aires", "Americas", "^MERV", "Merval", "ARGT", None),
    ("BCS", "Santiago Stock Exchange", "CL", "Santiago", "Americas", None, "Chile (ECH proxy)", "ECH", None),
    ("BVC", "Bolsa de Valores de Colombia", "CO", "Bogotá", "Americas", None, "Colombia (COLO proxy)", "COLO", None),
    ("BVL", "Lima Stock Exchange", "PE", "Lima", "Americas", None, "Peru (EPU proxy)", "EPU", None),
    ("LSE", "London Stock Exchange", "GB", "London", "Europe", "^FTSE", "FTSE 100", "EWU", None),
    ("XETRA", "Deutsche Börse Xetra", "DE", "Frankfurt", "Europe", "^GDAXI", "DAX", "EWG", None),
    ("EPA", "Euronext Paris", "FR", "Paris", "Europe", "^FCHI", "CAC 40", "EWQ", None),
    ("AMS", "Euronext Amsterdam", "NL", "Amsterdam", "Europe", "^AEX", "AEX", "EWN", None),
    ("BRU", "Euronext Brussels", "BE", "Brussels", "Europe", "^BFX", "BEL 20", "EWK", None),
    ("LIS", "Euronext Lisbon", "PT", "Lisbon", "Europe", "PSI20.LS", "PSI", None, None),
    ("DUB", "Euronext Dublin", "IE", "Dublin", "Europe", "^ISEQ", "ISEQ Overall", "EIRL", None),
    ("MIL", "Euronext Milan (Borsa Italiana)", "IT", "Milan", "Europe", "FTSEMIB.MI", "FTSE MIB", "EWI", None),
    ("OSL", "Euronext Oslo Børs", "NO", "Oslo", "Europe", "OSEBX.OL", "OSEBX", "NORW", None),
    ("BME", "Bolsa de Madrid", "ES", "Madrid", "Europe", "^IBEX", "IBEX 35", "EWP", None),
    ("SIX", "SIX Swiss Exchange", "CH", "Zurich", "Europe", "^SSMI", "SMI", "EWL", None),
    ("VIE", "Wiener Börse", "AT", "Vienna", "Europe", "^ATX", "ATX", "EWO", None),
    ("STO", "Nasdaq Stockholm", "SE", "Stockholm", "Europe", "^OMXS30", "OMX Stockholm 30", "EWD", None),
    ("CPH", "Nasdaq Copenhagen", "DK", "Copenhagen", "Europe", "^OMXC25", "OMX Copenhagen 25", "EDEN", None),
    ("HEL", "Nasdaq Helsinki", "FI", "Helsinki", "Europe", "^OMXH25", "OMX Helsinki 25", "EFNL", None),
    ("TAL", "Nasdaq Tallinn", "EE", "Tallinn", "Europe", "^OMXTGI", "OMX Tallinn", None, None),
    ("RIG", "Nasdaq Riga", "LV", "Riga", "Europe", "^OMXRGI", "OMX Riga", None, None),
    ("VIL", "Nasdaq Vilnius", "LT", "Vilnius", "Europe", "^OMXVGI", "OMX Vilnius", None, None),
    ("WSE", "Warsaw Stock Exchange", "PL", "Warsaw", "Europe", "WIG20.WA", "WIG20", "EPOL", None),
    ("ATH", "Athens Stock Exchange", "GR", "Athens", "Europe", "GD.AT", "Athens General", "GREK", None),
    ("BIST", "Borsa Istanbul", "TR", "Istanbul", "Europe", "XU100.IS", "BIST 100", "TUR", None),
    ("TASE", "Tel Aviv Stock Exchange", "IL", "Tel Aviv", "Middle East & Africa", "TA35.TA", "TA-35", "EIS", None),
    ("TADAWUL", "Saudi Exchange (Tadawul)", "SA", "Riyadh", "Middle East & Africa", "^TASI.SR", "TASI", "KSA", None),
    ("DFM", "Dubai Financial Market", "AE", "Dubai", "Middle East & Africa", "DFMGI.AE", "DFM General", "UAE", None),
    ("ADX", "Abu Dhabi Securities Exchange", "AE", "Abu Dhabi", "Middle East & Africa", "FADGI.FGI", "FTSE ADX General", "UAE", None),
    ("QSE", "Qatar Stock Exchange", "QA", "Doha", "Middle East & Africa", None, "Qatar (QAT proxy)", "QAT", None),
    ("BK", "Boursa Kuwait", "KW", "Kuwait City", "Middle East & Africa", None, "Kuwait (KWT proxy)", "KWT", None),
    ("EGX", "Egyptian Exchange", "EG", "Cairo", "Middle East & Africa", "^CASE30", "EGX 30", None, None),
    ("JSE", "Johannesburg Stock Exchange", "ZA", "Johannesburg", "Middle East & Africa", "^J203.JO", "All Share", "EZA", None),
    ("TSE", "Tokyo Stock Exchange (JPX)", "JP", "Tokyo", "Asia-Pacific", "^N225", "Nikkei 225", "EWJ", "11:30-12:30"),
    ("SSE", "Shanghai Stock Exchange", "CN", "Shanghai", "Asia-Pacific", "000001.SS", "SSE Composite", "MCHI", "11:30-13:00"),
    ("SZSE", "Shenzhen Stock Exchange", "CN", "Shenzhen", "Asia-Pacific", "399001.SZ", "SZSE Component", "MCHI", "11:30-13:00"),
    ("HKEX", "Hong Kong Exchanges", "HK", "Hong Kong", "Asia-Pacific", "^HSI", "Hang Seng", "EWH", "12:00-13:00"),
    ("KRX", "Korea Exchange", "KR", "Seoul", "Asia-Pacific", "^KS11", "KOSPI", "EWY", None),
    ("KOSDAQ", "KOSDAQ", "KR", "Seoul", "Asia-Pacific", "^KQ11", "KOSDAQ", "EWY", None),
    ("TWSE", "Taiwan Stock Exchange", "TW", "Taipei", "Asia-Pacific", "^TWII", "TAIEX", "EWT", None),
    ("NSE", "National Stock Exchange of India", "IN", "Mumbai", "Asia-Pacific", "^NSEI", "Nifty 50", "INDA", None),
    ("BSE", "BSE (Bombay Stock Exchange)", "IN", "Mumbai", "Asia-Pacific", "^BSESN", "Sensex", "INDA", None),
    ("SGX", "Singapore Exchange", "SG", "Singapore", "Asia-Pacific", "^STI", "Straits Times", "EWS", "12:00-13:00"),
    ("BURSA", "Bursa Malaysia", "MY", "Kuala Lumpur", "Asia-Pacific", "^KLSE", "FBM KLCI", "EWM", "12:30-14:30"),
    ("IDX", "Indonesia Stock Exchange", "ID", "Jakarta", "Asia-Pacific", "^JKSE", "Jakarta Composite", "EIDO", "12:00-13:30"),
    ("SET", "Stock Exchange of Thailand", "TH", "Bangkok", "Asia-Pacific", "^SET.BK", "SET", "THD", "12:30-14:30"),
    ("PSE", "Philippine Stock Exchange", "PH", "Manila", "Asia-Pacific", "PSEI.PS", "PSEi", "EPHE", None),
    ("HOSE", "Ho Chi Minh Stock Exchange", "VN", "Ho Chi Minh City", "Asia-Pacific", "^VNINDEX.VN", "VN-Index", "VNM", "11:30-13:00"),
    ("ASX", "Australian Securities Exchange", "AU", "Sydney", "Asia-Pacific", "^AXJO", "S&P/ASX 200", "EWA", None),
    ("NZX", "New Zealand Exchange", "NZ", "Wellington", "Asia-Pacific", "^NZ50", "NZX 50", "ENZL", None),
]
# local hours for proxy-only markets (the ETF trades in New York): tz, open, close, weekdays (0=Mon)
LOCAL_HOURS = {"BCS": ("America/Santiago", "09:30", "16:00", (0, 1, 2, 3, 4)), "BVC": ("America/Bogota", "09:30", "16:00", (0, 1, 2, 3, 4)),
               "BVL": ("America/Lima", "09:00", "16:00", (0, 1, 2, 3, 4)), "QSE": ("Asia/Qatar", "09:30", "13:15", (6, 0, 1, 2, 3)),
               "BK": ("Asia/Kuwait", "09:00", "12:40", (6, 0, 1, 2, 3)),
               # Yahoo reports these in the wrong time zone (New York / London), so their local hours are used
               "STO": ("Europe/Stockholm", "09:00", "17:30", (0, 1, 2, 3, 4)), "CPH": ("Europe/Copenhagen", "09:00", "17:00", (0, 1, 2, 3, 4)),
               "HEL": ("Europe/Helsinki", "10:00", "18:30", (0, 1, 2, 3, 4)), "TAL": ("Europe/Tallinn", "10:00", "16:00", (0, 1, 2, 3, 4)),
               "RIG": ("Europe/Riga", "10:00", "16:00", (0, 1, 2, 3, 4)), "VIL": ("Europe/Vilnius", "10:00", "16:00", (0, 1, 2, 3, 4)),
               "ADX": ("Asia/Dubai", "10:00", "15:00", (0, 1, 2, 3, 4))}
NO_FREE_DATA = ["Moscow Exchange (sanctions; Yahoo stopped)", "Prague", "Budapest", "Bucharest", "Sofia", "Nigeria (NGX)", "Kenya (NSE)", "Morocco (Casablanca)",
                "Pakistan (PSX)", "Sri Lanka (CSE)", "Bangladesh (DSE)", "Iceland", "Bahrain", "Oman", "Kazakhstan"]

_q: dict[str, tuple[float, Any]] = {}
_vol: dict[str, tuple[float, float | None]] = {}


def _intraday(sym: str) -> dict[str, Any] | None:
    from app.api import invest_routes as ir

    hit = _q.get(sym)
    if hit and time.time() - hit[0] < 55:
        return hit[1]
    try:
        h = ir._history(sym, "1d", "5m")
        val = {"meta": h["meta"], "spark": [round(c[4], 4) for c in h["candles"] if c[4]][-90:]}
    except Exception:  # noqa: BLE001
        val = None
    _q[sym] = (time.time(), val)
    return val


def _daily_vol(sym: str | None) -> float | None:
    """Standard deviation of daily % moves over the last year (cached 12h)."""
    from app.api import invest_routes as ir

    if not sym:
        return None
    hit = _vol.get(sym)
    if hit and time.time() - hit[0] < 12 * 3600:
        return hit[1]
    try:
        c = [x[4] for x in ir._history(sym, "1y", "1d")["candles"] if x[4]]
        r = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
        v = (sum(x * x for x in r) / len(r)) ** 0.5 * 100 if len(r) >= 60 else None
    except Exception:  # noqa: BLE001
        v = None
    _vol[sym] = (time.time(), v)
    return v


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _status(code: str, meta: dict[str, Any] | None, lunch: str | None, now: float) -> dict[str, Any]:
    """open / lunch / pre-open / closed with the next change time, from Yahoo's session for that exchange (or local hours for proxies)."""
    if code in LOCAL_HOURS or not meta:
        tzn, o, c, days = LOCAL_HOURS.get(code, ("UTC", "00:00", "00:00", ()))
        tz = ZoneInfo(tzn)
        loc = datetime.fromtimestamp(now, tz)
        mins = loc.hour * 60 + loc.minute
        today = loc.weekday() in days
        st = "OPEN" if today and _hm(o) <= mins < _hm(c) else "PRE" if today and mins < _hm(o) else "CLOSED"
        return {"status": st, "tz": tzn, "local": loc.strftime("%H:%M"), "hours": f"{o}-{c}", "approx": True}
    tzn = meta.get("exchangeTimezoneName") or "UTC"
    loc = datetime.fromtimestamp(now, ZoneInfo(tzn))
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = reg.get("start"), reg.get("end")
    out = {"tz": tzn, "local": loc.strftime("%H:%M"), "start": start, "end": end}
    if start and end:
        out["hours"] = datetime.fromtimestamp(start, ZoneInfo(tzn)).strftime("%H:%M") + "-" + datetime.fromtimestamp(end, ZoneInfo(tzn)).strftime("%H:%M")
        fresh = (now - (meta.get("regularMarketTime") or 0)) < 30 * 60
        if start <= now < end and (fresh or now - start < 20 * 60):
            st = "OPEN"
            if lunch:
                a, b = lunch.split("-")
                if _hm(a) <= loc.hour * 60 + loc.minute < _hm(b):
                    st = "LUNCH"
        elif now < start:
            st = "PRE"
        else:
            st = "CLOSED"
        out["status"] = st
        out["next"] = start if st == "PRE" else end if st in ("OPEN", "LUNCH") else None
    else:
        out["status"] = "CLOSED"
    return out


def snapshot() -> dict[str, Any]:
    now = time.time()
    syms = sorted({e[5] or e[7] for e in EXCHANGES if e[5] or e[7]})
    vsyms = sorted({e[7] or e[5] for e in EXCHANGES if e[7] or e[5]})
    with ThreadPoolExecutor(10) as ex:
        q = dict(zip(syms, ex.map(_intraday, syms)))
        vol = dict(zip(vsyms, ex.map(_daily_vol, vsyms)))
    rows = []
    for code, name, cc, city, region, idx, iname, etf, lunch in EXCHANGES:
        src = idx or etf
        d = q.get(src) or {}
        m = d.get("meta") or {}
        last, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose") or m.get("previousClose")
        pct = (last / prev - 1) * 100 if last and prev else None
        sd = vol.get(src) if src in vol and vol.get(src) else vol.get(etf or "")
        st = _status(code, m if idx and code not in LOCAL_HOURS else None, lunch, now)
        rows.append({"code": code, "exchange": name, "cc": cc, "city": city, "region": region, "symbol": src, "index": iname, "proxy": idx is None,
                     "currency": m.get("currency"), "last": last, "prev": prev, "pct": round(pct, 2) if pct is not None else None,
                     "z": round(pct / sd, 1) if pct is not None and sd else None, "normal": round(sd, 2) if sd else None,
                     "updated": m.get("regularMarketTime"), "spark": d.get("spark") or [], **st})
    open_n = sum(1 for r in rows if r.get("status") in ("OPEN", "LUNCH"))
    return {"at": int(now), "exchanges": rows, "open": open_n, "no_free_data": NO_FREE_DATA}


def _related_news(r: dict[str, Any], hours: float = 12) -> list[str]:
    from app import db
    from app.world.countries import COUNTRIES

    country = (COUNTRIES.get(r["cc"]) or (r["cc"],))[0]
    keys = {country.lower(), r["city"].lower(), r["index"].split(" (")[0].lower()}
    try:
        with db.read_conn() as conn:
            rows = conn.execute("SELECT title FROM news_stories WHERE ts > ? AND ntype NOT IN ('noise') ORDER BY ts DESC LIMIT 600", (int(time.time() - hours * 3600),)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [x[0] for x in rows if any(k and k in (x[0] or "").lower() for k in keys)][:3]


def world_tick() -> int:
    """Push a Telegram card when an exchange makes an unusual move (|z| >= 3 and |move| >= 2%), once per exchange per day."""
    import json
    from html import escape

    from app import db
    from app.notify import telegram_bot

    snap = snapshot()
    day = time.strftime("%Y-%m-%d")
    done = json.loads(db.meta_get("world_alerted") or "{}")
    done = {k: v for k, v in done.items() if v == day}
    sent = 0
    for r in snap["exchanges"]:
        if r["pct"] is None or r["z"] is None or r["code"] in done or abs(r["z"]) < 3 or abs(r["pct"]) < 2:
            continue
        if r.get("updated") and time.time() - r["updated"] > 20 * 3600:
            continue  # stale quote (holiday / old close)
        arrow = "📈" if r["pct"] > 0 else "📉"
        L = [f"🌍 <b>WORLD MARKET MOVE · {escape(r['exchange'])}</b>", "",
             f"{arrow} <b>{escape(r['index'])} {r['pct']:+.2f}%</b> · {abs(r['z']):.1f}× its normal daily move ({r['normal']:.2f}%)",
             f"<i>{escape(r['city'])} · {r.get('status', '').lower()} · local {r.get('local', '')}{' · US-listed ETF proxy' if r['proxy'] else ''}</i>"]
        news = _related_news(r)
        if news:
            L += ["", "<b>Related headlines</b>"] + [f"• {escape(t[:150])}" for t in news]
        peers = [x for x in snap["exchanges"] if x["region"] == r["region"] and x["code"] != r["code"] and x["pct"] is not None]
        if peers:
            L += ["", "Region: " + escape(" · ".join(f"{x['index']} {x['pct']:+.1f}%" for x in sorted(peers, key=lambda x: -abs(x["pct"]))[:5]))]
        L += ["", "<i>NOSTRO World Exchanges · not investment advice</i>"]
        if telegram_bot.send_rich("\n".join(L), []):
            sent += 1
        done[r["code"]] = day
    db.meta_set("world_alerted", json.dumps(done))
    return sent


def safe(v: Any) -> Any:
    return None if isinstance(v, float) and math.isnan(v) else v
