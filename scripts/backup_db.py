"""Safe online backup of data_cache/app.db (works while the server is running).

Uses SQLite's backup API, which copies a consistent snapshot even mid-write. Never
copy the live .db file directly - a plain file copy can capture a torn page.

Usage:
    .venv\\Scripts\\python.exe scripts\\backup_db.py            # label defaults to "manual"
    .venv\\Scripts\\python.exe scripts\\backup_db.py pre-phase3
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "manual"
    backups = db.DB_PATH.parent / "backups"
    backups.mkdir(exist_ok=True)
    dest = backups / f"app-{label}-{dt.datetime.now():%Y%m%d-%H%M%S}.db"

    src = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    check = sqlite3.connect(dest)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    print(f"backup: {dest} ({dest.stat().st_size / 1e6:.1f} MB) integrity_check={ok}")
    if ok != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
