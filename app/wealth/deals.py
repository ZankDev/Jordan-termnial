"""DEALS — physical businesses for sale in Israel, pulled from public listing boards and scored.

Sources (public pages whose robots.txt allows these listing pages; fetched politely, a few pages, at most every 6 hours):
  monopoli.co.il   "לוח עסקים למכירה"           /businesses-list?type=2&page=N
  brokerli.com     business brokerage listings   /property_type/עסקים-למכירה/page/N/
  menivim.net      commercial board, businesses  /category/businessesForSale/
Not scraped: Yad2 (JavaScript + bot protection — linked instead), Shuk Asakim (blocks automated access), Facebook groups
(need a logged-in account). They are listed on the page as links to browse by hand.

Every listing: title, asking price, city/region, category, description, link, first/last seen. From the text the parser
tries to read the annual turnover ("מחזור …") and the monthly owner profit ("משאיר/רווח … בחודש") — when both price and
profit are known it computes the payback period (price / yearly profit) and a multiple, the single most useful screen for a
small business. Numbers come from the seller's ad, unverified — due diligence is on the buyer.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any

import requests

from app import db

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept-Language": "he,en;q=0.8"}
SCHEMA = """CREATE TABLE IF NOT EXISTS deals (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, ext_id TEXT NOT NULL, title TEXT NOT NULL,
    price REAL, city TEXT, region TEXT, category TEXT, description TEXT, url TEXT NOT NULL, turnover_year REAL, profit_month REAL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, starred INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, ext_id))"""
PAGES = 3
REFRESH_SECONDS = 6 * 3600
MANUAL_SOURCES = [
    {"name": "Yad2 — עסקים למכירה", "url": "https://www.yad2.co.il/products/businesses-for-sale?category=37"},
    {"name": "שוק עסקים", "url": "https://www.shukasakim.co.il/"},
    {"name": "Insight Israel (brokerage)", "url": "https://insight-israel.co.il/"},
    {"name": "Facebook: search 'עסקים למכירה' groups", "url": "https://www.facebook.com/search/groups/?q=%D7%A2%D7%A1%D7%A7%D7%99%D7%9D%20%D7%9C%D7%9E%D7%9B%D7%99%D7%A8%D7%94"},
]
CATS = [
    ("Food & restaurants", ("מסעד", "קפה", "פיצ", "פלאפל", "שווארמ", "בורגר", "סושי", "מאפי", "קונדיטור", "גלידר", "ביסטרו", "בית קפה", "קייטרינג", "מזון", "פאב", "בר ")),
    ("Grocery & retail", ("סופר", "מכולת", "מינימרקט", "חנות", "קמעונא", "פיצוחים", "תבלינ", "ירקן", "אטליז", "קיוסק", "פרחים", "צעצוע", "ביגוד", "הלבשה", "תכשיט", "אופטיק")),
    ("Services", ("מספרה", "קוסמטיק", "ציפורניים", "כביסה", "ניקיון", "הובלות", "מוסך", "שטיפ", "סטודיו", "חוגים", "לימוד", "גן ילדים", "מעון", "מכון כושר", "ספא", "קליניק", "רפואי")),
    ("Industry & wholesale", ("מפעל", "ייצור", "יבוא", "סיטונא", "הפצה", "לוגיסט", "מחסן", "נגרי", "מסגרי", "דפוס", "קבלני", "בנייה", "שיפוצ")),
    ("Online & tech", ("אונליין", "אינטרנט", "אתר", "חנות מקוונת", "אפליקצי", "תוכנה", "דיגיטל", "סלולר", "סלולאר", "מחשבים")),
    ("Franchise", ("זכיינ", "זיכיון", "רשת ", "סניף")),
    ("Leisure & tourism", ("צימר", "מלון", "אירוח", "אטרקצי", "אירועים", "תיירות", "אולם")),
]
REGIONS = [
    ("Sharon", ("נתניה", "שרון", "אבן יהודה", "כפר יונה", "טייבה")),
    ("Tel Aviv & center", ("תל אביב", "רמת גן", "גבעתיים", "בני ברק", "חולון", "בת ים", "הרצליה", "רעננה", "כפר סבא", "פתח תקו", "ראשון לציון", "רחובות", "נס ציונה", "הוד השרון", "רמת השרון", "ראש העין", "לוד", "רמלה", "יהוד", "שוהם", "מודיעין", "גוש דן")),
    ("Jerusalem", ("ירושלים", "בית שמש", "מבשרת", "מעלה אדומים")),
    ("Haifa & north", ("חיפה", "קריות", "קרית", "עכו", "נהריה", "כרמיאל", "טבריה", "צפת", "עפולה", "נצרת", "יוקנעם", "קצרין", "צפון", "גליל", "קיסריה", "חדרה", "זכרון")),
    ("Shfela & south", ("אשדוד", "אשקלון", "באר שבע", "ב\"ש", "קרית גת", "שדרות", "אילת", "דימונה", "נתיבות", "אופקים", "שפלה", "דרום", "גדרה", "יבנה", "קרית מלאכי")),
]


def ensure() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA)


def _price(text: str) -> float | None:
    m = re.search(r"₪\s*([\d,]{4,})|([\d,]{4,})\s*₪", text)
    if not m:
        return None
    try:
        return float((m.group(1) or m.group(2)).replace(",", ""))
    except ValueError:
        return None


def _amount(txt: str) -> float | None:
    """'1.05 מיליון' -> 1_050_000, '30-35 אלף' -> 32_500, '450,000' -> 450000."""
    txt = txt.replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)(?:\s*[–-]\s*(\d+(?:\.\d+)?))?\s*(מיליון|מליון|מ'|אלף|k|K)?", txt)
    if not m:
        return None
    a = float(m.group(1))
    b = float(m.group(2)) if m.group(2) else a
    v = (a + b) / 2
    unit = m.group(3) or ""
    if unit.startswith("מ"):
        v *= 1_000_000
    elif unit in ("אלף", "k", "K"):
        v *= 1_000
    return v


def _financials(text: str) -> tuple[float | None, float | None]:
    t = text.replace("\n", " ")
    turnover = profit = None
    m = re.search(r"מחזור(?:\s+(?:שנתי|כספי|מכירות))?[^\d]{0,15}(\d[\d.,\s–-]*(?:מיליון|מליון|אלף)?)", t)
    if m:
        v = _amount(m.group(1))
        if v and v >= 50_000:
            turnover = v * (12 if re.search(r"מחזור\s+חודשי", t) else 1) if v < 5_000_000 or not re.search(r"מחזור\s+חודשי", t) else v
            if re.search(r"מחזור\s+חודשי", t):
                turnover = v * 12
    m = re.search(r"(?:משאיר|רווח(?:\s+נקי)?|הכנסה\s+לבעלים|רווחיות)[^\d]{0,25}(\d[\d.,\s–-]*(?:אלף|מיליון)?)\s*(?:₪|ש\"ח|שח)?\s*(?:ל?בחודש|לחודש|חודשי)", t)
    if m:
        v = _amount(m.group(1))
        if v and 3_000 <= v <= 2_000_000:
            profit = v
    return turnover, profit


def _classify(text: str, table: list[tuple[str, tuple[str, ...]]]) -> str | None:
    for name, words in table:
        if any(w in text for w in words):
            return name
    return None


def _get(url: str) -> str | None:
    try:
        r = requests.get(url, headers=UA, timeout=30)
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


def _monopoli() -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    out = []
    for p in range(1, PAGES + 1):
        html = _get(f"https://monopoli.co.il/businesses-list?type=2&page={p}")
        if not html:
            break
        s = BeautifulSoup(html, "html.parser")
        for d in s.select("div.item"):
            a = d.find("a", href=re.compile(r"/detail/\d+"))
            if not a:
                continue
            ext = re.search(r"/detail/(\d+)", a["href"]).group(1)
            h = d.select_one(".h3-wrapper") or d.find(["h3", "h2"])
            title = (h.get_text(" ", strip=True) if h else a.get_text(" ", strip=True))[:180]
            desc_el = d.select_one(".content-wrapper")
            text = d.get_text(" ", strip=True)
            out.append({"source": "monopoli", "ext_id": ext, "title": title, "price": _price(text), "description": (desc_el.get_text(" ", strip=True) if desc_el else text)[:600],
                        "url": "https://monopoli.co.il" + a["href"] if a["href"].startswith("/") else a["href"], "city": None, "text": text})
        time.sleep(1.0)
    return out


def _brokerli() -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    out = []
    base = "https://brokerli.com/property_type/%D7%A2%D7%A1%D7%A7%D7%99%D7%9D-%D7%9C%D7%9E%D7%9B%D7%99%D7%A8%D7%94/"
    for p in range(1, PAGES + 1):
        html = _get(base if p == 1 else f"{base}page/{p}/?property_type%5B0%5D=79")
        if not html:
            break
        s = BeautifulSoup(html, "html.parser")
        for d in s.select(".properties__item"):
            a = d.select_one("a.properties__address") or d.find("a", href=re.compile(r"/properties/"))
            if not a:
                continue
            txt = lambda sel: (d.select_one(sel).get_text(" ", strip=True) if d.select_one(sel) else "")  # noqa: E731
            params = [x.get_text(" ", strip=True) for x in d.select(".properties__params")]
            cat = next((x.split("-", 1)[1].strip() for x in params if x.startswith("סוג השקעה")), None)
            out.append({"source": "brokerli", "ext_id": a["href"].rstrip("/").rsplit("/", 1)[-1][:120], "title": txt(".properties__address-street") or a.get_text(" ", strip=True),
                        "price": _price(txt(".properties__offer-value")), "description": txt(".properties__intro")[:600], "url": a["href"],
                        "city": txt(".properties__address-city") or None, "hint": cat, "text": d.get_text(" ", strip=True)})
        time.sleep(1.0)
    return out


def _menivim() -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    html = _get("https://menivim.net/category/businessesForSale/")
    if not html:
        return []
    s = BeautifulSoup(html, "html.parser")
    out = []
    for a in s.find_all("a", href=re.compile(r"^/property/")):
        name, addr = a.select_one(".name"), a.select_one(".address")
        if not name:
            continue
        summ = a.select_one(".summary")
        out.append({"source": "menivim", "ext_id": a["href"].strip("/").rsplit("/", 1)[-1], "title": name.get_text(" ", strip=True),
                    "price": _price(a.get_text(" ", strip=True)), "description": (summ.get_text(" ", strip=True) if summ else ""), "url": "https://menivim.net" + a["href"],
                    "city": addr.get_text(" ", strip=True) if addr else None, "text": a.get_text(" ", strip=True)})
    return out


def refresh(force: bool = False) -> dict[str, Any]:
    ensure()
    last = float(db.meta_get("deals_refreshed_at") or 0)
    if not force and time.time() - last < REFRESH_SECONDS:
        return {"skipped": True, "refreshed_at": last}
    now = dt.datetime.now().isoformat(timespec="seconds")
    counts, new_items = {}, []
    for name, fn in (("monopoli", _monopoli), ("brokerli", _brokerli), ("menivim", _menivim)):
        try:
            items = fn()
        except Exception as e:  # noqa: BLE001 - one broken source must not stop the others
            counts[name] = f"error: {type(e).__name__}"
            continue
        counts[name] = len(items)
        with db.get_conn() as conn:
            for it in items:
                full = f"{it['title']} {it.get('description') or ''} {it.get('text') or ''} {it.get('hint') or ''}"
                turnover, profit = _financials(full)
                cat = _classify(full, CATS) or "Other"
                region = _classify(f"{it.get('city') or ''} {full}", REGIONS)
                cur = conn.execute("SELECT id FROM deals WHERE source=? AND ext_id=?", (it["source"], it["ext_id"])).fetchone()
                if cur:
                    conn.execute("UPDATE deals SET title=?, price=?, city=?, region=?, category=?, description=?, url=?, turnover_year=?, profit_month=?, last_seen=? WHERE id=?",
                                 (it["title"], it["price"], it.get("city"), region, cat, it.get("description"), it["url"], turnover, profit, now, cur["id"]))
                else:
                    conn.execute("INSERT INTO deals (source, ext_id, title, price, city, region, category, description, url, turnover_year, profit_month, first_seen, last_seen) "
                                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (it["source"], it["ext_id"], it["title"], it["price"], it.get("city"), region, cat, it.get("description"), it["url"], turnover, profit, now, now))
                    new_items.append(it["title"])
    db.meta_set("deals_refreshed_at", str(time.time()))
    return {"counts": counts, "new": len(new_items), "new_titles": new_items[:20], "refreshed_at": time.time()}


def listing(max_price: float | None = None, region: str | None = None, category: str | None = None, q: str | None = None,
            starred: bool = False, include_stale_days: int = 10) -> dict[str, Any]:
    ensure()
    cutoff = (dt.datetime.now() - dt.timedelta(days=include_stale_days)).isoformat(timespec="seconds")
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM deals WHERE hidden=0 AND last_seen >= ? ORDER BY first_seen DESC", (cutoff,)).fetchall()]
    new_cut = (dt.datetime.now() - dt.timedelta(days=2)).isoformat(timespec="seconds")
    out = []
    for r in rows:
        if max_price and r["price"] and r["price"] > max_price:
            continue
        if region and r["region"] != region:
            continue
        if category and r["category"] != category:
            continue
        if starred and not r["starred"]:
            continue
        if q and q.lower() not in f"{r['title']} {r['description'] or ''} {r['city'] or ''}".lower():
            continue
        yearly = r["profit_month"] * 12 if r["profit_month"] else None
        r["payback_years"] = round(r["price"] / yearly, 1) if r["price"] and yearly else None
        r["margin_pct"] = round(100 * yearly / r["turnover_year"], 1) if yearly and r["turnover_year"] else None
        r["price_to_turnover"] = round(r["price"] / r["turnover_year"], 2) if r["price"] and r["turnover_year"] else None
        r["new"] = r["first_seen"] >= new_cut
        out.append(r)
    priced = [r["price"] for r in out if r["price"]]
    by_cat: dict[str, list[float]] = {}
    for r in out:
        if r["price"]:
            by_cat.setdefault(r["category"] or "Other", []).append(r["price"])
    import statistics as st

    return {"deals": out, "count": len(out), "refreshed_at": float(db.meta_get("deals_refreshed_at") or 0),
            "median_price": st.median(priced) if priced else None,
            "by_category": sorted(({"category": k, "n": len(v), "median_price": st.median(v)} for k, v in by_cat.items()), key=lambda x: -x["n"]),
            "regions": [r for r, _ in REGIONS], "categories": [c for c, _ in CATS] + ["Other"], "manual_sources": MANUAL_SOURCES,
            "note": "Asking prices and financials are the seller's own claims from public ads — unverified. Payback = price ÷ (monthly owner profit × 12)."}


def mark(deal_id: int, starred: bool | None = None, hidden: bool | None = None) -> None:
    ensure()
    with db.get_conn() as conn:
        if starred is not None:
            conn.execute("UPDATE deals SET starred=? WHERE id=?", (int(starred), deal_id))
        if hidden is not None:
            conn.execute("UPDATE deals SET hidden=? WHERE id=?", (int(hidden), deal_id))
