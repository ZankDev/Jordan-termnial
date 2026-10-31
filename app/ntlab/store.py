"""NT LAB storage: every run, batch and evaluated candidate (spec + metrics) in SQLite."""
from __future__ import annotations

import json
import time
from typing import Any

from app import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS ntlab_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, updated REAL, provider TEXT, instrument TEXT, start TEXT, end TEXT,
    holdout_from TEXT, status TEXT, generation INTEGER DEFAULT 0, cfg TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS ntlab_batches (
    batch TEXT PRIMARY KEY, run_id INTEGER, generation INTEGER, n INTEGER, provider TEXT, status TEXT, created REAL, done REAL
);
CREATE TABLE IF NOT EXISTS ntlab_candidates (
    run_id INTEGER, spec_id TEXT, generation INTEGER, batch TEXT, family TEXT, role TEXT, spec TEXT, metrics TEXT, holdout TEXT,
    score REAL, stability REAL, champion INTEGER DEFAULT 0, why_not TEXT, created REAL,
    PRIMARY KEY (run_id, spec_id)
);
CREATE INDEX IF NOT EXISTS ix_ntlab_cand_score ON ntlab_candidates (run_id, score);
"""
_ready = False


def ensure() -> None:
    global _ready
    if not _ready:
        with db.get_conn() as conn:
            conn.executescript(SCHEMA)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(ntlab_candidates)").fetchall()}
            if "dsr" not in cols:
                conn.execute("ALTER TABLE ntlab_candidates ADD COLUMN dsr REAL")
            if "ml" not in cols:
                conn.execute("ALTER TABLE ntlab_candidates ADD COLUMN ml TEXT")  # meta-labeling result (JSON)
        _ready = True


def new_run(provider: str, instrument: str, start: str, end: str, holdout_from: str | None, cfg: dict[str, Any]) -> int:
    ensure()
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO ntlab_runs (created, updated, provider, instrument, start, end, holdout_from, status, cfg) VALUES (?,?,?,?,?,?,?,?,?)",
                           (time.time(), time.time(), provider, instrument, start, end, holdout_from, "running", json.dumps(cfg)))
        return int(cur.lastrowid)


def update_run(run_id: int, **kw: Any) -> None:
    ensure()
    sets = ", ".join(f"{k} = ?" for k in kw) + ", updated = ?"
    with db.get_conn() as conn:
        conn.execute(f"UPDATE ntlab_runs SET {sets} WHERE id = ?", (*kw.values(), time.time(), run_id))


def save_batch(batch: str, run_id: int, gen: int, n: int, provider: str, status: str) -> None:
    ensure()
    with db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO ntlab_batches (batch, run_id, generation, n, provider, status, created, done) VALUES (?,?,?,?,?,?,?,?)",
                     (batch, run_id, gen, n, provider, status, time.time(), time.time() if status == "done" else None))


def save_candidate(run_id: int, gen: int, batch: str, spec: dict[str, Any], role: str, metrics: dict[str, Any], holdout: dict[str, Any] | None) -> None:
    ensure()
    with db.get_conn() as conn:
        conn.execute("""INSERT OR REPLACE INTO ntlab_candidates (run_id, spec_id, generation, batch, family, role, spec, metrics, holdout, score, stability, champion, why_not, created)
                        VALUES (?,?,?,?,?,?,?,?,?,?,NULL,0,NULL,?)""",
                     (run_id, spec["id"], gen, batch, spec["family"], role, json.dumps(spec), json.dumps(metrics), json.dumps(holdout) if holdout else None, metrics["score"], time.time()))


def set_verdict(run_id: int, spec_id: str, stability: float | None, champion: bool, why_not: list[str], dsr: float | None = None,
                ml: dict[str, Any] | None = None) -> None:
    ensure()
    with db.get_conn() as conn:
        conn.execute("UPDATE ntlab_candidates SET stability = ?, champion = ?, why_not = ?, dsr = ?, ml = ? WHERE run_id = ? AND spec_id = ?",
                     (stability, 1 if champion else 0, json.dumps(why_not), dsr, json.dumps(ml, default=str) if ml else None, run_id, spec_id))


def set_ml_champion(run_id: int, spec_id: str) -> None:
    """champion = 2: the rules alone did not pass, the rules + their walk-forward LightGBM filter did."""
    ensure()
    with db.get_conn() as conn:
        conn.execute("UPDATE ntlab_candidates SET champion = 2 WHERE run_id = ? AND spec_id = ?", (run_id, spec_id))


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    ensure()
    with db.read_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def runs(n: int = 20) -> list[dict[str, Any]]:
    out = _rows("SELECT * FROM ntlab_runs ORDER BY id DESC LIMIT ?", (n,))
    for r in out:
        r["cfg"] = json.loads(r["cfg"] or "{}")
        r["candidates"] = _rows("SELECT COUNT(*) AS n, SUM(champion) AS champs FROM ntlab_candidates WHERE run_id = ?", (r["id"],))[0]
    return out


def leaderboard(run_id: int | None = None, n: int = 30, champions_only: bool = False) -> list[dict[str, Any]]:
    where = ("WHERE run_id = ?" if run_id else "WHERE 1=1") + (" AND champion = 1" if champions_only else "")
    rows = _rows(f"SELECT * FROM ntlab_candidates {where} ORDER BY champion > 0 DESC, score DESC LIMIT ?", ((run_id, n) if run_id else (n,)))
    for r in rows:
        r["spec"] = json.loads(r["spec"]); r["metrics"] = json.loads(r["metrics"]); r["holdout"] = json.loads(r["holdout"]) if r["holdout"] else None
        r["why_not"] = json.loads(r["why_not"]) if r["why_not"] else None
        r["ml"] = json.loads(r["ml"]) if r.get("ml") else None
        r["metrics"].pop("equity", None)
    return rows


def candidate(run_id: int, spec_id: str) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM ntlab_candidates WHERE run_id = ? AND spec_id = ?", (run_id, spec_id))
    if not rows:
        return None
    r = rows[0]
    r["spec"] = json.loads(r["spec"]); r["metrics"] = json.loads(r["metrics"]); r["holdout"] = json.loads(r["holdout"]) if r["holdout"] else None
    r["why_not"] = json.loads(r["why_not"]) if r["why_not"] else None
    r["ml"] = json.loads(r["ml"]) if r.get("ml") else None
    return r


def seen(run_id: int) -> set[str]:
    return {r["spec_id"] for r in _rows("SELECT spec_id FROM ntlab_candidates WHERE run_id = ?", (run_id,))}
