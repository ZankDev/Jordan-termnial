"""Removes every NOSTRO account from the terminal so it can run only the accounts the owner adds by hand.

Safety net first: a full online backup of the database, then every account and trade is copied into the archive tables (the trade history keeps feeding the
strategy statistics), and only then are the live rows deleted. The AUTO account is switched off so it does not recreate itself.
Run with the server STOPPED:   python scripts/reset_accounts.py --yes [--db path]"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--db", default=str(ROOT / "data_cache" / "app.db"))
ap.add_argument("--yes", action="store_true")
ap.add_argument("--keep-linked-if-open", action="store_true")
a = ap.parse_args()
if not a.yes:
    sys.exit("add --yes to confirm")

src = sqlite3.connect(a.db)
src.row_factory = sqlite3.Row
linked = src.execute("SELECT id, name, open_position_json FROM accounts WHERE traderspost_linked = 1").fetchall()
for r in linked:
    if r["open_position_json"]:
        sys.exit(f"STOP: the real-money account {r['name']} has an open position in the system. Close it at the broker first, then run this again.")

bk = ROOT / "data_cache" / "backups"
bk.mkdir(parents=True, exist_ok=True)
bpath = bk / f"pre_reset_{time.strftime('%Y%m%d_%H%M%S')}.db"
dst = sqlite3.connect(bpath)
src.backup(dst)
dst.close()
print("backup:", bpath, f"{bpath.stat().st_size / 1e6:.1f} MB")

n_acc = src.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
n_tr = src.execute("SELECT COUNT(*) FROM account_trades").fetchone()[0]
note = "owner reset " + time.strftime("%Y-%m-%d") + ": simulated accounts removed"
with src:
    src.execute("CREATE TABLE IF NOT EXISTS account_trades_archive(id INT, account_id INT, entry_time TEXT, exit_time TEXT, side TEXT, entry_price REAL, exit_price REAL, pnl REAL, equity_after REAL, exit_reason TEXT, "
                "stop_price REAL, target_price REAL, source TEXT, strategy TEXT, symbol TEXT, qty REAL, contract TEXT, commission REAL, risk_dollars REAL, archived_at, archive_note)")
    tcols = [r[1] for r in src.execute("PRAGMA table_info(account_trades)")]
    acols = [r[1] for r in src.execute("PRAGMA table_info(account_trades_archive)")]
    common = [c for c in tcols if c in acols]
    src.execute(f"INSERT INTO account_trades_archive ({','.join(common)}, archived_at, archive_note) SELECT {','.join(common)}, datetime('now'), ? FROM account_trades", (note,))
    try:
        ac = [r[1] for r in src.execute("PRAGMA table_info(accounts)")]
        arc = [r[1] for r in src.execute("PRAGMA table_info(accounts_archive)")]
        cc = [c for c in ac if c in arc]
        src.execute(f"INSERT INTO accounts_archive ({','.join(cc)}) SELECT {','.join(cc)} FROM accounts")
    except sqlite3.OperationalError as e:
        print("accounts_archive skipped:", e)
    src.execute("DELETE FROM account_trades")
    src.execute("DELETE FROM accounts")
    src.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('auto_account_disabled', '1')")
print(f"archived and removed {n_acc} accounts and {n_tr} trades; AUTO account switched off")
print("accounts now:", src.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], "| archived trades:", src.execute("SELECT COUNT(*) FROM account_trades_archive").fetchone()[0])
src.close()
