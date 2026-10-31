"""Daily EARNINGS-TODAY image album (Telegram, 10:00 New York, weekdays).

Cover -> overview of every notable reporter -> one slide per stock (largest by market cap, up to 8; Telegram albums cap at 10).
Each stock slide is built from that company's own data: BMO/AMC status, consensus vs last-year EPS, revenue estimate, last four
EPS surprises, 30-day chart, today's move, analyst rating/target, sector-specific "what matters", latest headlines. Once a
BMO company has printed, the slide shows actual vs estimate instead. Screening output, not investment advice.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app import db
from app.morning.brief import NY

logger = logging.getLogger(__name__)

SEND_AT = (10, 0)
STALE_AFTER = (15, 0)
MIN_MCAP = 2e9
MAX_STOCK_SLIDES = 8
OVERVIEW_ROWS = 12
W, H = 1080, 1350
M = 64
BG, PANEL, LINE = (6, 7, 11), (16, 18, 26), (34, 38, 52)
WHITE, MUTED, AMBER = (243, 244, 247), (139, 147, 167), (255, 160, 40)
GREEN, RED = (34, 197, 94), (239, 68, 68)
_state: dict[str, Any] = {"last_error": None, "last_sent": None}

SCHEMA = "CREATE TABLE IF NOT EXISTS earnings_reports (day TEXT PRIMARY KEY, created_at REAL NOT NULL, sent INTEGER NOT NULL DEFAULT 0, symbols TEXT NOT NULL DEFAULT '')"

WATCH = {
    "Technology": "Guidance and demand commentary (AI / cloud / cycle) usually move the stock more than the quarter itself.",
    "Communication Services": "Ad or subscriber trends and next-quarter guidance.",
    "Consumer Cyclical": "Same-store / unit demand, margins and the next-quarter outlook.",
    "Consumer Defensive": "Volume versus pricing, input-cost pressure on margins, and full-year guidance.",
    "Financial Services": "Net interest income or fee growth, credit quality, and buyback/capital return.",
    "Healthcare": "Pipeline and guidance updates; revenue mix versus the consensus.",
    "Industrials": "Backlog and orders, margin trend, and the full-year outlook.",
    "Energy": "Production and realised prices versus estimates, capex and payout.",
    "Basic Materials": "Realised pricing, volumes and cost inflation.",
    "Real Estate": "FFO versus estimates, occupancy and rate sensitivity.",
    "Utilities": "Earnings guidance, rate-case outcomes and capex plans.",
}


# ------------------------------------------------------------------ fonts / drawing helpers
def _font_path(names: list[str]) -> str | None:
    import os

    for base in (r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts/Supplemental"):
        for n in names:
            p = os.path.join(base, n)
            if os.path.exists(p):
                return p
    return None


@lru_cache(maxsize=64)
def F(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = {"black": ["seguibl.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"], "bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
             "reg": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"], "mono": ["consolab.ttf", "DejaVuSansMono-Bold.ttf"],
             "monor": ["consola.ttf", "DejaVuSansMono.ttf"]}[kind]
    p = _font_path(names)
    return ImageFont.truetype(p, size) if p else ImageFont.load_default()


def _tw(d: ImageDraw.ImageDraw, s: str, font) -> float:
    return d.textlength(s, font=font)


def _text(d, xy, s, font, fill, anchor="la", spacing=0.0):
    if spacing:
        x, y = xy
        if anchor[0] == "r":
            x -= sum(_tw(d, c, font) + spacing for c in s) - spacing
        for c in s:
            d.text((x, y), c, font=font, fill=fill, anchor="l" + anchor[1])
            x += _tw(d, c, font) + spacing
    else:
        d.text(xy, s, font=font, fill=fill, anchor=anchor)


def _fit(d, s: str, font, maxw: float) -> str:
    if _tw(d, s, font) <= maxw:
        return s
    while s and _tw(d, s + "…", font) > maxw:
        s = s[:-1]
    return s.rstrip() + "…"


def _wrap(d, s: str, font, maxw: float, lines: int) -> list[str]:
    words, out, cur = s.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if _tw(d, t, font) <= maxw:
            cur = t
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = _fit(d, out[-1] + " …", font, maxw)
    return [x for x in out if x]


def _new() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for y in range(0, H, 3):  # faint warm vignette at the top
        k = max(0.0, 1 - y / 700) * 0.07
        d.rectangle([0, y, W, y + 3], fill=(int(BG[0] + 255 * k), int(BG[1] + 120 * k), int(BG[2] + 20 * k)))
    return img, d


def _mark(d, x, y, s=54):
    d.rounded_rectangle([x, y, x + s, y + s], radius=13, fill=(10, 12, 17), outline=AMBER, width=2)
    k = s / 48
    j = [(13, 30), (13, 37), (17, 40), (22, 40), (27, 37), (29, 31), (29, 13)]
    d.line([(x + a * k, y + b * k) for a, b in j], fill=AMBER, width=int(5 * k), joint="curve")
    d.line([(x + 21 * k, y + 21 * k), (x + 29 * k, y + 12 * k), (x + 37 * k, y + 21 * k)], fill=WHITE, width=int(4 * k), joint="curve")


def _frame(d, label: str, idx: int, total: int):
    _mark(d, M, 44)
    _text(d, (M + 70, 71), "JORDAN TERMINAL", F("black", 26), WHITE, "lm", 2.5)
    _text(d, (W - M, 71), label, F("mono", 24), AMBER, "rm", 3)
    _text(d, (M, H - 52), "SCREENING OUTPUT  -  NOT INVESTMENT ADVICE", F("monor", 20), MUTED, "lm", 2)
    x = W - M - (34 + (total - 1) * 22)
    for i in range(total):
        w = 34 if i == idx else 10
        d.rounded_rectangle([x, H - 57, x + w, H - 47], radius=5, fill=AMBER if i == idx else LINE)
        x += w + 12


def _chip(d, x, y, s, color, font=None, pad=16, h=40):
    font = font or F("mono", 20)
    w = _tw(d, s, font) + pad * 2 + len(s) * 1.5
    d.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=tuple(int(c * 0.18 + PANEL[i] * 0.82) for i, c in enumerate(color)), outline=color, width=2)
    _text(d, (x + pad, y + h / 2 + 1), s, font, color, "lm", 1.5)
    return x + w


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG", optimize=True)
    return b.getvalue()


# ------------------------------------------------------------------ formatting
def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"${v / div:.2f}{suf}" if a < 100 * div else f"${v / div:.0f}{suf}"
    return f"${v:,.0f}"


def _eps(v: float | None) -> str:
    return "n/a" if v is None else f"{'-' if v < 0 else ''}${abs(v):.2f}"


def _pct(v: float | None, d: int = 1) -> str:
    return "n/a" if v is None else f"{v:+.{d}f}%"


def _col(v: float | None) -> tuple[int, int, int]:
    return MUTED if v is None else (GREEN if v >= 0 else RED)


# ------------------------------------------------------------------ data
def _stock_data(row: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    from app.api import invest_routes as inv
    from app.morning import catalysts

    sym = row["symbol"]
    out: dict[str, Any] = {"symbol": sym, "name": row.get("name") or quote.get("shortName") or sym, "when": row.get("when"), "row": row}
    out["price"] = quote.get("regularMarketPrice")
    out["move"] = quote.get("regularMarketChangePercent") if quote.get("marketState") == "REGULAR" else (quote.get("preMarketChangePercent") if quote.get("preMarketChangePercent") is not None else quote.get("regularMarketChangePercent"))
    out["mcap"] = quote.get("marketCap") or row.get("mcap")
    try:
        qs = inv._qs(sym, "recommendationTrend,earningsTrend,earningsHistory,financialData,assetProfile")
    except Exception:  # noqa: BLE001
        qs = {}
    out["sector"] = (qs.get("assetProfile") or {}).get("sector")
    fd = qs.get("financialData") or {}
    out["rating"] = (fd.get("recommendationKey") or "").upper().replace("_", " ") or None
    tgt, px = fd.get("targetMeanPrice"), fd.get("currentPrice") or out["price"]
    out["upside"] = 100 * (tgt / px - 1) if tgt and px else None
    out["n_analysts"] = fd.get("numberOfAnalystOpinions")
    trend = next((t for t in ((qs.get("earningsTrend") or {}).get("trend") or []) if t.get("period") == "0q"), {})
    out["rev_est"] = (trend.get("revenueEstimate") or {}).get("avg")
    out["rev_yago"] = (trend.get("revenueEstimate") or {}).get("yearAgoRevenue")
    out["eps_est"] = row.get("eps_est") if row.get("eps_est") is not None else (trend.get("earningsEstimate") or {}).get("avg")
    out["eps_yago"] = row.get("eps_last_year") if row.get("eps_last_year") is not None else (trend.get("earningsEstimate") or {}).get("yearAgoEps")
    out["eps_actual"], out["surprise"] = row.get("eps"), None
    if out["eps_actual"] is not None and out["eps_est"]:
        out["surprise"] = 100 * (out["eps_actual"] - out["eps_est"]) / abs(out["eps_est"])
    out["hist"] = [{"a": h.get("epsActual"), "e": h.get("epsEstimate"), "s": (h.get("surprisePercent") or 0) * 100 if h.get("surprisePercent") is not None else None}
                   for h in ((qs.get("earningsHistory") or {}).get("history") or []) if h.get("epsActual") is not None][-4:]
    h = inv._safe_history(sym, "1mo", "1d")
    out["spark"] = [c[4] for c in (h or {}).get("candles", []) if c and c[4] is not None]
    try:
        items = inv._news(sym, True)
    except Exception:  # noqa: BLE001
        items = []
    cutoff = time.time() - 72 * 3600
    out["heads"] = [{"t": i["title"], "src": i.get("source")} for i in items if (i.get("ts") or 0) >= cutoff][:2]
    out["typical"] = catalysts._range_str(catalysts._mcap_bucket(out["mcap"], out["sector"]), "+" if catalysts._mcap_bucket(out["mcap"], out["sector"]) == "earnings_biotech" else "")
    return out


def collect(day: str | None = None) -> dict[str, Any]:
    from app.api import market_routes as mr
    from app.morning import brief

    day = day or dt.datetime.now(NY).date().isoformat()
    rows = [r for r in mr._earn_day(day) if r.get("symbol") and (r.get("mcap") or 0) >= MIN_MCAP]
    rows.sort(key=lambda r: -(r["mcap"] or 0))
    quotes = brief._quotes([r["symbol"] for r in rows[:OVERVIEW_ROWS]]) if rows else {}
    with ThreadPoolExecutor(4) as pool:
        stocks = list(pool.map(lambda r: _stock_data(r, quotes.get(r["symbol"], {})), rows[:MAX_STOCK_SLIDES]))
    extra = []
    for r in rows[MAX_STOCK_SLIDES:OVERVIEW_ROWS]:
        q = quotes.get(r["symbol"], {})
        extra.append({"symbol": r["symbol"], "name": r.get("name"), "when": r.get("when"), "row": r, "eps_est": r.get("eps_est"), "eps_actual": r.get("eps"),
                      "move": q.get("regularMarketChangePercent"), "mcap": r.get("mcap"), "surprise": None})
    return {"day": day, "total": len(rows), "stocks": stocks, "extra": extra,
            "bmo": sum(1 for r in rows if r.get("when") == "BMO"), "amc": sum(1 for r in rows if r.get("when") == "AMC")}


# ------------------------------------------------------------------ slides
def _when_label(w: str | None) -> tuple[str, tuple[int, int, int]]:
    return {"BMO": ("BEFORE OPEN", AMBER), "AMC": ("AFTER CLOSE", (96, 165, 250))}.get(w or "", ("TIME TBA", MUTED))


def render_cover(data: dict[str, Any], total: int) -> bytes:
    img, d = _new()
    _frame(d, "EARNINGS TODAY", 0, total)
    day = dt.date.fromisoformat(data["day"])
    _text(d, (M, 190), day.strftime("%A  %d %B %Y").upper(), F("mono", 26), AMBER, "lm", 4)
    _text(d, (M, 320), "TODAY'S", F("black", 118), WHITE, "ls")
    _text(d, (M, 440), "EARNINGS", F("black", 118), AMBER, "ls")
    _text(d, (M, 560), "REPORTS", F("black", 118), WHITE, "ls")
    sub = f"{data['total']} notable companies report today  -  {data['bmo']} before the open, {data['amc']} after the close."
    for i, ln in enumerate(_wrap(d, sub, F("reg", 34), W - 2 * M, 2)):
        _text(d, (M, 610 + i * 46), ln, F("reg", 34), MUTED, "la")
    y = 740
    for s in data["stocks"][:5]:
        lab, colr = _when_label(s["when"])
        d.rounded_rectangle([M, y, W - M, y + 78], radius=16, fill=PANEL, outline=LINE)
        _text(d, (M + 28, y + 39), s["symbol"], F("black", 40), WHITE, "lm")
        _text(d, (M + 250, y + 39), _fit(d, s["name"], F("reg", 26), 270), F("reg", 26), MUTED, "lm")
        _chip(d, W - M - 420, y + 19, lab, colr, F("mono", 17), 12, 40)
        _text(d, (W - M - 28, y + 39), _pct(s["move"]) if s["move"] is not None else "", F("mono", 30), _col(s["move"]), "rm")
        y += 90
    _text(d, (M, y + 18), "SWIPE  >  ONE SLIDE PER STOCK", F("mono", 22), AMBER, "lm", 3)
    return _png(img)


def render_overview(data: dict[str, Any], total: int) -> bytes:
    img, d = _new()
    _frame(d, "ALL REPORTERS", 1, total)
    _text(d, (M, 170), "WHO REPORTS TODAY", F("black", 64), WHITE, "lm")
    _text(d, (M, 222), f"{data['total']} companies above $2B market cap  -  largest shown", F("reg", 26), MUTED, "lm")
    rows = data["stocks"] + data["extra"]
    y = 270
    hdr = F("monor", 19)
    _text(d, (M + 24, y), "SYMBOL", hdr, MUTED, "lm", 2)
    _text(d, (430, y), "WHEN", hdr, MUTED, "lm", 2)
    _text(d, (690, y), "EPS EST", hdr, MUTED, "rm", 2)
    _text(d, (W - M - 24, y), "MOVE", hdr, MUTED, "rm", 2)
    y += 26
    for r in rows[:OVERVIEW_ROWS]:
        lab, colr = _when_label(r["when"])
        d.rounded_rectangle([M, y, W - M, y + 76], radius=14, fill=PANEL, outline=LINE)
        _text(d, (M + 24, y + 28), r["symbol"], F("black", 34), WHITE, "lm")
        _text(d, (M + 24, y + 58), _fit(d, r["name"] or "", F("reg", 20), 300), F("reg", 20), MUTED, "lm")
        _text(d, (430, y + 38), lab, F("mono", 19), colr, "lm", 1.5)
        est = r.get("eps_est")
        act = r.get("eps_actual")
        _text(d, (690, y + 38), (f"{_eps(act)} / " if act is not None else "") + _eps(est), F("mono", 24), WHITE, "rm")
        _text(d, (W - M - 24, y + 38), _pct(r.get("move")) if r.get("move") is not None else "-", F("mono", 28), _col(r.get("move")), "rm")
        y += 84
    return _png(img)


def _spark(d, box, vals: list[float], color):
    x0, y0, x1, y1 = box
    d.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=PANEL, outline=LINE)
    if len(vals) < 2:
        _text(d, ((x0 + x1) / 2, (y0 + y1) / 2), "chart unavailable", F("reg", 24), MUTED, "mm")
        return
    lo, hi = min(vals), max(vals)
    pad = 22
    pts = [(x0 + pad + (x1 - x0 - 2 * pad) * i / (len(vals) - 1), y1 - pad - (y1 - y0 - pad - 58) * ((v - lo) / ((hi - lo) or 1))) for i, v in enumerate(vals)]
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.polygon(pts + [(pts[-1][0], y1 - 4), (pts[0][0], y1 - 4)], fill=color + (46,))
    d._image.paste(ov, (0, 0), ov)
    d.line(pts, fill=color, width=5, joint="curve")
    d.ellipse([pts[-1][0] - 8, pts[-1][1] - 8, pts[-1][0] + 8, pts[-1][1] + 8], fill=color)
    _text(d, (x0 + 22, y0 + 22), "LAST 30 DAYS  -  DAILY CLOSE", F("monor", 18), MUTED, "lm", 2)


def _watch_line(s: dict[str, Any]) -> str:
    bits = []
    if s["eps_actual"] is not None and s["surprise"] is not None:
        bits.append(f"Printed {_eps(s['eps_actual'])} vs {_eps(s['eps_est'])} expected ({'beat' if s['surprise'] >= 0 else 'miss'} {abs(s['surprise']):.0f}%)"
                    + (f"; shares {_pct(s['move'])} so far." if s["move"] is not None else "."))
    elif s["eps_est"] is not None:
        yoy = f" ({100 * (s['eps_est'] / s['eps_yago'] - 1):+.0f}% YoY)" if s["eps_yago"] and s["eps_yago"] > 0 and s["eps_est"] > 0 else ""
        bits.append(f"Consensus EPS {_eps(s['eps_est'])} vs {_eps(s['eps_yago'])} a year ago{yoy}.")
    beats = [h for h in s["hist"] if (h["s"] or 0) > 0]
    if s["hist"]:
        bits.append(f"Beat EPS in {len(beats)} of the last {len(s['hist'])} quarters.")
    bits.append(WATCH.get(s.get("sector") or "", "Guidance versus consensus usually decides the reaction."))
    return " ".join(bits)


def render_stock(s: dict[str, Any], idx: int, total: int) -> bytes:
    img, d = _new()
    lab, colr = _when_label(s["when"])
    _frame(d, f"EARNINGS  {idx - 1}/{total - 2}", idx, total)
    sym_font = F("black", 150 if len(s["symbol"]) <= 4 else 120)
    _text(d, (M, 200), s["symbol"], sym_font, WHITE, "lm")
    _text(d, (M, 298), _fit(d, f"{s['name']}  -  {s.get('sector') or 'n/a'}", F("reg", 26), 600), F("reg", 26), MUTED, "lm")
    mv = s["move"]
    _text(d, (W - M, 190), _pct(mv), F("mono", 76), _col(mv), "rm")
    _text(d, (W - M, 262), (f"${s['price']:,.2f}" if s["price"] else "") + "  TODAY", F("mono", 26), MUTED, "rm", 2)
    x = _chip(d, M, 338, lab, colr)
    if s["surprise"] is not None:
        _chip(d, x + 12, 338, f"EPS {'BEAT' if s['surprise'] >= 0 else 'MISS'} {abs(s['surprise']):.0f}%", GREEN if s["surprise"] >= 0 else RED)
    elif s["when"] == "BMO":
        _chip(d, x + 12, 338, "REPORTED - ACTUALS PENDING", MUTED)
    else:
        _chip(d, x + 12, 338, "UPCOMING", AMBER)
    _spark(d, (M, 398, W - M, 600), s["spark"], _col((s["spark"][-1] - s["spark"][0]) if len(s["spark"]) > 1 else None) if s["spark"] else MUTED)

    # left panel: the print / consensus
    py = 622
    d.rounded_rectangle([M, py, 536, py + 240], radius=16, fill=PANEL, outline=LINE)
    _text(d, (M + 24, py + 30), "THE PRINT" if s["eps_actual"] is not None else "WHAT'S EXPECTED", F("mono", 20), AMBER, "lm", 3)
    if s["eps_actual"] is not None:
        _text(d, (M + 24, py + 92), _eps(s["eps_actual"]), F("black", 56), GREEN if (s["surprise"] or 0) >= 0 else RED, "lm")
        _text(d, (M + 24, py + 140), f"vs {_eps(s['eps_est'])} estimate", F("reg", 26), MUTED, "lm")
    else:
        _text(d, (M + 24, py + 92), _eps(s["eps_est"]), F("black", 56), WHITE, "lm")
        _text(d, (M + 24, py + 140), f"consensus EPS  -  {_eps(s['eps_yago'])} last year", F("reg", 22), MUTED, "lm")
    _text(d, (M + 24, py + 192), "REVENUE EST", F("monor", 17), MUTED, "lm", 2)
    _text(d, (M + 24, py + 222), _money(s["rev_est"]) + (f"   ({100 * (s['rev_est'] / s['rev_yago'] - 1):+.0f}% YoY)" if s["rev_est"] and s["rev_yago"] else ""), F("bold", 28), WHITE, "lm")

    # right panel: last 4 EPS surprises
    rx = 560
    d.rounded_rectangle([rx, py, W - M, py + 240], radius=16, fill=PANEL, outline=LINE)
    _text(d, (rx + 24, py + 30), "LAST 4 EPS SURPRISES", F("mono", 20), AMBER, "lm", 3)
    if s["hist"]:
        mx = max(10.0, max(abs(h["s"] or 0) for h in s["hist"]))
        bw, base = 70, py + 160
        for i, h in enumerate(s["hist"]):
            v = h["s"] or 0
            hh = max(6, (80 if v >= 0 else 44) * min(1.0, abs(v) / mx))
            bx = rx + 30 + i * (bw + 28)
            top = base - hh if v >= 0 else base
            d.rounded_rectangle([bx, top, bx + bw, top + hh], radius=6, fill=GREEN if v >= 0 else RED)
            _text(d, (bx + bw / 2, (top - 18) if v >= 0 else (top + hh + 18)), f"{v:+.0f}%", F("mono", 20), WHITE, "mm")
        d.line([rx + 20, base, W - M - 20, base], fill=LINE, width=2)
    else:
        _text(d, ((rx + W - M) / 2, py + 130), "no history", F("reg", 24), MUTED, "mm")

    # stats row
    sy = py + 260
    stats = [("MARKET CAP", _money(s["mcap"]), WHITE), ("ANALYSTS", s["rating"] or "n/a", WHITE), ("TARGET UPSIDE", _pct(s["upside"], 0), _col(s["upside"])),
             ("TYPICAL MOVE", s["typical"], AMBER)]
    cw = (W - 2 * M - 3 * 14) / 4
    for i, (k, v, c) in enumerate(stats):
        x0 = M + i * (cw + 14)
        d.rounded_rectangle([x0, sy, x0 + cw, sy + 100], radius=14, fill=PANEL, outline=LINE)
        _text(d, (x0 + 18, sy + 28), k, F("monor", 16), MUTED, "lm", 2)
        _text(d, (x0 + 18, sy + 68), _fit(d, v, F("black", 30), cw - 30), F("black", 30), c, "lm")

    # what to watch
    wy = sy + 122
    d.rectangle([M, wy, W - M, wy + 170], fill=(24, 20, 14))
    d.rectangle([M, wy, M + 6, wy + 170], fill=AMBER)
    _text(d, (M + 30, wy + 26), "WHAT TO WATCH", F("mono", 19), AMBER, "lm", 3)
    for i, ln in enumerate(_wrap(d, _watch_line(s), F("reg", 26), W - 2 * M - 60, 4)):
        _text(d, (M + 30, wy + 62 + i * 34), ln, F("reg", 26), WHITE, "lm")
    hy = wy + 196
    for h in s["heads"][:2]:
        _text(d, (M, hy), "> " + _fit(d, h["t"], F("reg", 23), W - 2 * M - 30), F("reg", 23), MUTED, "lm")
        hy += 34
    return _png(img)


def render_all(data: dict[str, Any]) -> list[tuple[str, bytes]]:
    total = 2 + len(data["stocks"])
    out = [("cover", render_cover(data, total)), ("overview", render_overview(data, total))]
    for i, s in enumerate(data["stocks"]):
        out.append((s["symbol"], render_stock(s, i + 2, total)))
    return out


# ------------------------------------------------------------------ schedule / send
def _ensure() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)


def _already_sent(day: str) -> bool:
    _ensure()
    with db.get_conn() as conn:
        r = conn.execute("SELECT sent FROM earnings_reports WHERE day = ?", (day,)).fetchone()
    return bool(r and r["sent"])


def send(day: str | None = None, force: bool = False) -> dict[str, Any]:
    from app.notify import telegram_bot

    _ensure()
    data = collect(day)
    day = data["day"]
    if not force and _already_sent(day):
        return {"day": day, "sent": False, "reason": "already sent"}
    if not data["stocks"]:
        telegram_bot.send_text(f"Earnings today ({day}): no notable companies (>$2B market cap) report.")
        ok, syms = True, []
    else:
        images = render_all(data)
        cap = f"EARNINGS TODAY {day} - {data['total']} notable reports ({data['bmo']} before open, {data['amc']} after close). Screening output, not investment advice."
        ok = telegram_bot.send_album([b for _n, b in images], cap)
        syms = [s["symbol"] for s in data["stocks"]]
    if ok:
        with db.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO earnings_reports (day, created_at, sent, symbols) VALUES (?,?,1,?)", (day, time.time(), ",".join(syms)))
    return {"day": day, "sent": bool(ok), "symbols": syms, "total": data["total"]}


def tick() -> None:
    now = dt.datetime.now(NY)
    hm = (now.hour, now.minute)
    if now.weekday() >= 5 or hm < SEND_AT or hm >= STALE_AFTER:
        return
    if _already_sent(now.date().isoformat()):
        return
    r = send()
    if r.get("sent"):
        _state["last_sent"] = r["day"]
        logger.info("earnings report sent for %s: %s", r["day"], r.get("symbols"))


def status() -> dict[str, Any]:
    return dict(_state)
