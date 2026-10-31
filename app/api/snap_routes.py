"""Chart snapshots: a screenshot of the GP chart (with the user's drawings) saved against a coin or stock, plus the drawings
themselves so they can be restored later. Images live in data_cache/chart_snaps/, metadata in the chart_snaps table."""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/api/snaps", tags=["snaps"])
SNAP_DIR = Path(__file__).resolve().parents[2] / "data_cache" / "chart_snaps"
SCHEMA = """
CREATE TABLE IF NOT EXISTS chart_snaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    ts REAL NOT NULL,
    timeframe TEXT,
    price REAL,
    note TEXT,
    drawings TEXT,
    image TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chart_snaps_symbol ON chart_snaps(symbol, ts);
"""


def _ensure() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


class SnapIn(BaseModel):
    symbol: str
    data_url: str
    timeframe: str | None = None
    price: float | None = None
    note: str = ""
    drawings: list[dict[str, Any]] = []


class NoteIn(BaseModel):
    note: str = ""


def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["drawings"] = json.loads(d["drawings"] or "[]")
    d["url"] = f"/api/snaps/{d['id']}/image"
    return d


class DrawingsIn(BaseModel):
    symbol: str
    drawings: list[dict[str, Any]] = []


@router.get("/drawings")
def get_drawings(symbol: str):
    """The drawings on a security's chart, kept in the database (not the browser) so they survive the exe window, another
    browser, a cleared cache or a different address. Times are unix seconds, so they sit on the same candles at every timeframe."""
    raw = db.meta_get(f"gp_draw:{symbol.upper()}")
    try:
        return {"drawings": json.loads(raw) if raw else None}
    except ValueError:
        return {"drawings": None}


@router.put("/drawings")
def put_drawings(d: DrawingsIn):
    if len(d.drawings) > 2000:
        raise HTTPException(status_code=422, detail="Too many drawings on one chart.")
    blob = json.dumps(d.drawings, separators=(",", ":"))
    if len(blob) > 2_000_000:
        raise HTTPException(status_code=422, detail="Drawings are too large to save.")
    db.meta_set(f"gp_draw:{d.symbol.upper()}", blob)
    return {"ok": True, "n": len(d.drawings)}


@router.get("")
def list_snaps(symbol: str, limit: int = 60):
    _ensure()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM chart_snaps WHERE symbol = ? ORDER BY ts DESC LIMIT ?", (symbol.upper(), min(limit, 200))).fetchall()
    return {"snaps": [_row(r) for r in rows]}


@router.get("/counts")
def counts():
    _ensure()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT symbol, COUNT(*) n FROM chart_snaps GROUP BY symbol").fetchall()
    return {"counts": {r["symbol"]: r["n"] for r in rows}}


@router.post("")
def create_snap(s: SnapIn):
    _ensure()
    m = re.match(r"^data:image/(png|jpeg|webp);base64,(.+)$", s.data_url, re.S)
    if not m:
        raise HTTPException(status_code=400, detail="expected a PNG/JPEG/WebP data URL")
    raw = base64.b64decode(m.group(2))
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image too large (8 MB max)")
    sym = re.sub(r"[^A-Z0-9.\-=^]", "", s.symbol.upper())[:20]
    if not sym:
        raise HTTPException(status_code=400, detail="bad symbol")
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{sym.replace('^', '_').replace('=', '_')}_{int(time.time() * 1000)}.{'jpg' if m.group(1) == 'jpeg' else m.group(1)}"
    (SNAP_DIR / name).write_bytes(raw)
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO chart_snaps (symbol, ts, timeframe, price, note, drawings, image) VALUES (?,?,?,?,?,?,?)",
                           (sym, time.time(), s.timeframe, s.price, s.note[:500], json.dumps(s.drawings[:400]), name))
        return {"id": cur.lastrowid}


@router.get("/{snap_id}/image")
def snap_image(snap_id: int):
    _ensure()
    with db.get_conn() as conn:
        r = conn.execute("SELECT image FROM chart_snaps WHERE id = ?", (snap_id,)).fetchone()
    if not r or not (SNAP_DIR / r["image"]).exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(SNAP_DIR / r["image"])


@router.put("/{snap_id}")
def update_note(snap_id: int, n: NoteIn):
    _ensure()
    with db.get_conn() as conn:
        conn.execute("UPDATE chart_snaps SET note = ? WHERE id = ?", (n.note[:500], snap_id))
    return {"ok": True}


@router.delete("/{snap_id}")
def delete_snap(snap_id: int):
    _ensure()
    with db.get_conn() as conn:
        r = conn.execute("SELECT image FROM chart_snaps WHERE id = ?", (snap_id,)).fetchone()
        conn.execute("DELETE FROM chart_snaps WHERE id = ?", (snap_id,))
    if r:
        (SNAP_DIR / r["image"]).unlink(missing_ok=True)
    return {"ok": True}
