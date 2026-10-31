"""SPIDER memory: everything the spider sees and reads is kept in the database, so nothing is lost when a headline drops
off the wire and the brain can answer from what it read last week, not only from what is on screen now.

Tables
  spider_news      every headline from every channel (first seen, the mark it got, why, assets)
  spider_events    the critical / watch events it saw
  spider_articles  full texts it fetched (paragraphs as JSON)
  spider_reads     its read of each article (summary, key points, impact, assets, marked sentences)
  spider_reports   full stock reports (one row per report, newest wins)
  spider_qa        questions you asked and its answers

The background loop (`spider_loop`, every 2.5 min, in the server process — it is network-bound, not CPU) stores the wire
and the events, then reads ONE more article in full: critical first, then important, then company results (8-K item 2.02
and press releases). One at a time keeps a free language-model tier inside its per-minute token limit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from app import db

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS spider_news (
    link TEXT PRIMARY KEY, title TEXT, source TEXT, tag TEXT, ts REAL, descr TEXT, first_seen REAL, last_seen REAL,
    level TEXT, score REAL, why TEXT, assets TEXT, brain INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_spider_news_ts ON spider_news (ts);
CREATE INDEX IF NOT EXISTS ix_spider_news_level ON spider_news (level, ts);
CREATE TABLE IF NOT EXISTS spider_events (
    key TEXT PRIMARY KEY, title TEXT, severity INTEGER, kind TEXT, ts REAL, first_seen REAL
);
CREATE TABLE IF NOT EXISTS spider_articles (
    url TEXT PRIMARY KEY, title TEXT, source TEXT, author TEXT, published TEXT, words INTEGER, partial INTEGER, error TEXT,
    paragraphs TEXT, fetched_at REAL
);
CREATE TABLE IF NOT EXISTS spider_reads (
    url TEXT PRIMARY KEY, title TEXT, source TEXT, level TEXT, summary TEXT, points TEXT, impact TEXT, assets TEXT,
    highlights TEXT, brain INTEGER, read_at REAL
);
CREATE INDEX IF NOT EXISTS ix_spider_reads_at ON spider_reads (read_at);
CREATE TABLE IF NOT EXISTS spider_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, created_at REAL, stance TEXT, report TEXT
);
CREATE INDEX IF NOT EXISTS ix_spider_reports_sym ON spider_reports (symbol, created_at);
CREATE TABLE IF NOT EXISTS spider_qa (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, q TEXT, url TEXT, answer TEXT, refs TEXT
);
"""
_ready = False


def ensure() -> None:
    global _ready
    if _ready:
        return
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)
    _ready = True


# ------------------------------------------------------------------ writes
def save_news(items: list[dict[str, Any]]) -> None:
    ensure()
    now = time.time()
    rows = [(i["link"], i["title"], i.get("source"), i.get("tag"), i.get("ts"), i.get("desc"), now, now, i.get("level") or "", i.get("score") or 0,
             i.get("why"), json.dumps(i.get("assets") or []), 1 if i.get("brain") else 0) for i in items if i.get("link")]
    with db.get_conn() as conn:
        conn.executemany("""INSERT INTO spider_news (link, title, source, tag, ts, descr, first_seen, last_seen, level, score, why, assets, brain)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(link) DO UPDATE SET last_seen=excluded.last_seen, level=excluded.level, score=excluded.score,
                              why=COALESCE(excluded.why, spider_news.why), assets=excluded.assets, brain=MAX(spider_news.brain, excluded.brain)""", rows)


def save_events(events: list[dict[str, Any]]) -> None:
    ensure()
    now = time.time()
    rows = [(f"{e.get('title')}|{int((e.get('ts') or 0) // 3600)}", e.get("title"), e.get("severity"), e.get("kind"), e.get("ts"), now) for e in events if e.get("title")]
    with db.get_conn() as conn:
        conn.executemany("INSERT OR IGNORE INTO spider_events (key, title, severity, kind, ts, first_seen) VALUES (?,?,?,?,?,?)", rows)


def save_article(a: dict[str, Any]) -> None:
    ensure()
    with db.get_conn() as conn:
        conn.execute("""INSERT OR REPLACE INTO spider_articles (url, title, source, author, published, words, partial, error, paragraphs, fetched_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (a["url"], a.get("title"), a.get("source"), a.get("author"), a.get("published"), a.get("words"), 1 if a.get("partial") else 0,
                      a.get("error"), json.dumps(a.get("paragraphs") or []), time.time()))


def save_read(url: str, a: dict[str, Any], r: dict[str, Any]) -> None:
    ensure()
    with db.get_conn() as conn:
        conn.execute("""INSERT OR REPLACE INTO spider_reads (url, title, source, level, summary, points, impact, assets, highlights, brain, read_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (url, a.get("title"), a.get("source"), r.get("level"), r.get("summary"), json.dumps(r.get("points") or []), r.get("impact"),
                      json.dumps(r.get("assets") or []), json.dumps(r.get("highlights") or []), 1 if r.get("brain") else 0, time.time()))


def save_report(symbol: str, rep: dict[str, Any]) -> None:
    ensure()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO spider_reports (symbol, created_at, stance, report) VALUES (?,?,?,?)",
                     (symbol.upper(), time.time(), (rep.get("view") or {}).get("stance"), json.dumps(rep)))


def save_qa(q: str, url: str | None, answer: str, refs: list[dict[str, Any]]) -> None:
    ensure()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO spider_qa (ts, q, url, answer, refs) VALUES (?,?,?,?,?)",
                     (time.time(), q, url, answer, json.dumps([{"title": r.get("title"), "link": r.get("link"), "source": r.get("source")} for r in refs])))


# ------------------------------------------------------------------ reads
def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    ensure()
    with db.read_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_read(url: str, max_age: float = 86400 * 7) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM spider_reads WHERE url = ? AND read_at > ?", (url, time.time() - max_age))
    if not rows:
        return None
    r = rows[0]
    return {"summary": r["summary"], "points": json.loads(r["points"] or "[]"), "impact": r["impact"], "assets": json.loads(r["assets"] or "[]"),
            "level": r["level"], "highlights": json.loads(r["highlights"] or "[]"), "brain": bool(r["brain"]), "read_at": r["read_at"]}


def latest_report(symbol: str, max_age: float) -> dict[str, Any] | None:
    rows = _rows("SELECT report, created_at FROM spider_reports WHERE symbol = ? AND created_at > ? ORDER BY created_at DESC LIMIT 1",
                 (symbol.upper(), time.time() - max_age))
    return {**json.loads(rows[0]["report"]), "created_at": rows[0]["created_at"]} if rows else None


def report_history(symbol: str, n: int = 10) -> list[dict[str, Any]]:
    return _rows("SELECT id, created_at, stance FROM spider_reports WHERE symbol = ? ORDER BY created_at DESC LIMIT ?", (symbol.upper(), n))


def recall(query: str, days: float = 14, n: int = 10) -> list[dict[str, Any]]:
    """What the spider has already READ that matches the query words (title, summary, impact, assets): its long memory."""
    words = [w for w in re.findall(r"[a-z0-9$.]{3,}", query.lower()) if w not in _STOP][:8]
    if not words:
        return []
    since = time.time() - days * 86400
    rows = _rows("SELECT url, title, source, level, summary, impact, assets, read_at FROM spider_reads WHERE read_at > ? ORDER BY read_at DESC LIMIT 1500", (since,))
    scored = []
    for r in rows:
        hay = f"{r['title']} {r['summary']} {r['impact']} {r['assets']}".lower()
        k = sum(1 for w in words if w in hay)
        if k:
            scored.append((k + (0.5 if r["level"] == "critical" else 0), r))
    scored.sort(key=lambda x: (-x[0], -x[1]["read_at"]))
    return [r for _k, r in scored[:n]]


def search_news(query: str, days: float = 30, n: int = 40) -> list[dict[str, Any]]:
    words = [w for w in re.findall(r"[a-z0-9$.]{2,}", query.lower()) if w not in _STOP][:6]
    if not words:
        return []
    like = " AND ".join("LOWER(title) LIKE ?" for _ in words)
    return _rows(f"SELECT link, title, source, tag, ts, level, why FROM spider_news WHERE ts > ? AND {like} ORDER BY ts DESC LIMIT ?",
                 (time.time() - days * 86400, *[f"%{w}%" for w in words], n))


def stats() -> dict[str, Any]:
    ensure()
    with db.read_conn() as conn:
        q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        day = time.time() - 86400
        return {"headlines": q("SELECT COUNT(*) FROM spider_news"), "headlines_24h": q(f"SELECT COUNT(*) FROM spider_news WHERE first_seen > {day}"),
                "marked": q("SELECT COUNT(*) FROM spider_news WHERE level != ''"), "events": q("SELECT COUNT(*) FROM spider_events"),
                "articles": q("SELECT COUNT(*) FROM spider_articles"), "reads": q("SELECT COUNT(*) FROM spider_reads"),
                "reads_24h": q(f"SELECT COUNT(*) FROM spider_reads WHERE read_at > {day}"), "reports": q("SELECT COUNT(*) FROM spider_reports"),
                "questions": q("SELECT COUNT(*) FROM spider_qa")}


def recent_reads(n: int = 30) -> list[dict[str, Any]]:
    rows = _rows("SELECT url, title, source, level, summary, impact, assets, read_at FROM spider_reads ORDER BY read_at DESC LIMIT ?", (n,))
    for r in rows:
        r["assets"] = json.loads(r["assets"] or "[]")
    return rows


_STOP = set("the and for with that this what about from into have has had was were are will would could should can how why who when "
            "news today stock stocks market markets tell me give any did does do you your".split())


# ------------------------------------------------------------------ the background reader
def _queue(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What to read next: critical, then important, then company results / press releases; newest first; not read yet."""
    done = {r["url"] for r in _rows("SELECT url FROM spider_reads WHERE read_at > ?", (time.time() - 86400 * 3,))}
    def rank(i: dict[str, Any]) -> tuple:
        lvl = 0 if i.get("level") == "critical" else 1 if i.get("level") == "important" else 2
        results = 0 if ("RESULTS" in i["title"] or i.get("tag") == "EARN") else 1
        return (lvl, results, -(i.get("ts") or 0))
    cand = [i for i in items if i["link"] not in done and (i.get("level") or "RESULTS" in i["title"] or i.get("tag") in ("EARN", "CB"))
            and time.time() - (i.get("ts") or 0) < 86400 * 2]
    return sorted(cand, key=rank)


def cycle() -> dict[str, Any]:
    from app.intel import spider
    s = spider.scan()
    save_news(s["items"])
    save_events(s["events"])
    q = _queue(s["items"])
    read = None
    if q:
        it = q[0]
        try:
            spider.read_article(it["link"], force=True)
            read = it["title"]
        except Exception as e:  # noqa: BLE001
            logger.info("spider: couldn't read %s (%s)", it["link"], type(e).__name__)
    return {"stored": len(s["items"]), "queue": len(q), "read": read}


async def spider_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            out = await asyncio.to_thread(cycle)
            if out.get("read"):
                logger.info("spider read: %s (%d waiting)", out["read"][:80], out["queue"] - 1)
        except Exception:  # noqa: BLE001
            logger.exception("spider cycle failed")
        await asyncio.sleep(150)
