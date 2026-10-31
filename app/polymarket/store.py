"""Polymarket vertical's SQLite schema — opens the SAME app.db file as the rest of
the terminal (via app.db.get_conn()) but in its own tables, never touching the
futures accounts/champions/candidate_log tables (see the plan's decision #2: those
assume futures-shaped columns that don't map onto a $0-1 share price).

All tables for every phase are declared here up front (sessions/prices now;
trainer/accounts/copytrader tables land as their phases do) — mirrors app/db.py's
own single-init_db()-owns-everything style rather than scattering CREATE TABLE
across every module that uses one.
"""
from __future__ import annotations

import json
import time
from typing import Any

from app import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    series_slug TEXT NOT NULL,
    asset TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    up_token_id TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    strike_price REAL,
    final_spot REAL,
    sigma_per_min_pct REAL,        -- realized vol at session start, cached at backfill time (see below)
    n_ticks INTEGER,               -- CLOB price ticks stored for the session (replay needs >= 2)
    resolved_up INTEGER,           -- 1 = Up won, 0 = Down won, NULL = not yet known
    backfilled_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pm_sessions_series_end ON pm_sessions (series_slug, end_ts);
CREATE INDEX IF NOT EXISTS idx_pm_sessions_asset_end ON pm_sessions (asset, end_ts);

CREATE TABLE IF NOT EXISTS pm_session_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES pm_sessions (id),
    t INTEGER NOT NULL,
    up_price REAL NOT NULL,
    UNIQUE (session_id, t)
);
CREATE INDEX IF NOT EXISTS idx_pm_prices_session_t ON pm_session_prices (session_id, t);

-- Intra-session underlying-spot trace, captured ONCE at backfill time while
-- Binance's 1-second klines are still within its short retention window (see
-- spot.session_trace's doc comment) — re-fetching live on every backtest/trainer
-- run would be both slow (repeated Binance calls for the same historical session)
-- and non-reproducible (older sessions silently lose resolution as retention
-- passes). backtest.py reads this cache instead of calling spot.session_trace().
CREATE TABLE IF NOT EXISTS pm_session_spot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES pm_sessions (id),
    t INTEGER NOT NULL,
    spot_price REAL NOT NULL,
    UNIQUE (session_id, t)
);
CREATE INDEX IF NOT EXISTS idx_pm_spot_session_t ON pm_session_spot (session_id, t);

-- Compact per-session spot trace: zlib(uint16 second-offsets + float32 prices). The
-- row-per-second pm_session_spot table above cost ~300 rows a session, which does
-- not scale to a deep multi-week corpus; new backfills write only this blob (the old
-- table is still read as a fallback for sessions stored before it existed).
CREATE TABLE IF NOT EXISTS pm_session_spot_blob (
    session_id INTEGER PRIMARY KEY,
    t0 INTEGER NOT NULL,
    n INTEGER NOT NULL,
    data BLOB NOT NULL
);

-- Phase 4: trainer. Leader-tier only for now (see plan decision #4) — the `tier`
-- column is kept so a future 'confirmed' tier needs no migration.
CREATE TABLE IF NOT EXISTS pm_champion_slots (
    tier TEXT PRIMARY KEY,
    strategy TEXT NOT NULL,
    params_json TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    score REAL,
    score_epoch INTEGER,
    train_metrics_json TEXT,
    test_metrics_json TEXT,
    fold_scores_json TEXT,
    run_id INTEGER,
    version INTEGER,
    promoted_at TEXT,
    last_rescored_at TEXT,
    consecutive_fails INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pm_champion_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tier TEXT NOT NULL,
    strategy TEXT,
    params_json TEXT,
    score REAL,
    reason TEXT,
    version INTEGER
);

CREATE TABLE IF NOT EXISTS pm_promotion_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id INTEGER,
    tier TEXT NOT NULL,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT,
    strategy TEXT,
    params_hash TEXT,
    cand_score REAL,
    inc_score REAL,
    margin REAL,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_pm_promo_ts ON pm_promotion_attempts (ts);

CREATE TABLE IF NOT EXISTS pm_optimizer_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    strategy TEXT NOT NULL,
    n_iterations INTEGER NOT NULL,
    n_sessions INTEGER,
    result_json TEXT NOT NULL,
    top_score REAL
);

CREATE TABLE IF NOT EXISTS pm_candidate_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    run_id INTEGER,
    strategy TEXT NOT NULL,
    tier INTEGER NOT NULL,
    flags INTEGER NOT NULL DEFAULT 0,
    n INTEGER,
    wr_net REAL, rr_net REAL, exp_net REAL, cost_r REAL,
    score REAL,
    params_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_pm_cand_ts ON pm_candidate_log (ts);
CREATE INDEX IF NOT EXISTS idx_pm_cand_tier_ts ON pm_candidate_log (tier, ts);

CREATE TABLE IF NOT EXISTS pm_field_cycle_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    run_id INTEGER,
    n_total INTEGER, n_tier0 INTEGER, n_tier1 INTEGER, n_survivors INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pm_fcs_ts ON pm_field_cycle_stats (ts);

-- Phase 5: paper accounts.
CREATE TABLE IF NOT EXISTS polymarket_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    starting_balance REAL NOT NULL,
    balance REAL NOT NULL,
    strategy TEXT,
    strategy_params_json TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    open_position_json TEXT,
    last_run_at TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    archived_reason TEXT,
    archived_at TEXT,
    telegram_msg_id INTEGER
);

CREATE TABLE IF NOT EXISTS polymarket_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    session_slug TEXT NOT NULL,
    asset TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    shares REAL NOT NULL,
    pnl REAL NOT NULL,
    equity_after REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    strategy TEXT
);
CREATE INDEX IF NOT EXISTS idx_pm_trades_account ON polymarket_trades (account_id, id);

-- Phase 10: copytrader.
CREATE TABLE IF NOT EXISTS polymarket_copy_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    created_at TEXT NOT NULL,
    starting_balance REAL NOT NULL,
    balance REAL NOT NULL,
    stake_usd REAL NOT NULL DEFAULT 5,
    status TEXT NOT NULL DEFAULT 'idle',
    last_tx_hash TEXT,
    last_poll_at TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS polymarket_copy_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    copy_account_id INTEGER NOT NULL,
    tx_hash TEXT NOT NULL,
    market_slug TEXT,
    outcome TEXT,
    wallet_price REAL,
    shadow_price REAL,
    shadow_shares REAL,
    pnl REAL,
    status TEXT NOT NULL DEFAULT 'open',
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    UNIQUE (copy_account_id, tx_hash)
);
"""


REAL_SCHEMA = """
-- REAL-money trades on the user's connected Polymarket account (see real_trading.py).
-- One row per position: created 'open' when the buy fills, closed when sold / settled.
CREATE TABLE IF NOT EXISTS polymarket_real_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_slug TEXT NOT NULL,
    asset TEXT NOT NULL,
    side TEXT NOT NULL,
    token_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',      -- open | closed
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    start_ts INTEGER, end_ts INTEGER, strike REAL,
    entry_price REAL,                          -- average fill price actually paid
    shares REAL,
    cost REAL,                                 -- USDC actually spent
    exit_price REAL,
    proceeds REAL,                             -- USDC actually received (or estimated at settlement)
    pnl REAL,
    exit_reason TEXT,
    settled_estimate INTEGER NOT NULL DEFAULT 0, -- 1 = P&L assumed from the resolved outcome, not from a fill
    peak_pct REAL,
    flip_prob REAL,
    params_hash TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_pm_real_status ON polymarket_real_trades (status, id);
"""


def _ensure(conn, table: str, column: str, decl: str) -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


def init_db() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)
        _ensure(conn, "pm_sessions", "sigma_per_min_pct", "REAL")
        if _ensure(conn, "pm_sessions", "n_ticks", "INTEGER"):
            conn.execute(
                "UPDATE pm_sessions SET n_ticks = (SELECT COUNT(*) FROM pm_session_prices p WHERE p.session_id = pm_sessions.id) "
                "WHERE resolved_up IS NOT NULL"
            )
        # The trainer's candidate collector writes a sampling weight; the first
        # release of this table lacked it, which crashed every training cycle right
        # after the search finished (no candidates logged, no champion ever promoted).
        _ensure(conn, "pm_candidate_log", "w", "INTEGER NOT NULL DEFAULT 1")
        _ensure(conn, "pm_champion_slots", "holdout_json", "TEXT")
        _ensure(conn, "polymarket_accounts", "role", "TEXT")
        _ensure(conn, "polymarket_accounts", "is_auto", "INTEGER NOT NULL DEFAULT 0")
        _ensure(conn, "polymarket_accounts", "params_hash", "TEXT")
        _ensure(conn, "polymarket_accounts", "stake_usd", "REAL NOT NULL DEFAULT 5")
        _ensure(conn, "polymarket_trades", "flip_prob", "REAL")
        _ensure(conn, "polymarket_trades", "peak_pct", "REAL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pm_sessions_resolved ON pm_sessions (resolved_up, end_ts)")
        conn.executescript(REAL_SCHEMA)


# ---------------------------------------------------------------------------
# pm_sessions / pm_session_prices
# ---------------------------------------------------------------------------

def upsert_session(sess: dict[str, Any]) -> int:
    """Insert a session discovered from Gamma, or no-op if already known (slug is
    UNIQUE). Returns the row id either way."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO pm_sessions (slug, series_slug, asset, start_ts, end_ts, up_token_id, down_token_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(slug) DO NOTHING",
            (sess["slug"], sess["series_slug"], sess["asset"], sess["start_ts"], sess["end_ts"],
             sess["up_token_id"], sess["down_token_id"]),
        )
        row = conn.execute("SELECT id FROM pm_sessions WHERE slug = ?", (sess["slug"],)).fetchone()
        return row["id"]


def set_session_outcome(
    session_id: int, strike_price: float | None, final_spot: float | None, resolved_up: bool | None,
    sigma_per_min_pct: float | None = None, n_ticks: int | None = None,
) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE pm_sessions SET strike_price = ?, final_spot = ?, resolved_up = ?, sigma_per_min_pct = ?, "
            "n_ticks = ?, backfilled_at = datetime('now') WHERE id = ?",
            (strike_price, final_spot, None if resolved_up is None else int(resolved_up), sigma_per_min_pct,
             n_ticks, session_id),
        )


def get_session_by_slug(slug: str) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM pm_sessions WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None


def insert_prices(session_id: int, points: list[tuple[int, float]]) -> None:
    if not points:
        return
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO pm_session_prices (session_id, t, up_price) VALUES (?, ?, ?)",
            [(session_id, t, p) for t, p in points],
        )


def get_prices(session_id: int) -> list[tuple[int, float]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT t, up_price FROM pm_session_prices WHERE session_id = ? ORDER BY t ASC", (session_id,)
        ).fetchall()
        return [(r["t"], r["up_price"]) for r in rows]


def _bulk(table: str, value_col: str, session_ids: list[int]) -> dict[int, list[tuple[float, float]]]:
    """Chunked bulk read. The DB lock is taken per 200-id chunk and released between
    chunks, so a large training preload never starves the API and the live engines."""
    out: dict[int, list[tuple[float, float]]] = {i: [] for i in session_ids}
    for i in range(0, len(session_ids), 200):
        chunk = session_ids[i:i + 200]
        marks = ",".join("?" * len(chunk))
        with db.get_conn() as conn:
            rows = conn.execute(
                f"SELECT session_id, t, {value_col} AS v FROM {table} WHERE session_id IN ({marks}) ORDER BY session_id, t",
                chunk,
            ).fetchall()
        for r in rows:
            out[r["session_id"]].append((r["t"], r["v"]))
    return out


def get_prices_bulk(session_ids: list[int]) -> dict[int, list[tuple[float, float]]]:
    """Up-token price ticks for many sessions in a handful of queries (the trainer
    replays every session once per candidate — per-session queries were the
    dominant cost of a training cycle)."""
    return _bulk("pm_session_prices", "up_price", session_ids)


def _pack_trace(trace: list[tuple[float, float]]) -> tuple[int, int, bytes]:
    import zlib
    from array import array

    t0 = int(trace[0][0])
    offs = array("H", [min(65535, max(0, int(t) - t0)) for t, _ in trace])
    prices = array("f", [float(p) for _, p in trace])
    return t0, len(trace), zlib.compress(offs.tobytes() + prices.tobytes(), 6)


def _unpack_trace(t0: int, n: int, data: bytes) -> list[tuple[float, float]]:
    import zlib
    from array import array

    raw = zlib.decompress(data)
    offs = array("H")
    offs.frombytes(raw[: 2 * n])
    prices = array("f")
    prices.frombytes(raw[2 * n: 2 * n + 4 * n])
    return [(float(t0 + o), float(p)) for o, p in zip(offs, prices)]


def get_spot_bulk(session_ids: list[int]) -> dict[int, list[tuple[float, float]]]:
    """Spot traces for many sessions: the compact blob when present, else the legacy
    row-per-second table. Lock taken per chunk; decompression happens outside it."""
    out: dict[int, list[tuple[float, float]]] = {}
    for i in range(0, len(session_ids), 200):
        chunk = session_ids[i:i + 200]
        marks = ",".join("?" * len(chunk))
        with db.get_conn() as conn:
            rows = conn.execute(f"SELECT session_id, t0, n, data FROM pm_session_spot_blob WHERE session_id IN ({marks})", chunk).fetchall()
        for r in rows:
            out[r["session_id"]] = _unpack_trace(r["t0"], r["n"], r["data"])
    missing = [i for i in session_ids if i not in out]
    if missing:
        out.update(_bulk("pm_session_spot", "spot_price", missing))
    return out


def insert_spot_trace(session_id: int, points: list[tuple[float, float]]) -> None:
    if not points:
        return
    t0, n, data = _pack_trace(points)
    with db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO pm_session_spot_blob (session_id, t0, n, data) VALUES (?, ?, ?, ?)",
                     (session_id, t0, n, data))


def get_spot_trace(session_id: int) -> list[tuple[float, float]]:
    return get_spot_bulk([session_id]).get(session_id, [])


def list_resolved_sessions(asset: str | None = None, since_ts: int | None = None, limit: int = 5000,
                           latest: bool = False, stride: int = 1) -> list[dict[str, Any]]:
    """Resolved sessions oldest-first. latest=True takes the NEWEST `limit` sessions
    (still returned oldest-first) — what the trainer wants when the corpus is bigger
    than one search can chew. Sessions without >= 2 price ticks can't be replayed and
    are excluded."""
    clauses, args = ["resolved_up IS NOT NULL", "COALESCE(n_ticks, 2) >= 2"], []
    if asset:
        clauses.append("asset = ?")
        args.append(asset)
    if since_ts is not None:
        clauses.append("end_ts >= ?")
        args.append(since_ts)
    if stride > 1:
        clauses.append("id % ? = 0")
        args.append(stride)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM pm_sessions WHERE {' AND '.join(clauses)} ORDER BY end_ts {'DESC' if latest else 'ASC'} LIMIT ?",
            (*args, limit),
        ).fetchall()
        out = [dict(r) for r in rows]
        return out[::-1] if latest else out


def count_resolved_sessions() -> int:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM pm_sessions WHERE resolved_up IS NOT NULL AND COALESCE(n_ticks, 2) >= 2"
        ).fetchone()
        return row["c"]


# ---------------------------------------------------------------------------
# polymarket_accounts / polymarket_trades (Phase 5 — paper accounts)
# ---------------------------------------------------------------------------

def _parse_pm_account(row) -> dict[str, Any]:
    """open_position_json holds a LIST of open positions (an account can be in
    several sessions at once); a legacy single dict is upgraded on read. `open_position`
    (the first one) is kept for callers that only care whether anything is open."""
    d = dict(row)
    raw = d.pop("open_position_json", None)
    val = json.loads(raw) if raw else []
    positions = [val] if isinstance(val, dict) else list(val or [])
    d["open_positions"] = positions
    d["open_position"] = positions[0] if positions else None
    return d


def create_pm_account(name: str, starting_balance: float = 1_000.0, role: str | None = None, is_auto: bool = False,
                      stake_usd: float = 5.0) -> dict[str, Any]:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO polymarket_accounts (name, created_at, starting_balance, balance, status, role, is_auto, stake_usd) "
            "VALUES (?, datetime('now'), ?, ?, 'idle', ?, ?, ?)",
            (name, starting_balance, starting_balance, role, int(is_auto), stake_usd),
        )
        row = conn.execute("SELECT * FROM polymarket_accounts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _parse_pm_account(row)


def list_pm_accounts(include_archived: bool = False) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        q = "SELECT * FROM polymarket_accounts" if include_archived else "SELECT * FROM polymarket_accounts WHERE is_archived = 0"
        rows = conn.execute(q + " ORDER BY id DESC").fetchall()
        return [_parse_pm_account(r) for r in rows]


def get_pm_account(account_id: int) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM polymarket_accounts WHERE id = ?", (account_id,)).fetchone()
        return _parse_pm_account(row) if row else None


def list_live_pm_accounts() -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM polymarket_accounts WHERE status = 'live'").fetchall()
        return [_parse_pm_account(r) for r in rows]


def assign_pm_strategy(account_id: int, strategy: str, params: dict[str, Any], params_hash: str | None = None) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE polymarket_accounts SET strategy = ?, strategy_params_json = ?, params_hash = ? WHERE id = ?",
            (strategy, json.dumps(params), params_hash, account_id),
        )


def archive_pm_account(account_id: int, reason: str) -> None:
    """Hide an account (its trades stay) — used when an auto account is rotated to a
    new champion/candidate so each parameter set keeps its own forward track record."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE polymarket_accounts SET is_archived = 1, archived_reason = ?, archived_at = datetime('now'), "
            "status = 'idle' WHERE id = ?", (reason, account_id),
        )


def set_pm_status(account_id: int, status: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE polymarket_accounts SET status = ?, last_run_at = datetime('now') WHERE id = ?",
            (status, account_id),
        )


def set_pm_open_positions(account_id: int, positions: list[dict[str, Any]]) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE polymarket_accounts SET open_position_json = ? WHERE id = ?",
            (json.dumps(positions) if positions else None, account_id),
        )


def account_has_traded(account_id: int, session_slug: str) -> bool:
    """One attempt per session per account (the source bot's `momoTried`): true if
    this account already has a closed trade in the session."""
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM polymarket_trades WHERE account_id = ? AND session_slug = ? LIMIT 1",
            (account_id, session_slug),
        ).fetchone() is not None


def set_pm_telegram_msg(account_id: int, message_id: int | None) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE polymarket_accounts SET telegram_msg_id = ? WHERE id = ?", (message_id, account_id))


def reset_pm_account(account_id: int) -> None:
    with db.get_conn() as conn:
        acc = conn.execute("SELECT starting_balance FROM polymarket_accounts WHERE id = ?", (account_id,)).fetchone()
        if not acc:
            return
        conn.execute(
            "UPDATE polymarket_accounts SET balance = ?, open_position_json = NULL WHERE id = ?",
            (acc["starting_balance"], account_id),
        )
        conn.execute("DELETE FROM polymarket_trades WHERE account_id = ?", (account_id,))


def delete_pm_account(account_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM polymarket_trades WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM polymarket_accounts WHERE id = ?", (account_id,))


BURN_LOSS_FRACTION = 0.30  # paper training accounts get real room to run; auto accounts are never archived by this


def archive_pm_account_if_burned(account_id: int, balance: float, starting_balance: float) -> bool:
    if starting_balance <= 0 or balance > starting_balance * (1 - BURN_LOSS_FRACTION):
        return False
    with db.get_conn() as conn:
        row = conn.execute("SELECT is_archived FROM polymarket_accounts WHERE id = ?", (account_id,)).fetchone()
        if not row or row["is_archived"]:
            return False
        conn.execute(
            "UPDATE polymarket_accounts SET is_archived = 1, archived_reason = 'burned', archived_at = datetime('now'), "
            "status = 'idle', open_position_json = NULL WHERE id = ?",
            (account_id,),
        )
    return True


def record_pm_trade(account_id: int, trade: dict[str, Any], new_balance: float) -> None:
    with db.get_conn() as conn:
        acct = conn.execute("SELECT strategy FROM polymarket_accounts WHERE id = ?", (account_id,)).fetchone()
        conn.execute(
            "INSERT INTO polymarket_trades (account_id, session_slug, asset, side, entry_time, exit_time, "
            "entry_price, exit_price, shares, pnl, equity_after, exit_reason, strategy, flip_prob, peak_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, trade["session_slug"], trade["asset"], trade["side"], trade["entry_time"],
             trade["exit_time"], trade["entry_price"], trade["exit_price"], trade["shares"], trade["pnl"],
             new_balance, trade["exit_reason"], trade.get("strategy") or (acct["strategy"] if acct else None),
             trade.get("flip_prob"), trade.get("peak_pct")),
        )
        conn.execute(
            "UPDATE polymarket_accounts SET balance = ?, last_run_at = datetime('now') WHERE id = ?",
            (new_balance, account_id),
        )


def get_pm_trades(account_id: int) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM polymarket_trades WHERE account_id = ? ORDER BY id ASC", (account_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_pm_trades(limit: int = 300) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT t.*, a.name AS account_name FROM polymarket_trades t "
            "JOIN polymarket_accounts a ON a.id = t.account_id ORDER BY t.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# polymarket_copy_accounts / polymarket_copy_trades (Phase 10 — copytrader)
# ---------------------------------------------------------------------------

def create_copy_account(name: str, wallet_address: str, starting_balance: float = 1_000.0, stake_usd: float = 5.0) -> dict[str, Any]:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO polymarket_copy_accounts (name, wallet_address, created_at, starting_balance, balance, "
            "stake_usd, status) VALUES (?, ?, datetime('now'), ?, ?, ?, 'idle')",
            (name, wallet_address.lower(), starting_balance, starting_balance, stake_usd),
        )
        row = conn.execute("SELECT * FROM polymarket_copy_accounts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_copy_accounts(include_archived: bool = False) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        q = "SELECT * FROM polymarket_copy_accounts" if include_archived else "SELECT * FROM polymarket_copy_accounts WHERE is_archived = 0"
        return [dict(r) for r in conn.execute(q + " ORDER BY id DESC").fetchall()]


def get_copy_account(account_id: int) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM polymarket_copy_accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None


def list_live_copy_accounts() -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM polymarket_copy_accounts WHERE status = 'live'").fetchall()]


def set_copy_status(account_id: int, status: str) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE polymarket_copy_accounts SET status = ? WHERE id = ?", (status, account_id))


def set_copy_last_tx(account_id: int, tx_hash: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE polymarket_copy_accounts SET last_tx_hash = ?, last_poll_at = datetime('now') WHERE id = ?",
            (tx_hash, account_id),
        )


def set_copy_balance(account_id: int, balance: float) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE polymarket_copy_accounts SET balance = ? WHERE id = ?", (balance, account_id))


def open_copy_trade(account_id: int, trade: dict[str, Any]) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO polymarket_copy_trades (copy_account_id, tx_hash, market_slug, outcome, "
            "wallet_price, shadow_price, shadow_shares, status, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', datetime('now'))",
            (account_id, trade["tx_hash"], trade["slug"], trade["outcome"], trade["price"], trade["price"], trade["shares"]),
        )


def find_open_copy_trade(account_id: int, slug: str, outcome: str) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM polymarket_copy_trades WHERE copy_account_id = ? AND market_slug = ? AND outcome = ? "
            "AND status = 'open' ORDER BY id ASC LIMIT 1",
            (account_id, slug, outcome),
        ).fetchone()
        return dict(row) if row else None


def close_copy_trade(trade_id: int, exit_price: float, pnl: float) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE polymarket_copy_trades SET status = 'closed', shadow_price = ?, pnl = ?, closed_at = datetime('now') "
            "WHERE id = ?",
            (exit_price, pnl, trade_id),
        )


def get_copy_trades(account_id: int) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM polymarket_copy_trades WHERE copy_account_id = ? ORDER BY id DESC", (account_id,)
        ).fetchall()]


def delete_copy_account(account_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM polymarket_copy_trades WHERE copy_account_id = ?", (account_id,))
        conn.execute("DELETE FROM polymarket_copy_accounts WHERE id = ?", (account_id,))


def oldest_unbackfilled_offset(series_slug: str) -> int:
    """How many closed sessions of this series we already have (drives the next
    backfill page's offset — a resumable cursor without a separate table)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM pm_sessions WHERE series_slug = ? AND resolved_up IS NOT NULL",
            (series_slug,),
        ).fetchone()
        return row["c"]
