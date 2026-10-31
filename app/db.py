"""Minimal SQLite persistence: optimizer runs, demo accounts, and their trades."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# NOSTRO_DB_PATH points a staged server / offline trainer at a copy of the database.
DB_PATH = Path(os.environ.get("NOSTRO_DB_PATH") or Path(__file__).resolve().parent.parent / "data_cache" / "app.db")
DB_PATH.parent.mkdir(exist_ok=True)

# FastAPI runs sync endpoints in a thread pool, so concurrent requests can open
# concurrent sqlite3 connections; SQLite only allows one writer at a time. A
# generous busy timeout plus serializing access through this lock avoids
# "database is locked" errors under normal concurrent use.
_DB_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS optimizer_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    interval TEXT NOT NULL,
    n_iterations INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    top_strategy TEXT,
    top_score REAL
);

CREATE TABLE IF NOT EXISTS champions (
    symbol TEXT PRIMARY KEY,
    strategy TEXT NOT NULL,
    params_json TEXT NOT NULL,
    score REAL NOT NULL,
    train_metrics_json TEXT NOT NULL,
    test_metrics_json TEXT NOT NULL,
    run_id INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_stats (
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    best_score REAL NOT NULL,
    cycles_since_improvement INTEGER NOT NULL DEFAULT 0,
    total_cycles INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, strategy)
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    starting_balance REAL NOT NULL,
    balance REAL NOT NULL,
    symbol TEXT,
    strategy TEXT,
    strategy_params_json TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS account_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    pnl REAL NOT NULL,
    equity_after REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts (id)
);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    with _DB_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    """Read-only connection that does NOT queue behind the process-wide write lock. With the database in WAL mode readers
    run beside a writer, so pages stay fast while the trainer / live engine write. Use only for pure SELECTs."""
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


PROMOTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS champion_slots (
    symbol TEXT NOT NULL,
    tier TEXT NOT NULL,                 -- 'leader' (fast-moving current best) | 'confirmed' (strict)
    strategy TEXT NOT NULL,
    params_json TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    score REAL,                         -- latest same-scale (re-scored) fast-screen score
    score_epoch INTEGER,
    window TEXT,
    train_metrics_json TEXT,
    test_metrics_json TEXT,
    fold_scores_json TEXT,
    deep_score REAL,
    deep_fold_scores_json TEXT,
    neighborhood_json TEXT,
    apex_json TEXT,
    run_id INTEGER,
    version INTEGER,
    promoted_at TEXT,
    last_rescored_at TEXT,
    rescore_history_json TEXT,
    consecutive_fails INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, tier)
);

CREATE TABLE IF NOT EXISTS champion_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    tier TEXT NOT NULL,
    strategy TEXT,
    params_json TEXT,
    score REAL,
    reason TEXT,
    version INTEGER
);

CREATE TABLE IF NOT EXISTS promotion_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    run_id INTEGER,
    tier TEXT NOT NULL,
    stage TEXT NOT NULL,                -- rescore|margin|dwell|deep|apex|robust|data|pool
    outcome TEXT NOT NULL,              -- promoted|rejected|held|demoted|skipped|kept
    reason TEXT,
    strategy TEXT,
    params_hash TEXT,
    cand_score REAL,
    inc_score REAL,
    margin REAL,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_promo_symbol_ts ON promotion_attempts (symbol, ts);

CREATE TABLE IF NOT EXISTS candidate_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    run_id INTEGER,
    epoch INTEGER,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    tier INTEGER NOT NULL,              -- 0 rejected by the trade-count gate, 1 scored but flagged, 2 survivor
    flags INTEGER NOT NULL DEFAULT 0,   -- 1 diverged, 2 too_smooth, 4 burn limit, 8 regime, 16 score<=0
    n INTEGER,
    wr_net REAL, wr_gross REAL, rr_net REAL, rr_gross REAL,
    exp_net REAL, exp_gross REAL, cost_r REAL, risk_usd REAL,
    score REAL,
    apex REAL,
    w INTEGER NOT NULL DEFAULT 1,       -- sampling weight: survivors 1, sampled non-survivors 4
    params_json TEXT                    -- survivors only
);
CREATE INDEX IF NOT EXISTS idx_cand_symbol_ts ON candidate_log (symbol, ts);
CREATE INDEX IF NOT EXISTS idx_cand_tier_ts ON candidate_log (tier, ts);

CREATE TABLE IF NOT EXISTS auto_account_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,                -- created|swap|eval_concluded|hold
    account_id INTEGER,
    symbol TEXT,
    from_strategy TEXT,
    to_strategy TEXT,
    tier TEXT,
    reason TEXT,
    champion_version INTEGER
);

CREATE TABLE IF NOT EXISTS field_cycle_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    run_id INTEGER,
    epoch INTEGER,
    symbol TEXT NOT NULL,
    n_total INTEGER, n_tier0 INTEGER, n_tier1 INTEGER, n_survivors INTEGER,
    n_gp INTEGER, n_np INTEGER, n_gp_nn INTEGER, n_apex_pass INTEGER,
    median_cost_r REAL, median_risk_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_fcs_symbol_ts ON field_cycle_stats (symbol, ts);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db() -> None:
    with get_conn() as conn:
        try:  # WAL: readers never wait for a writer (persistent setting of the database file)
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        conn.executescript(SCHEMA)
        # Detect a pre-migration DB (no "diverged" column yet) *before* adding it below, so we
        # can invalidate any champion crowned under the old scoring scale.
        pre_existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(champions)").fetchall()]
        had_diverged_column = "diverged" in pre_existing_cols
        # Detect a pre-migration strategy_stats table (no cumulative trade count yet)
        # before adding it below, so a one-time backfill can run only once.
        pre_existing_stats_cols = [r[1] for r in conn.execute("PRAGMA table_info(strategy_stats)").fetchall()]
        had_trades_column = "total_trades_evaluated" in pre_existing_stats_cols
        # ALTER TABLE for anyone upgrading from a pre-existing app.db without these columns
        _ensure_column(conn, "optimizer_runs", "top_strategy", "TEXT")
        _ensure_column(conn, "optimizer_runs", "top_score", "REAL")
        _ensure_column(conn, "accounts", "open_position_json", "TEXT")
        _ensure_column(conn, "accounts", "last_bar_time", "TEXT")
        _ensure_column(conn, "accounts", "account_type", "TEXT NOT NULL DEFAULT 'standard'")
        _ensure_column(conn, "accounts", "profit_target", "REAL")
        _ensure_column(conn, "accounts", "max_drawdown", "REAL")
        _ensure_column(conn, "accounts", "max_contracts_minis", "INTEGER")
        _ensure_column(conn, "accounts", "peak_equity", "REAL")
        _ensure_column(conn, "accounts", "drawdown_floor", "REAL")
        _ensure_column(conn, "accounts", "eval_status", "TEXT")
        _ensure_column(conn, "accounts", "eval_started_at", "TEXT")
        _ensure_column(conn, "accounts", "eval_ended_at", "TEXT")
        _ensure_column(conn, "accounts", "eval_fail_reason", "TEXT")
        # Trainer statistical-soundness fields (candidate/confirmed champion gating — see
        # optimizer/search.py's edge_t_stat + diverged check, and the forward-test gate).
        # Added together now via the same idempotent migration pattern even though some stay
        # unpopulated until later phases land, to avoid repeated schema churn.
        _ensure_column(conn, "champions", "status", "TEXT NOT NULL DEFAULT 'candidate'")
        _ensure_column(conn, "champions", "fold_scores_json", "TEXT")
        _ensure_column(conn, "champions", "neighborhood_score", "REAL")
        _ensure_column(conn, "champions", "robust", "INTEGER")
        _ensure_column(conn, "champions", "diverged", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "champions", "too_smooth", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "champions", "exceeds_burn_limit", "INTEGER NOT NULL DEFAULT 0")
        # Flags a candidate solidly profitable in one market regime (trending vs choppy,
        # see app/optimizer/regime.py) and solidly losing in the other — same treatment
        # as diverged/too_smooth/exceeds_burn_limit: never becomes the persisted champion.
        _ensure_column(conn, "champions", "regime_inconsistent", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "champions", "forward_test_account_id", "INTEGER")
        _ensure_column(conn, "champions", "candidate_since", "TEXT")
        _ensure_column(conn, "champions", "confirmed_at", "TEXT")
        _ensure_column(conn, "accounts", "is_system", "INTEGER NOT NULL DEFAULT 0")
        # Daily loss/profit circuit breaker: stop opening new positions for the rest of the
        # trading day once realized P&L crosses either threshold (existing open positions
        # still manage to their normal stop/target/eod exit). NULL limit = no cap on that side.
        _ensure_column(conn, "accounts", "daily_loss_limit", "REAL")
        _ensure_column(conn, "accounts", "daily_profit_limit", "REAL")
        _ensure_column(conn, "accounts", "daily_realized_pnl", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "accounts", "daily_pnl_date", "TEXT")
        # Burn-threshold auto-hide: once an account loses BURN_LOSS_FRACTION of its starting
        # balance, it's archived (hidden from the home page, trade history kept) rather than
        # deleted — a losing account is a data point about the strategy, not something to lose.
        # Trade history of accounts that were reassigned or reset is kept here (the live feed, insights and the dashboard all read it). It used to exist only in a
        # database where someone had created it by hand, so every fresh database answered those pages with "no such table".
        conn.execute(
            "CREATE TABLE IF NOT EXISTS account_trades_archive(id INT, account_id INT, entry_time TEXT, exit_time TEXT, side TEXT, entry_price REAL, exit_price REAL, pnl REAL, "
            "equity_after REAL, exit_reason TEXT, stop_price REAL, target_price REAL, source TEXT, strategy TEXT, symbol TEXT, qty REAL, contract TEXT, commission REAL, "
            "risk_dollars REAL, archived_at, archive_note)"
        )
        _ensure_column(conn, "accounts", "is_archived", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "accounts", "archived_reason", "TEXT")
        _ensure_column(conn, "accounts", "archived_at", "TEXT")
        # Telegram trade-alert message id for the account's current open position, so
        # live_engine can edit the same message with PnL updates / the final close result
        # instead of sending a new one each tick. NULL when no position is open or Telegram
        # isn't configured.
        _ensure_column(conn, "accounts", "telegram_msg_id", "INTEGER")
        # Which trading day we've already sent a daily-circuit-breaker Telegram card for,
        # so hitting the profit/loss limit notifies once (not every tick for the rest of
        # the day the breaker stays tripped). NULL/mismatched date = not yet notified today.
        _ensure_column(conn, "accounts", "circuit_notified_date", "TEXT")
        # Real-order execution link (TradersPost -> Tradovate): 0/NULL for every account
        # except the single one explicitly linked via the UI. live_engine only sends a
        # webhook when traderspost_linked = 1 AND TRADERSPOST_WEBHOOK_URL is configured;
        # every other account's local simulation is completely unaffected either way.
        _ensure_column(conn, "accounts", "traderspost_linked", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "accounts", "tradovate_account_id", "TEXT")
        _ensure_column(conn, "accounts", "tradovate_account_name", "TEXT")
        _ensure_column(conn, "accounts", "traderspost_linked_at", "TEXT")
        # Stop live trading once equity reaches this level (e.g. an Apex payout threshold) and
        # flag the account for the user to notice, rather than keep trading past a withdrawal
        # point. NULL = no target set; awaiting_payout flips to 1 the moment it's crossed.
        _ensure_column(conn, "accounts", "payout_target", "REAL")
        _ensure_column(conn, "accounts", "awaiting_payout", "INTEGER NOT NULL DEFAULT 0")
        # Kept alongside each closed trade so a Telegram refresh-button tap, possibly long
        # after the trade closed, can still rebuild an accurate close caption (TP/SL aren't
        # derivable from entry/exit price alone).
        _ensure_column(conn, "account_trades", "stop_price", "REAL")
        _ensure_column(conn, "account_trades", "target_price", "REAL")
        # Per-strategy skill-level inputs (see app/optimizer/skill_level.py): cumulative
        # out-of-sample trades the strategy's best candidate has actually been judged on
        # across every cycle, and whether it has ever passed full deep validation.
        _ensure_column(conn, "strategy_stats", "total_trades_evaluated", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "strategy_stats", "ever_confirmed_champion", "INTEGER NOT NULL DEFAULT 0")
        if not had_trades_column:
            # Backfill: any strategy that's the current confirmed champion for a symbol
            # gets credit immediately rather than looking artificially reset to Noob.
            # total_trades_evaluated is deliberately left at 0 — no historical per-cycle
            # trade data was ever stored, and an honest 0 that grows from here beats a
            # fabricated backfill number.
            for row in conn.execute("SELECT symbol, strategy FROM champions WHERE status = 'confirmed'").fetchall():
                conn.execute(
                    "UPDATE strategy_stats SET ever_confirmed_champion = 1 WHERE symbol = ? AND strategy = ?",
                    (row["symbol"], row["strategy"]),
                )
        if not had_diverged_column:
            # Any champion already stored was crowned by the old sqrt(252)-inflated
            # sharpe_approx score, which is on a completely different (much larger) scale
            # than the new sqrt(n_trades) edge_t_stat. Left in place, its inflated score
            # would permanently block any correctly-scored new champion from ever winning
            # the ">" comparison in trainer_service.py. One-time reset, runs only the first
            # time this migration applies.
            conn.execute("DELETE FROM champions")

        conn.executescript(PROMOTION_SCHEMA)
        _ensure_column(conn, "champion_slots", "certification_json", "TEXT")
        _ensure_column(conn, "optimizer_runs", "slimmed", "INTEGER NOT NULL DEFAULT 0")
        # Position sizing per account: existing accounts stay on the legacy 1-mini behaviour
        # (fixed_1) so nothing changes underneath their open positions; new accounts default
        # to risk_micro (see create_account).
        _ensure_column(conn, "accounts", "sizing_mode", "TEXT NOT NULL DEFAULT 'fixed_1'")
        _ensure_column(conn, "accounts", "risk_per_trade", "REAL")
        _ensure_column(conn, "optimizer_runs", "epoch", "INTEGER")
        _ensure_column(conn, "strategy_stats", "peak_score_all_epochs", "REAL")
        _ensure_column(conn, "strategy_stats", "apex_last_pass_rate", "REAL")
        _ensure_column(conn, "strategy_stats", "apex_best_pass_rate", "REAL")
        # Live-trades page: tell real live trades apart from one-shot backtest-run rows (they
        # share account_trades), and denormalise strategy/symbol/qty so a trade keeps its own
        # attribution even after an account's strategy is swapped (the auto-account does that).
        _ensure_column(conn, "account_trades", "source", "TEXT")
        _ensure_column(conn, "account_trades", "strategy", "TEXT")
        _ensure_column(conn, "account_trades", "symbol", "TEXT")
        _ensure_column(conn, "account_trades", "qty", "REAL NOT NULL DEFAULT 1")
        _ensure_column(conn, "account_trades", "contract", "TEXT")
        _ensure_column(conn, "account_trades", "commission", "REAL")
        _ensure_column(conn, "account_trades", "risk_dollars", "REAL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_source_id ON account_trades (source, id)")
        if not conn.execute("SELECT 1 FROM meta WHERE key = 'trades_source_v1'").fetchone():
            # An account-level rule, not a per-row one: trades written before stop_price was
            # stored have NULL stop but are still live trades (a per-row rule misclassified 8 of 22).
            conn.execute(
                "UPDATE account_trades SET source = CASE WHEN account_id IN "
                "(SELECT DISTINCT account_id FROM account_trades WHERE stop_price IS NOT NULL) "
                "THEN 'live' ELSE 'backtest' END WHERE source IS NULL"
            )
            conn.execute(
                "UPDATE account_trades SET strategy = (SELECT a.strategy FROM accounts a WHERE a.id = account_id), "
                "symbol = (SELECT a.symbol FROM accounts a WHERE a.id = account_id) WHERE strategy IS NULL"
            )
            conn.execute("INSERT INTO meta (key, value) VALUES ('trades_source_v1', datetime('now'))")

        # Scoring-epoch migration (idempotent). Scores from different epochs are not comparable,
        # so on a bump: retire the champion slots to history, and reset each strategy's
        # best_score / staleness - but keep the -999 "never produced a candidate" sentinel
        # (else _needs_long_window would misroute strategies to the expensive 2-year search)
        # and keep total_cycles, trades and confirmed flags. The pre-bump peak is preserved in
        # peak_score_all_epochs so skill levels don't collapse.
        from app.optimizer.epoch import SCORING_EPOCH

        row = conn.execute("SELECT value FROM meta WHERE key = 'scoring_epoch'").fetchone()
        if not row or int(row["value"]) != SCORING_EPOCH:
            conn.execute(
                "UPDATE strategy_stats SET peak_score_all_epochs = MAX(COALESCE(peak_score_all_epochs, -999), best_score) "
            )
            conn.execute(
                "UPDATE strategy_stats SET best_score = CASE WHEN best_score <= -900 THEN best_score ELSE 0.0 END, "
                "cycles_since_improvement = 0"
            )
            for slot in conn.execute("SELECT * FROM champion_slots").fetchall():
                conn.execute(
                    "INSERT INTO champion_history (ts, symbol, tier, strategy, params_json, score, reason, version) "
                    "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?)",
                    (slot["symbol"], slot["tier"], slot["strategy"], slot["params_json"], slot["score"],
                     f"epoch_bump_to_{SCORING_EPOCH}", slot["version"]),
                )
            conn.execute("DELETE FROM champion_slots")
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('scoring_epoch', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCORING_EPOCH),),
            )
        # One-time re-baseline: the two legacy champions were crowned under an older, larger
        # score scale and could never be beaten (nothing ever re-scored them). Keep them as
        # history, leave the legacy table in place, and let the new slot pipeline crown fresh
        # leaders on the very next cycle.
        if not conn.execute("SELECT 1 FROM meta WHERE key = 'rebaseline_v1'").fetchone():
            for row in conn.execute("SELECT * FROM champions").fetchall():
                conn.execute(
                    "INSERT INTO champion_history (ts, symbol, tier, strategy, params_json, score, reason, version) "
                    "VALUES (datetime('now'), ?, 'legacy', ?, ?, ?, 'legacy_rebaseline', 0)",
                    (row["symbol"], row["strategy"], row["params_json"], row["score"]),
                )
            conn.execute("INSERT INTO meta (key, value) VALUES ('rebaseline_v1', datetime('now'))")


def save_optimizer_run(
    symbol: str,
    strategy: str,
    interval: str,
    n_iterations: int,
    result: list[dict[str, Any]],
    top_strategy: str | None = None,
    top_score: float | None = None,
) -> int:
    with get_conn() as conn:
        from app.optimizer.epoch import SCORING_EPOCH

        cur = conn.execute(
            "INSERT INTO optimizer_runs (created_at, symbol, strategy, interval, n_iterations, result_json, "
            "top_strategy, top_score, epoch) VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, strategy, interval, n_iterations, json.dumps(result), top_strategy, top_score, SCORING_EPOCH),
        )
        return cur.lastrowid


def list_optimizer_runs(limit: int = 20, strategy: str | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT id, created_at, symbol, strategy, interval, n_iterations, top_strategy, top_score FROM optimizer_runs"
    clauses, params = [], []
    if strategy:
        clauses.append("strategy = ?")
        params.append(strategy)
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_optimizer_runs(strategy: str | None = None) -> int:
    with get_conn() as conn:
        if strategy:
            row = conn.execute("SELECT COUNT(*) AS c FROM optimizer_runs WHERE strategy = ?", (strategy,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM optimizer_runs").fetchone()
        return row["c"]


def get_optimizer_run(run_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM optimizer_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["result"] = json.loads(d.pop("result_json"))
        return d


def get_strategy_stats(symbol: str) -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM strategy_stats WHERE symbol = ?", (symbol,)).fetchall()
        return {r["strategy"]: dict(r) for r in rows}


def update_strategy_stats(
    symbol: str, strategy: str, cycle_best_score: float | None, cycle_trades: int = 0
) -> None:
    """Upserts one strategy's rolling record after a cycle: bumps best_score and resets
    cycles_since_improvement when this cycle beat the stored best, otherwise just
    increments cycles_since_improvement. cycle_best_score=None (no valid candidate this
    cycle at all) counts as a cycle with no improvement, not a score of 0.

    cycle_trades is the out-of-sample trade count of THIS CYCLE'S best-scoring candidate
    only (not every candidate the search tried) — added to a running total used by
    app/optimizer/skill_level.py. Using only the winner keeps the number interpretable as
    "cumulative out-of-sample trades this strategy's best parameter set has actually been
    judged on," rather than a figure inflated by however many search iterations happened
    to be allocated that cycle."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT best_score, cycles_since_improvement FROM strategy_stats WHERE symbol = ? AND strategy = ?",
            (symbol, strategy),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO strategy_stats "
                "(symbol, strategy, best_score, cycles_since_improvement, total_cycles, total_trades_evaluated, updated_at) "
                "VALUES (?, ?, ?, 0, 1, ?, datetime('now'))",
                (symbol, strategy, cycle_best_score if cycle_best_score is not None else -999.0, cycle_trades),
            )
            return

        improved = cycle_best_score is not None and cycle_best_score > row["best_score"]
        new_best = cycle_best_score if improved else row["best_score"]
        new_streak = 0 if improved else row["cycles_since_improvement"] + 1
        conn.execute(
            "UPDATE strategy_stats SET best_score = ?, cycles_since_improvement = ?, "
            "total_cycles = total_cycles + 1, total_trades_evaluated = total_trades_evaluated + ?, "
            "updated_at = datetime('now') WHERE symbol = ? AND strategy = ?",
            (new_best, new_streak, cycle_trades, symbol, strategy),
        )


def update_strategy_apex(symbol: str, strategy: str, pass_rate: float | None) -> None:
    """Remember the latest and best Apex-evaluation pass rate seen for a strategy (the
    'PASSES NOSTRO' badge). No-op if the strategy has no stats row yet or no rate."""
    if pass_rate is None:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE strategy_stats SET apex_last_pass_rate = ?, "
            "apex_best_pass_rate = MAX(COALESCE(apex_best_pass_rate, 0), ?) WHERE symbol = ? AND strategy = ?",
            (pass_rate, pass_rate, symbol, strategy),
        )


def mark_strategy_confirmed(symbol: str, strategy: str) -> None:
    """Flips the one-way 'has this strategy ever passed full deep validation' flag —
    the strongest evidence this codebase produces, and the gate skill_level.py requires
    to award the top ('Master') skill tier."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE strategy_stats SET ever_confirmed_champion = 1 WHERE symbol = ? AND strategy = ?",
            (symbol, strategy),
        )


_SLOT_JSON_FIELDS = {
    "params_json": "params",
    "train_metrics_json": "train_metrics",
    "test_metrics_json": "test_metrics",
    "fold_scores_json": "fold_scores",
    "deep_fold_scores_json": "deep_fold_scores",
    "neighborhood_json": "neighborhood",
    "apex_json": "apex",
    "rescore_history_json": "rescore_history",
    "certification_json": "certification",
}


def meta_get(key: str, default: str | None = None) -> str | None:
    with read_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def meta_set(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def bump_champion_version() -> int:
    """Monotonic counter bumped whenever any leader/confirmed slot changes. The UI compares
    this (not the score, which re-scoring can lower) to decide when to announce a new champion."""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM meta WHERE key = 'champion_version'").fetchone()
        nxt = (int(row["value"]) if row else 0) + 1
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('champion_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(nxt),),
        )
        return nxt


def _parse_slot(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for col, key in _SLOT_JSON_FIELDS.items():
        raw = d.pop(col, None)
        d[key] = json.loads(raw) if raw else None
    return d


def get_slot(symbol: str, tier: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM champion_slots WHERE symbol = ? AND tier = ?", (symbol, tier)).fetchone()
        return _parse_slot(row) if row else None


def put_slot(symbol: str, tier: str, slot: dict[str, Any]) -> None:
    """Insert/replace a slot. BEGIN IMMEDIATE takes the write lock up front so the trainer
    and scripts/offline_train.py (a second process) can't interleave promotions."""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO champion_slots (symbol, tier, strategy, params_json, params_hash, score, "
            "score_epoch, window, train_metrics_json, test_metrics_json, fold_scores_json, deep_score, "
            "deep_fold_scores_json, neighborhood_json, apex_json, run_id, version, promoted_at, last_rescored_at, "
            "rescore_history_json, consecutive_fails, certification_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?, ?, ?)",
            (
                symbol, tier, slot["strategy"], json.dumps(slot["params"]), slot["params_hash"], slot.get("score"),
                slot.get("score_epoch"), slot.get("window"),
                json.dumps(slot.get("train_metrics")) if slot.get("train_metrics") is not None else None,
                json.dumps(slot.get("test_metrics")) if slot.get("test_metrics") is not None else None,
                json.dumps(slot.get("fold_scores")) if slot.get("fold_scores") is not None else None,
                slot.get("deep_score"),
                json.dumps(slot.get("deep_fold_scores")) if slot.get("deep_fold_scores") is not None else None,
                json.dumps(slot.get("neighborhood")) if slot.get("neighborhood") is not None else None,
                json.dumps(slot.get("apex")) if slot.get("apex") is not None else None,
                slot.get("run_id"), slot.get("version"), slot.get("promoted_at"), slot.get("last_rescored_at"),
                json.dumps(slot.get("rescore_history") or []), slot.get("consecutive_fails", 0),
                json.dumps(slot.get("certification")) if slot.get("certification") is not None else None,
            ),
        )


def patch_slot(symbol: str, tier: str, **fields: Any) -> None:
    """Update selected scalar/JSON fields of an existing slot (re-score bookkeeping)."""
    if not fields:
        return
    sets, vals = [], []
    json_cols = {v: k for k, v in _SLOT_JSON_FIELDS.items()}
    for key, value in fields.items():
        col = json_cols.get(key, key)
        sets.append(f"{col} = ?")
        vals.append(json.dumps(value) if key in json_cols and value is not None else value)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"UPDATE champion_slots SET {', '.join(sets)} WHERE symbol = ? AND tier = ?", (*vals, symbol, tier))


def retire_slot(symbol: str, tier: str, reason: str) -> None:
    """Move a slot to champion_history and clear it (superseded, decayed, demoted)."""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM champion_slots WHERE symbol = ? AND tier = ?", (symbol, tier)).fetchone()
        if not row:
            return
        conn.execute(
            "INSERT INTO champion_history (ts, symbol, tier, strategy, params_json, score, reason, version) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?)",
            (symbol, tier, row["strategy"], row["params_json"], row["score"], reason, row["version"]),
        )
        conn.execute("DELETE FROM champion_slots WHERE symbol = ? AND tier = ?", (symbol, tier))


def log_attempt(
    symbol: str, run_id: int | None, tier: str, stage: str, outcome: str, reason: str,
    strategy: str | None = None, params_hash: str | None = None, cand_score: float | None = None,
    inc_score: float | None = None, margin: float | None = None, detail: dict[str, Any] | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO promotion_attempts (ts, symbol, run_id, tier, stage, outcome, reason, strategy, params_hash, "
            "cand_score, inc_score, margin, detail_json) VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, run_id, tier, stage, outcome, reason, strategy, params_hash, cand_score, inc_score, margin,
             json.dumps(detail) if detail else None),
        )


def list_attempts(symbol: str | None = None, tier: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    clauses, args = [], []
    if symbol:
        clauses.append("symbol = ?")
        args.append(symbol)
    if tier:
        clauses.append("tier = ?")
        args.append(tier)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM promotion_attempts {where} ORDER BY id DESC LIMIT ?", (*args, limit)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        raw = d.pop("detail_json", None)
        d["detail"] = json.loads(raw) if raw else None
        out.append(d)
    return out


def attempted_recently(symbol: str, tier: str, params_hash: str, hours: float) -> bool:
    """True if this exact param set already went through a deep/robust/apex stage within
    `hours` - the attempt log doubles as the retry cache, so a candidate that just failed
    an expensive validation isn't re-run every cycle."""
    since = meta_get("apex_gate_since", "1970-01-01 00:00:00")  # attempts judged by an older gate are not a cache hit
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM promotion_attempts WHERE symbol = ? AND tier = ? AND params_hash = ? "
            "AND stage IN ('deep', 'apex', 'robust') AND ts >= datetime('now', ?) AND ts >= ? LIMIT 1",
            (symbol, tier, params_hash, f"-{hours} hours", since),
        ).fetchone()
        return row is not None


def get_active_system_account() -> dict[str, Any] | None:
    """The AUTO account: the newest non-archived is_system account."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE is_system = 1 AND is_archived = 0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _parse_account(row) if row else None


def set_risk_per_trade(account_id: int, risk_dollars: float | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET risk_per_trade = ? WHERE id = ?", (risk_dollars, account_id))


def archive_account(account_id: int, reason: str) -> None:
    """Hide an account from the default list while keeping every trade (never reset/delete
    the auto account - its history is the forward evidence for the strategies it followed)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET is_archived = 1, archived_reason = ?, archived_at = datetime('now'), "
            "status = 'idle', open_position_json = NULL WHERE id = ?",
            (reason, account_id),
        )


def log_auto_event(
    event: str, account_id: int | None, symbol: str | None = None, from_strategy: str | None = None,
    to_strategy: str | None = None, tier: str | None = None, reason: str | None = None,
    champion_version: int | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO auto_account_log (ts, event, account_id, symbol, from_strategy, to_strategy, tier, reason, "
            "champion_version) VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)",
            (event, account_id, symbol, from_strategy, to_strategy, tier, reason, champion_version),
        )


def list_auto_events(limit: int = 30) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM auto_account_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def last_auto_swap_ts(account_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ts FROM auto_account_log WHERE account_id = ? AND event IN ('swap', 'created') ORDER BY id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return row["ts"] if row else None


def insert_candidates(rows: list[tuple], stats: tuple) -> None:
    """One batched write per training cycle: the sampled candidate rows plus the exact
    per-cycle counts (the Field page's headline numbers come from the counts, so sampling the
    scatter never distorts them)."""
    with get_conn() as conn:
        if rows:
            conn.executemany(
                "INSERT INTO candidate_log (ts, run_id, epoch, symbol, strategy, tier, flags, n, wr_net, wr_gross, "
                "rr_net, rr_gross, exp_net, exp_gross, cost_r, risk_usd, score, apex, w, params_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        conn.execute(
            "INSERT INTO field_cycle_stats (ts, run_id, epoch, symbol, n_total, n_tier0, n_tier1, n_survivors, "
            "n_gp, n_np, n_gp_nn, n_apex_pass, median_cost_r, median_risk_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            stats,
        )


def prune_field() -> None:
    """Retention: non-survivors 48h, survivors and cycle stats 180 days (the "all time" view)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM candidate_log WHERE tier < 2 AND ts < strftime('%s', 'now') - 172800")
        conn.execute("DELETE FROM candidate_log WHERE ts < strftime('%s', 'now') - 15552000")
        conn.execute("DELETE FROM field_cycle_stats WHERE ts < strftime('%s', 'now') - 15552000")


def trim_attempts(keep: int = 5000) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM promotion_attempts WHERE id <= (SELECT MAX(id) FROM promotion_attempts) - ?", (keep,)
        )


def slim_optimizer_runs(batch: int = 50) -> int:
    """Age-based retention for optimizer_runs.result_json, which was ~37 MB/day of growth
    (mostly per-candidate fold_metrics nobody reads once a run is old). Levels: 1 = 12h-7d old
    -> top 15 results without fold_metrics; 2 = 7-30d -> top 5; 3 = >30d -> top 1. The
    top_strategy/top_score columns are never touched. Returns rows slimmed this call."""
    tiers = [(3, "-30 days", 1), (2, "-7 days", 5), (1, "-12 hours", 15)]
    done = 0
    with get_conn() as conn:
        for level, cutoff, keep in tiers:
            rows = conn.execute(
                "SELECT id, result_json FROM optimizer_runs WHERE strategy = 'ALL_STRATEGIES' AND slimmed < ? "
                "AND created_at <= datetime('now', ?) ORDER BY id LIMIT ?",
                (level, cutoff, batch - done),
            ).fetchall()
            for r in rows:
                try:
                    results = json.loads(r["result_json"])
                except ValueError:
                    continue
                slim = []
                for cand in results[:keep]:
                    cand = dict(cand)
                    cand.pop("fold_metrics", None)
                    slim.append(cand)
                conn.execute(
                    "UPDATE optimizer_runs SET result_json = ?, slimmed = ? WHERE id = ?",
                    (json.dumps(slim), level, r["id"]),
                )
                done += 1
            if done >= batch:
                break
    return done


def get_champion(symbol: str) -> dict[str, Any] | None:
    """The champion shown to the UI: the fast-moving LEADER if there is one, else the strict
    CONFIRMED slot. Keeps the legacy response keys (strategy, params, score, train/test
    metrics, fold_scores, updated_at, status) so existing consumers keep working, and adds
    tier/version/re-score info plus the confirmed slot's summary."""
    leader = get_slot(symbol, "leader")
    confirmed = get_slot(symbol, "confirmed")
    shown = leader or confirmed
    if not shown:
        return None
    d = dict(shown)
    d["updated_at"] = d.get("promoted_at")
    d["status"] = "confirmed" if shown is confirmed else "leader"
    d["confirmed"] = (
        {k: confirmed.get(k) for k in ("strategy", "params", "deep_score", "promoted_at", "version", "apex")}
        if confirmed else None
    )
    if d.get("promoted_at"):
        try:
            promoted = dt.datetime.strptime(d["promoted_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            d["age_hours"] = round((dt.datetime.now(dt.timezone.utc) - promoted).total_seconds() / 3600, 2)
        except ValueError:
            d["age_hours"] = None
    return d


def list_champions() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT symbol FROM champions").fetchall()
    return [get_champion(r["symbol"]) for r in rows]


def upsert_champion(symbol: str, winner: dict[str, Any], run_id: int, status: str = "candidate") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO champions (symbol, strategy, params_json, score, train_metrics_json, test_metrics_json, "
            "run_id, updated_at, diverged, too_smooth, exceeds_burn_limit, regime_inconsistent, fold_scores_json, "
            "neighborhood_score, robust, status, candidate_since, confirmed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?) "
            "ON CONFLICT(symbol) DO UPDATE SET strategy=excluded.strategy, params_json=excluded.params_json, "
            "score=excluded.score, train_metrics_json=excluded.train_metrics_json, "
            "test_metrics_json=excluded.test_metrics_json, run_id=excluded.run_id, updated_at=excluded.updated_at, "
            "diverged=excluded.diverged, too_smooth=excluded.too_smooth, "
            "exceeds_burn_limit=excluded.exceeds_burn_limit, regime_inconsistent=excluded.regime_inconsistent, "
            "fold_scores_json=excluded.fold_scores_json, neighborhood_score=excluded.neighborhood_score, "
            "robust=excluded.robust, status=excluded.status, confirmed_at=excluded.confirmed_at",
            (
                symbol,
                winner["strategy"],
                json.dumps(winner["params"]),
                winner["score"],
                json.dumps(winner["train_metrics"]),
                json.dumps(winner["test_metrics"]),
                run_id,
                int(winner.get("diverged", False)),
                int(winner.get("too_smooth", False)),
                int(winner.get("exceeds_burn_limit", False)),
                int(winner.get("regime_inconsistent", False)),
                json.dumps(winner.get("fold_scores")) if winner.get("fold_scores") is not None else None,
                winner.get("neighborhood_score"),
                int(winner["robust"]) if winner.get("robust") is not None else None,
                status,
                dt.datetime.now().isoformat() if status == "confirmed" else None,
            ),
        )


def _parse_account(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    pos = d.pop("open_position_json", None)
    d["open_position"] = json.loads(pos) if pos else None
    return d


def create_account(
    name: str,
    starting_balance: float = 50_000.0,
    account_type: str = "standard",
    profit_target: float | None = None,
    max_drawdown: float | None = None,
    max_contracts_minis: int | None = None,
    daily_loss_limit: float | None = None,
    daily_profit_limit: float | None = None,
    sizing_mode: str = "risk_micro",
    risk_per_trade: float | None = None,
    is_system: int = 0,
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO accounts (name, created_at, starting_balance, balance, status, account_type, "
            "profit_target, max_drawdown, max_contracts_minis, daily_loss_limit, daily_profit_limit, "
            "sizing_mode, risk_per_trade, is_system) "
            "VALUES (?, datetime('now'), ?, ?, 'idle', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name, starting_balance, starting_balance, account_type, profit_target, max_drawdown,
                max_contracts_minis, daily_loss_limit, daily_profit_limit, sizing_mode, risk_per_trade, is_system,
            ),
        )
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (new_id,)).fetchone()
        return _parse_account(row)


def list_accounts(include_archived: bool = False) -> list[dict[str, Any]]:
    with read_conn() as conn:
        query = "SELECT * FROM accounts" if include_archived else "SELECT * FROM accounts WHERE is_archived = 0"
        rows = conn.execute(query + " ORDER BY id DESC").fetchall()
        return [_parse_account(r) for r in rows]


BURN_LOSS_FRACTION = 0.05  # an account down this fraction of its starting balance is "burned"


def check_and_archive_if_burned(account_id: int, balance: float, starting_balance: float) -> bool:
    """Hide a burned account from the default account list (trade history kept, not deleted)
    once it's lost BURN_LOSS_FRACTION of its starting balance, and stop it from trading
    further. Returns True if this call just archived it."""
    if starting_balance <= 0 or balance > starting_balance * (1 - BURN_LOSS_FRACTION):
        return False
    with get_conn() as conn:
        row = conn.execute("SELECT is_archived FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row or row["is_archived"]:
            return False
        conn.execute(
            "UPDATE accounts SET is_archived = 1, archived_reason = 'burned', archived_at = datetime('now'), "
            "status = 'idle', open_position_json = NULL WHERE id = ?",
            (account_id,),
        )
    return True


def list_live_accounts() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM accounts WHERE status = 'live'").fetchall()
        return [_parse_account(r) for r in rows]


def get_account(account_id: int) -> dict[str, Any] | None:
    with read_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return _parse_account(row) if row else None


def assign_strategy(account_id: int, strategy: str, symbol: str, params: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET strategy = ?, symbol = ?, strategy_params_json = ? WHERE id = ?",
            (strategy, symbol, json.dumps(params), account_id),
        )


def set_account_status(account_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET status = ?, last_run_at = datetime('now') WHERE id = ?",
            (status, account_id),
        )


def reset_account(account_id: int) -> None:
    with get_conn() as conn:
        acc = conn.execute("SELECT starting_balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not acc:
            return
        conn.execute(
            "UPDATE accounts SET balance = ?, status = 'idle', open_position_json = NULL, last_bar_time = NULL, "
            "eval_status = NULL, eval_started_at = NULL, eval_ended_at = NULL, eval_fail_reason = NULL, "
            "peak_equity = NULL, drawdown_floor = NULL, daily_realized_pnl = 0, daily_pnl_date = NULL WHERE id = ?",
            (acc["starting_balance"], account_id),
        )
        conn.execute("DELETE FROM account_trades WHERE account_id = ?", (account_id,))


def go_live(account_id: int, last_bar_time: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET status = 'live', open_position_json = NULL, last_bar_time = ? WHERE id = ?",
            (last_bar_time, account_id),
        )


def stop_live(account_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET status = 'idle', open_position_json = NULL WHERE id = ?",
            (account_id,),
        )


def start_eval(account_id: int, starting_balance: float, max_drawdown: float) -> None:
    floor = starting_balance - max_drawdown
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET eval_status = 'in_progress', eval_started_at = datetime('now'), "
            "eval_ended_at = NULL, eval_fail_reason = NULL, peak_equity = ?, drawdown_floor = ? WHERE id = ?",
            (starting_balance, floor, account_id),
        )


def update_eval_progress(account_id: int, peak_equity: float, drawdown_floor: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET peak_equity = ?, drawdown_floor = ? WHERE id = ?",
            (peak_equity, drawdown_floor, account_id),
        )


def finish_eval(account_id: int, eval_status: str, reason: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET eval_status = ?, eval_ended_at = datetime('now'), eval_fail_reason = ?, "
            "status = 'idle' WHERE id = ?",
            (eval_status, reason, account_id),
        )


def get_trades_by_month(account_id: int, year: int, month: int) -> list[dict[str, Any]]:
    prefix = f"{year:04d}-{month:02d}"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM account_trades WHERE account_id = ? AND exit_time LIKE ? ORDER BY exit_time ASC",
            (account_id, f"{prefix}%"),
        ).fetchall()
        return [dict(r) for r in rows]


def set_open_position(account_id: int, position: dict[str, Any] | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET open_position_json = ? WHERE id = ?",
            (json.dumps(position) if position else None, account_id),
        )


def set_telegram_msg(account_id: int, message_id: int | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET telegram_msg_id = ? WHERE id = ?", (message_id, account_id))


def set_last_bar_time(account_id: int, last_bar_time: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET last_bar_time = ? WHERE id = ?", (last_bar_time, account_id))


def set_circuit_notified(account_id: int, trading_date: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET circuit_notified_date = ? WHERE id = ?", (trading_date, account_id))


def set_payout_target(account_id: int, target: float | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET payout_target = ?, awaiting_payout = 0 WHERE id = ?", (target, account_id))


def set_awaiting_payout(account_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET status = 'idle', awaiting_payout = 1 WHERE id = ?", (account_id,))


def set_traderspost_link(account_id: int, linked: bool, tradovate_account_id: str | None = None, tradovate_account_name: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET traderspost_linked = ?, tradovate_account_id = ?, tradovate_account_name = ?, "
            "traderspost_linked_at = CASE WHEN ? THEN datetime('now') ELSE NULL END WHERE id = ?",
            (1 if linked else 0, tradovate_account_id if linked else None, tradovate_account_name if linked else None, 1 if linked else 0, account_id),
        )


def record_live_trade(account_id: int, trade: dict[str, Any], new_balance: float) -> None:
    """Append one closed live trade (as opposed to record_account_run, which wipes and
    replaces the whole trade list for a one-shot backtest)."""
    with get_conn() as conn:
        acct = conn.execute("SELECT strategy, symbol FROM accounts WHERE id = ?", (account_id,)).fetchone()
        conn.execute(
            "INSERT INTO account_trades (account_id, entry_time, exit_time, side, entry_price, "
            "exit_price, pnl, equity_after, exit_reason, stop_price, target_price, source, strategy, symbol, "
            "qty, contract, commission, risk_dollars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                trade["entry_time"],
                trade["exit_time"],
                trade["side"],
                trade["entry_price"],
                trade["exit_price"],
                trade["pnl"],
                new_balance,
                trade["exit_reason"],
                trade.get("stop_price"),
                trade.get("target_price"),
                trade.get("strategy") or (acct["strategy"] if acct else None),
                trade.get("symbol") or (acct["symbol"] if acct else None),
                trade.get("qty", 1),
                trade.get("contract"),
                trade.get("commission"),
                trade.get("risk_dollars"),
            ),
        )
        conn.execute(
            "UPDATE accounts SET balance = ?, open_position_json = NULL, last_run_at = datetime('now') WHERE id = ?",
            (new_balance, account_id),
        )


def update_daily_pnl(account_id: int, trading_date: str, realized_pnl_delta: float) -> None:
    """Accumulate today's realized P&L for the daily circuit breaker. If `trading_date`
    doesn't match what's stored, the day has rolled over — reset the accumulator to just
    this trade's P&L instead of adding to a stale total."""
    with get_conn() as conn:
        row = conn.execute("SELECT daily_pnl_date, daily_realized_pnl FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row and row["daily_pnl_date"] == trading_date:
            new_total = (row["daily_realized_pnl"] or 0.0) + realized_pnl_delta
        else:
            new_total = realized_pnl_delta
        conn.execute(
            "UPDATE accounts SET daily_pnl_date = ?, daily_realized_pnl = ? WHERE id = ?",
            (trading_date, new_total, account_id),
        )


def get_recent_trades_with_account(limit: int = 300) -> list[dict[str, Any]]:
    with read_conn() as conn:
        rows = conn.execute(
            f"SELECT t.*, a.name AS account_name, a.symbol AS account_symbol FROM {FEED_TRADES} t "
            "JOIN accounts a ON a.id = t.account_id ORDER BY t.exit_time DESC, t.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# closed trades shown in the trade feeds: the live table + trades restored into the archive after an account reset wiped them
_FEED_COLS = ("id, account_id, symbol, strategy, side, qty, contract, entry_time, exit_time, entry_price, exit_price, pnl, "
              "exit_reason, equity_after, stop_price, target_price, risk_dollars, source")
FEED_TRADES = (f"(SELECT {_FEED_COLS}, 0 AS restored FROM account_trades UNION ALL "
               f"SELECT {_FEED_COLS}, 1 AS restored FROM account_trades_archive WHERE archive_note LIKE 'restored:%')")


def live_trades_feed(after_id: int | None = None, before_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Live (not backtest) closed trades across all accounts, newest first, attributed to the
    strategy/symbol that took them. after_id -> only newer rows (live polling); before_id ->
    older page (load-more). Archived accounts are included and flagged."""
    clauses, args = ["t.source = 'live'"], []
    if after_id is not None:
        clauses.append("t.id > ?")
        args.append(after_id)
    if before_id is not None:
        clauses.append("t.id < ?")
        args.append(before_id)
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT t.id, t.account_id, a.name AS account_name, a.is_archived AS archived, "
            "COALESCE(t.symbol, a.symbol) AS symbol, COALESCE(t.strategy, a.strategy) AS strategy, "
            "t.side, t.qty, t.contract, t.entry_time, t.exit_time, t.entry_price, t.exit_price, t.pnl, "
            "t.exit_reason, t.equity_after, t.stop_price, t.target_price, t.risk_dollars, t.restored "
            f"FROM {FEED_TRADES} t JOIN accounts a ON a.id = t.account_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY t.id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def live_trades_since(date_prefix: str | None) -> list[dict[str, Any]]:
    """All live trades whose exit_time date (ISO prefix, NY local) is >= date_prefix, oldest first."""
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT t.id, t.account_id, a.name AS account_name, COALESCE(t.symbol, a.symbol) AS symbol, "
            "COALESCE(t.strategy, a.strategy) AS strategy, t.side, t.qty, t.entry_price, t.stop_price, "
            f"t.exit_time, t.pnl, t.risk_dollars FROM {FEED_TRADES} t JOIN accounts a ON a.id = t.account_id "
            "WHERE t.source = 'live' AND (? IS NULL OR substr(t.exit_time, 1, 10) >= ?) ORDER BY t.exit_time ASC, t.id ASC",
            (date_prefix, date_prefix),
        ).fetchall()
        return [dict(r) for r in rows]


def record_account_run(account_id: int, ending_balance: float, trades: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM account_trades WHERE account_id = ?", (account_id,))
        acct = conn.execute("SELECT strategy, symbol FROM accounts WHERE id = ?", (account_id,)).fetchone()
        for t in trades:
            conn.execute(
                "INSERT INTO account_trades (account_id, entry_time, exit_time, side, entry_price, "
                "exit_price, pnl, equity_after, exit_reason, source, strategy, symbol) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'backtest', ?, ?)",
                (
                    account_id,
                    t["entry_time"],
                    t["exit_time"],
                    t["side"],
                    t["entry_price"],
                    t["exit_price"],
                    t["pnl"],
                    t["equity_after"],
                    t["exit_reason"],
                    acct["strategy"] if acct else None,
                    acct["symbol"] if acct else None,
                ),
            )
        conn.execute(
            "UPDATE accounts SET balance = ?, status = 'ran', last_run_at = datetime('now') WHERE id = ?",
            (ending_balance, account_id),
        )


def get_account_trades(account_id: int) -> list[dict[str, Any]]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM account_trades WHERE account_id = ? ORDER BY id ASC", (account_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_account(account_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM account_trades WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
