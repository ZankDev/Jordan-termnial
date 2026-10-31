"""LEARNING JOURNAL API (prefix /api/journal): long rich-text entries (HTML from the editor in web/journal.js) and the images in them
(screenshots, drawings, charts), stored in SQLite + data_cache/journal/. Personal notes only; nothing here touches trading."""
from __future__ import annotations

import base64
import os
import re
import secrets
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse

from app import db

router = APIRouter(prefix="/api/journal", tags=["journal"])
FILES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data_cache", "journal")
os.makedirs(FILES, exist_ok=True)
MAX_HTML = 25_000_000      # a long entry with many inline objects; images themselves live as files
MAX_FILE = 15_000_000
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/svg+xml": "svg"}
SCHEMA = """CREATE TABLE IF NOT EXISTS learning_journal (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT 'Untitled',
  html TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', pinned INTEGER NOT NULL DEFAULT 0, words INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, updated REAL NOT NULL)"""
_ready = False


def _ensure() -> None:
    global _ready
    if not _ready:
        with db.get_conn() as c:
            c.execute(SCHEMA)
        _ready = True


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


@router.get("/research/bigmoves.pdf")
def bigmoves_pdf():
    """BIG MOVERS research report (built by app/notify/report_bigmoves.py)."""
    from app.intel.bigmoves import OUT

    path = os.path.join(OUT, "BigMovers_research.pdf")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="research not built yet")
    return FileResponse(path, media_type="application/pdf", filename="BigMovers_research.pdf")


@router.get("/iceberg")
def iceberg_state() -> dict[str, Any]:
    """ICEBERG study course progress (topics passed, attempts, quiz history)."""
    from app.learn import iceberg

    return iceberg.api_state()


@router.get("/docs")
def list_docs(q: str = "", tag: str = ""):
    _ensure()
    with db.read_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT id, title, tags, pinned, words, created, updated, substr(html, 1, 4000) AS head FROM learning_journal ORDER BY pinned DESC, updated DESC")]
    out = []
    ql = q.strip().lower()
    for r in rows:
        if tag and tag.lower() not in [t.strip().lower() for t in (r["tags"] or "").split(",")]:
            continue
        snip = _text(r.pop("head"))
        if ql and ql not in (r["title"] + " " + snip + " " + r["tags"]).lower():
            # search the full text only when the head did not match
            with db.read_conn() as c:
                full = c.execute("SELECT html FROM learning_journal WHERE id=?", (r["id"],)).fetchone()[0]
            if ql not in _text(full).lower():
                continue
        r["snippet"] = snip[:160]
        out.append(r)
    return {"docs": out}


@router.post("/docs")
def create_doc(body: dict[str, Any] = Body(default={})):
    _ensure()
    now = time.time()
    with db.get_conn() as c:
        cur = c.execute("INSERT INTO learning_journal (title, html, tags, created, updated) VALUES (?,?,?,?,?)",
                        (str(body.get("title") or "Untitled")[:200], str(body.get("html") or "")[:MAX_HTML], str(body.get("tags") or "")[:300], now, now))
        return {"id": cur.lastrowid}


@router.get("/docs/{doc_id}")
def get_doc(doc_id: int):
    _ensure()
    with db.read_conn() as c:
        r = c.execute("SELECT * FROM learning_journal WHERE id=?", (doc_id,)).fetchone()
    if not r:
        raise HTTPException(404, "entry not found")
    return dict(r)


@router.put("/docs/{doc_id}")
def save_doc(doc_id: int, body: dict[str, Any] = Body(...)):
    _ensure()
    html = str(body.get("html") or "")
    if len(html) > MAX_HTML:
        raise HTTPException(413, "entry too large: move big images to files (paste/upload does that automatically)")
    with db.get_conn() as c:
        n = c.execute("UPDATE learning_journal SET title=?, html=?, tags=?, pinned=?, words=?, updated=? WHERE id=?",
                      (str(body.get("title") or "Untitled")[:200], html, str(body.get("tags") or "")[:300], 1 if body.get("pinned") else 0,
                       len(_text(html).split()), time.time(), doc_id)).rowcount
    if not n:
        raise HTTPException(404, "entry not found")
    return {"ok": True, "updated": time.time()}


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: int):
    _ensure()
    with db.get_conn() as c:
        c.execute("DELETE FROM learning_journal WHERE id=?", (doc_id,))
    return {"ok": True}


@router.post("/upload")
async def upload(request: Request):
    """Body: {data: 'data:image/png;base64,...'} (paste, drop, drawings, charts) -> {url}."""
    body = await request.json()
    m = re.match(r"^data:(image/[a-z+.-]+);base64,(.+)$", str(body.get("data") or ""), re.S)
    if not m or m.group(1) not in EXT:
        raise HTTPException(400, "send an image as a data URL (png, jpg, webp, gif, svg)")
    raw = base64.b64decode(m.group(2))
    if len(raw) > MAX_FILE:
        raise HTTPException(413, "image larger than 15 MB")
    name = f"{time.strftime('%Y%m%d')}_{secrets.token_hex(6)}.{EXT[m.group(1)]}"
    with open(os.path.join(FILES, name), "wb") as f:
        f.write(raw)
    return {"url": f"/api/journal/file/{name}"}


@router.get("/file/{name}")
def get_file(name: str):
    if not re.fullmatch(r"[0-9]{8}_[0-9a-f]{12}\.(png|jpg|webp|gif|svg)", name):
        raise HTTPException(404, "not found")
    p = os.path.join(FILES, name)
    if not os.path.exists(p):
        raise HTTPException(404, "not found")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=31536000, immutable"})
