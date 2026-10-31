"""Fast sources: public Telegram channels (squawk, Israel, geopolitics, crypto), Trump's Truth Social posts, and the existing RSS feeds, merged into
one stream of timestamped items and grouped into stories.

* Telegram: the public web preview `t.me/s/<channel>` (no login, exact post time). Only public channels; private groups need an api_id from
  my.telegram.org. The channel list lives in meta `intel_tg_channels` so channels can be added from the terminal.
* Truth Social: the trumpstruth.org RSS archive of Trump's posts.
* X itself needs a paid API token; the main squawk accounts are mirrored by the Telegram channels above.

Every item: {ts (publisher time, Unix), source, kind (squawk | truth | israel | geo | crypto | polymarket | rss), text, link, tickers}. The same story from
several sources is grouped by word overlap; the story time is the EARLIEST source, which is what the impact engine measures from.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import email.utils
import hashlib
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (NOSTRO terminal)"}
# verified 2026-10-09: each returns posts with timestamps from t.me/s/<name>
DEFAULT_CHANNELS: dict[str, str] = {
    "WalterBloomberg": "squawk", "financialjuice": "squawk", "marketfeed": "squawk",
    "amitsegal": "israel", "N12chat": "israel", "News_IL": "israel", "israelhayomofficial": "israel", "GlobesNews": "israel", "abualiexpress": "israel",
    "idfofficial": "israel", "intelslava": "geo", "disclosetv": "geo", "WatcherGuru": "crypto", "cointelegraph": "crypto", "PolymarketIntel": "polymarket",
    # added 2026-10-09 (each verified posting this week)
    "insiderpaper": "squawk", "whitehouse": "squawk", "ynetalerts": "israel", "mivzakim": "israel", "ILTVnews": "israel", "calcalist": "israel",
    "manniefabian": "israel", "Middle_East_Spectator": "geo", "warmonitors": "geo", "DDGeopolitics": "geo", "OSINTdefender": "geo", "clashreport": "geo",
    "AJENews_Official": "geo", "QudsNen": "geo", "PolymarketWhales": "polymarket",
}
TRUTH = "https://www.trumpstruth.org/feed"
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS news_items (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, source TEXT, kind TEXT, text TEXT, link TEXT, "
    "tickers TEXT, hash TEXT UNIQUE, story_id INTEGER, seen_at INTEGER)",
    "CREATE INDEX IF NOT EXISTS news_items_ts ON news_items (ts)",
    "CREATE TABLE IF NOT EXISTS news_stories (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, title TEXT, words TEXT, n_sources INTEGER DEFAULT 1, "
    "sources TEXT, kinds TEXT, tickers TEXT, ntype TEXT, direction TEXT, expected REAL, status TEXT DEFAULT 'new', verdict TEXT, explain TEXT, "
    "measured TEXT, alerted INTEGER DEFAULT 0, updated INTEGER)",
    "CREATE INDEX IF NOT EXISTS news_stories_ts ON news_stories (ts)",
)
STOP = {"the", "a", "an", "of", "to", "in", "on", "and", "for", "as", "at", "by", "with", "is", "are", "was", "be", "after", "over", "from", "that", "this", "it",
        "its", "will", "has", "have", "says", "said", "new", "more", "than", "into", "about", "per", "his", "her", "their", "את", "של", "על", "עם", "לא", "גם",
        "הוא", "היא", "זה", "כי", "אם", "או", "יותר", "בין", "אחרי", "לפני", "walterbloomberg", "breaking", "update"}
TICKER = re.compile(r"\$([A-Z]{1,5})\b")


_ready = False


def ensure() -> None:
    global _ready
    if _ready:
        return  # tables exist; skip taking the shared DB lock again
    with db.get_conn() as conn:
        for s in SCHEMA:
            conn.execute(s)
    _ready = True


def channels() -> dict[str, str]:
    raw = db.meta_get("intel_tg_channels")
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return dict(DEFAULT_CHANNELS)


def set_channels(ch: dict[str, str]) -> dict[str, str]:
    clean = {re.sub(r"[^A-Za-z0-9_]", "", k): (v if v in ("squawk", "israel", "geo", "crypto", "polymarket", "other") else "other") for k, v in ch.items() if k}
    db.meta_set("intel_tg_channels", json.dumps(clean))
    return clean


def _clean(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"\(\s*@\w+\s*\)", "", s)  # "( @WalterBloomberg )" signature
    s = re.sub(r"https?://\S+|t\.co/\S+", "", s)  # bare links carry no words
    return re.sub(r"\s+", " ", s).strip()


def telegram(channel: str, kind: str, before: int | None = None) -> list[dict[str, Any]]:
    """Posts from one public channel's web preview (newest ~20, or the page before message id `before`)."""
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    r = requests.get(url, headers=UA, timeout=12, allow_redirects=False)
    if r.status_code != 200:
        return []
    out = []
    for block in re.split(r'<div class="tgme_widget_message_wrap', r.text)[1:]:
        m_id = re.search(r'data-post="[^/]+/(\d+)"', block)
        m_tx = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
        m_ts = re.search(r'<time datetime="([^"]+)"', block)
        if not (m_id and m_tx and m_ts):
            continue
        text = _clean(m_tx.group(1))
        if len(text) < 12:
            continue
        ts = int(dt.datetime.fromisoformat(m_ts.group(1)).timestamp())
        out.append({"ts": ts, "source": f"tg:{channel}", "kind": kind, "text": text[:600], "link": f"https://t.me/{channel}/{m_id.group(1)}", "msg_id": int(m_id.group(1))})
    return out


def truth() -> list[dict[str, Any]]:
    r = requests.get(TRUTH, headers=UA, timeout=12)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).findall(".//item")[:30]:
        text = _clean(it.findtext("description") or it.findtext("title") or "")
        if len(text) < 12 or text.lower().startswith(("rt @", "[no title]")):
            continue
        try:
            ts = int(email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp())
        except Exception:  # noqa: BLE001
            continue
        out.append({"ts": ts, "source": "truth:realDonaldTrump", "kind": "truth", "text": text[:600], "link": (it.findtext("link") or "").strip()})
    return out


def rss() -> list[dict[str, Any]]:
    from app.intel import feeds

    return [{"ts": int(i["ts"]), "source": f"rss:{i['source']}", "kind": "israel" if i["group"] == "il" else "rss", "text": i["title"], "link": i["link"]} for i in feeds.fetch_all(max_age=240)]


def words(text: str) -> set[str]:
    text = re.sub(r"https?://\S+", " ", text.lower())
    return {w for w in re.findall(r"[\w']+", text) if len(w) > 2 and w not in STOP and not w.isdigit()}


def _hash(it: dict[str, Any]) -> str:
    return hashlib.sha1(f"{it['source']}|{it['text'][:200]}".encode()).hexdigest()


def _attach(conn, it: dict[str, Any], wset: set[str]) -> int:
    """Join the item to an existing story (>= 50% word overlap within 6 hours) or open a new one. A story keeps the earliest time."""
    best, best_sc = None, 0.0
    for s in conn.execute("SELECT id, ts, words, sources, kinds, tickers, n_sources FROM news_stories WHERE ts > ?", (it["ts"] - 6 * 3600,)).fetchall():
        sw = set(json.loads(s["words"] or "[]"))
        if not sw or not wset:
            continue
        sc = len(sw & wset) / min(len(sw), len(wset))
        if sc > best_sc:
            best, best_sc = s, sc
    if best is not None and best_sc >= 0.5 and len(wset) >= 3:
        srcs = set(json.loads(best["sources"] or "[]")) | {it["source"]}
        kinds = set(json.loads(best["kinds"] or "[]")) | {it["kind"]}
        tick = set(json.loads(best["tickers"] or "[]")) | set(it["tickers"])
        conn.execute("UPDATE news_stories SET ts=MIN(ts, ?), n_sources=?, sources=?, kinds=?, tickers=?, updated=? WHERE id=?",
                     (it["ts"], len(srcs), json.dumps(sorted(srcs)), json.dumps(sorted(kinds)), json.dumps(sorted(tick)), int(time.time()), best["id"]))
        return best["id"]
    cur = conn.execute("INSERT INTO news_stories (ts, title, words, n_sources, sources, kinds, tickers, updated) VALUES (?,?,?,?,?,?,?,?)",
                       (it["ts"], it["text"][:300], json.dumps(sorted(wset)), 1, json.dumps([it["source"]]), json.dumps([it["kind"]]), json.dumps(it["tickers"]), int(time.time())))
    return cur.lastrowid


def store(items: list[dict[str, Any]]) -> list[int]:
    """Insert new items, group them into stories; returns the ids of stories that were created or changed."""
    ensure()
    touched: list[int] = []
    now = int(time.time())
    with db.get_conn() as conn:
        for it in sorted(items, key=lambda x: x["ts"]):
            if it["ts"] < now - 3 * 86400:
                continue
            h = _hash(it)
            if conn.execute("SELECT 1 FROM news_items WHERE hash=?", (h,)).fetchone():
                continue
            it["tickers"] = sorted(set(TICKER.findall(it["text"])))
            sid = _attach(conn, it, words(it["text"]))
            conn.execute("INSERT INTO news_items (ts, source, kind, text, link, tickers, hash, story_id, seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (it["ts"], it["source"], it["kind"], it["text"], it.get("link", ""), json.dumps(it["tickers"]), h, sid, now))
            touched.append(sid)
        conn.execute("DELETE FROM news_items WHERE ts < ?", (now - 90 * 86400,))
    return sorted(set(touched))


def poll_fast() -> dict[str, Any]:
    """Telegram channels + Truth Social (every ~60 s)."""
    jobs = [(c, k) for c, k in channels().items()]
    items: list[dict[str, Any]] = []
    errors = 0
    with cf.ThreadPoolExecutor(8) as ex:
        futs = [ex.submit(telegram, c, k) for c, k in jobs] + [ex.submit(truth)]
        for f in futs:
            try:
                items += f.result()
            except Exception:  # noqa: BLE001 - one source failing never stops the rest
                errors += 1
    return {"items": len(items), "errors": errors, "stories": store(items)}


def poll_slow() -> dict[str, Any]:
    try:
        items = rss()
    except Exception:  # noqa: BLE001
        logger.exception("rss poll failed")
        items = []
    return {"items": len(items), "stories": store(items)}


def backfill_telegram(channel: str, kind: str, pages: int = 40, pause: float = 1.0, max_age_days: float = 58) -> int:
    """Walk a channel back `pages` x ~20 posts (for training the impact engine). Stores items regardless of age (no 3-day cut)."""
    ensure()
    before, n = None, 0
    for _ in range(pages):
        try:
            posts = telegram(channel, kind, before)
        except Exception:  # noqa: BLE001
            break
        if not posts:
            break
        with db.get_conn() as conn:
            for it in posts:
                h = _hash(it)
                if conn.execute("SELECT 1 FROM news_items WHERE hash=?", (h,)).fetchone():
                    continue
                it["tickers"] = sorted(set(TICKER.findall(it["text"])))
                conn.execute("INSERT INTO news_items (ts, source, kind, text, link, tickers, hash, story_id, seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
                             (it["ts"], it["source"], it["kind"], it["text"], it["link"], json.dumps(it["tickers"]), h, None, int(time.time())))
                n += 1
        before = min(p["msg_id"] for p in posts)
        if min(p["ts"] for p in posts) < time.time() - max_age_days * 86400:
            break  # older than the 5-minute price history can measure
        time.sleep(pause)
    return n


def build_stories(batch: int = 5000) -> int:
    """Group backfilled items (story_id NULL) into stories, oldest first."""
    ensure()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, ts, source, kind, text, tickers FROM news_items WHERE story_id IS NULL ORDER BY ts LIMIT ?", (batch,)).fetchall()]
        for it in rows:
            it["tickers"] = json.loads(it["tickers"] or "[]")
            sid = _attach(conn, it, words(it["text"]))
            conn.execute("UPDATE news_items SET story_id=? WHERE id=?", (sid, it["id"]))
    return len(rows)


def recent_stories(hours: float = 24, limit: int = 200) -> list[dict[str, Any]]:
    ensure()
    with db.read_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM news_stories WHERE ts > ? ORDER BY ts DESC LIMIT ?", (int(time.time() - hours * 3600), limit)).fetchall()]
    for r in rows:
        for k in ("sources", "kinds", "tickers"):
            r[k] = json.loads(r[k] or "[]")
        r["measured"] = json.loads(r["measured"]) if r.get("measured") else None
        r.pop("words", None)
    return rows
