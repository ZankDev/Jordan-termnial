"""Connection setup for the real-money path, so nothing has to be edited by hand in run.bat: the TradersPost webhook URL, the Telegram bot (token and chat) and
the real-balance records.

Secrets are stored in the database encrypted with Windows DPAPI (bound to this Windows user, the same protection the Polymarket wallet key gets) and are
loaded into the process environment at start, so every existing code path that reads TRADERSPOST_WEBHOOK_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID keeps
working unchanged. A value set in run.bat still wins over a stored one. Secrets are never returned to the browser, only masked.

Real balance: TradingView and TradersPost expose no free balance feed, so the balance is recorded from what the owner can provide: a typed balance, or a Tradovate
performance export (CSV). Each imported fill is stored once; the summary compares the realised profit to what the local simulation believes."""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import re
import time
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
WEBHOOK_RE = re.compile(r"^https://webhooks\.traderspost\.io/trading/webhook/[0-9a-fA-F-]{8,}/[A-Za-z0-9_-]{6,}$")
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
ENV = {"webhook": "TRADERSPOST_WEBHOOK_URL", "tg_token": "TELEGRAM_BOT_TOKEN", "tg_chat": "TELEGRAM_CHAT_ID"}
_pending_token: dict[str, Any] = {}


# ------------------------------------------------------------------ encrypted storage
def _put(key: str, value: str) -> None:
    from app.wealth import security

    db.meta_set(f"conn:{key}", base64.b64encode(security.dpapi_protect(value.encode())).decode())


def _get(key: str) -> str | None:
    raw = db.meta_get(f"conn:{key}")
    if not raw:
        return None
    try:
        from app.wealth import security

        return security.dpapi_unprotect(base64.b64decode(raw)).decode()
    except Exception:  # noqa: BLE001
        logger.exception("could not decrypt stored connection %s", key)
        return None


def _clear(key: str) -> None:
    db.meta_set(f"conn:{key}", "")


def load_into_env() -> None:
    """Called once at start: anything set up through the wizard becomes visible to the code that reads the environment."""
    for k, env in ENV.items():
        if not os.environ.get(env):
            v = _get(k)
            if v:
                os.environ[env] = v


def _source(k: str) -> str:
    stored = bool(db.meta_get(f"conn:{k}"))
    if os.environ.get(ENV[k]) and not (stored and _get(k) == os.environ.get(ENV[k])):
        return "run.bat"
    return "wizard" if stored else "none"


def mask(s: str | None, keep: int = 4) -> str:
    if not s:
        return ""
    return s[:keep] + "…" + "•" * 6 + s[-3:] if len(s) > keep + 6 else "•" * len(s)


# ------------------------------------------------------------------ TradersPost
def set_webhook(url: str) -> dict[str, Any]:
    url = (url or "").strip()
    if not WEBHOOK_RE.match(url):
        raise ValueError("That does not look like a TradersPost webhook URL. It should look like https://webhooks.traderspost.io/trading/webhook/<id>/<password> (copy it from your Strategy page, Webhook section).")
    _put("webhook", url)
    os.environ[ENV["webhook"]] = url
    return {"ok": True, "masked": mask(url, 46)}


def clear_webhook() -> None:
    _clear("webhook")
    os.environ.pop(ENV["webhook"], None)


def log_signal(payload: dict[str, Any], status: int | None, ok: bool, detail: str) -> None:
    try:
        with db.get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS traderspost_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')), action TEXT, ticker TEXT, price REAL, "
                         "qty REAL, stop REAL, target REAL, http_status INTEGER, ok INTEGER, detail TEXT)")
            conn.execute("INSERT INTO traderspost_log (action, ticker, price, qty, stop, target, http_status, ok, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                         (payload.get("action"), payload.get("ticker"), payload.get("price"), payload.get("quantity"), (payload.get("stopLoss") or {}).get("stopPrice"),
                          (payload.get("takeProfit") or {}).get("limitPrice"), status, 1 if ok else 0, (detail or "")[:300]))
            conn.execute("DELETE FROM traderspost_log WHERE id NOT IN (SELECT id FROM traderspost_log ORDER BY id DESC LIMIT 200)")
    except Exception:  # noqa: BLE001
        logger.exception("could not log the TradersPost signal")


def recent_signals(n: int = 20) -> list[dict[str, Any]]:
    try:
        with db.get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS traderspost_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')), action TEXT, ticker TEXT, price REAL, "
                         "qty REAL, stop REAL, target REAL, http_status INTEGER, ok INTEGER, detail TEXT)")
            return [dict(r) for r in conn.execute("SELECT * FROM traderspost_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()]
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ Telegram
def telegram_verify_token(token: str) -> dict[str, Any]:
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise ValueError("That does not look like a bot token. It looks like 123456789:AAH... and BotFather gives it to you when you create the bot.")
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    if r.status_code != 200 or not r.json().get("ok"):
        raise ValueError("Telegram rejected this token. Copy it again from BotFather.")
    me = r.json()["result"]
    _pending_token.clear()
    _pending_token.update({"token": token, "username": me.get("username"), "at": time.time()})
    return {"username": me.get("username"), "name": me.get("first_name")}


def telegram_detect_chat() -> dict[str, Any]:
    """After the owner opened the bot and pressed START, read the chat id back and save the token and chat together."""
    if not _pending_token or time.time() - _pending_token.get("at", 0) > 1800:
        raise ValueError("Verify the bot token first.")
    token = _pending_token["token"]
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    if r.status_code == 409:
        raise ValueError("Another program is already reading this bot's messages. Use the token of a new bot made for this terminal.")
    res = (r.json().get("result") or []) if r.status_code == 200 else []
    chats = [((u.get("message") or {}).get("chat") or {}) for u in res if u.get("message")]
    chats = [c for c in chats if c.get("id") and c.get("type") == "private"]
    if not chats:
        return {"found": False}
    chat = chats[-1]
    _put("tg_token", token)
    _put("tg_chat", str(chat["id"]))
    os.environ[ENV["tg_token"]] = token
    os.environ[ENV["tg_chat"]] = str(chat["id"])
    _pending_token.clear()
    return {"found": True, "chat_name": chat.get("first_name") or chat.get("username") or "your chat"}


def clear_telegram() -> None:
    for k in ("tg_token", "tg_chat"):
        _clear(k)
        os.environ.pop(ENV[k], None)


_BOT_NAME: dict[str, Any] = {}


def telegram_info() -> dict[str, Any]:
    tok = os.environ.get(ENV["tg_token"])
    chat = os.environ.get(ENV["tg_chat"])
    out: dict[str, Any] = {"configured": bool(tok and chat), "source": _source("tg_token") if tok else "none", "username": None}
    if tok:
        # the bot's name never changes for a token: ask Telegram once per token, not on every page refresh (it took up to 10 s)
        if _BOT_NAME.get("tok") != tok:
            try:
                r = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=6)
                if r.status_code == 200:
                    _BOT_NAME.update(tok=tok, name=r.json()["result"].get("username"))
            except Exception:  # noqa: BLE001
                pass
        if _BOT_NAME.get("tok") == tok:
            out["username"] = _BOT_NAME.get("name")
    out["pending"] = {"username": _pending_token.get("username")} if _pending_token else None
    return out


# ------------------------------------------------------------------ real balance
def _ensure_balance_tables(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS real_balance_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')), balance REAL NOT NULL, source TEXT, note TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS real_fills (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, qty REAL, buy_price REAL, sell_price REAL, pnl REAL NOT NULL, "
                 "bought_at TEXT, sold_at TEXT, imported_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE(symbol, qty, buy_price, sell_price, bought_at, sold_at))")


def add_balance(balance: float, note: str = "") -> dict[str, Any]:
    if not (balance == balance) or balance <= 0 or balance > 5_000_000:
        raise ValueError("Enter the balance in dollars, for example 25312.50.")
    with db.get_conn() as conn:
        _ensure_balance_tables(conn)
        conn.execute("INSERT INTO real_balance_log (balance, source, note) VALUES (?, 'typed', ?)", (float(balance), note[:200]))
    return {"ok": True}


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def import_tradovate_csv(data: bytes, strict: bool = False) -> dict[str, Any]:
    """Tradovate's Performance / trade export: one row per closed round trip with its profit. Columns are matched by name, ignoring case and spaces."""
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("The file has no rows.")
    key = {re.sub(r"[^a-z]", "", k.lower()): k for k in rows[0].keys() if k}
    if strict and not ({"boughttimestamp", "soldtimestamp"} <= set(key) or {"buyfillid", "sellfillid"} <= set(key)):
        raise ValueError("not a Tradovate performance export")  # automatic pickup only trusts files with Tradovate's own columns (other brokers also have a PnL column)
    pnl_k = key.get("pnl") or key.get("profitloss") or key.get("realizedpnl") or key.get("netpnl")
    if not pnl_k:
        raise ValueError("No profit column found. In Tradovate use Account > Reports > Performance (or the trade log) and export CSV. Columns found: " + ", ".join(list(rows[0].keys())[:12]))
    g = lambda r, *names: next((r.get(key[n]) for n in names if n in key), None)  # noqa: E731
    added = skipped = 0
    total = 0.0
    with db.get_conn() as conn:
        _ensure_balance_tables(conn)
        for r in rows:
            pnl = _num(r.get(pnl_k))
            if pnl is None:
                skipped += 1
                continue
            vals = (str(g(r, "symbol", "contract", "product") or ""), _num(g(r, "qty", "quantity")), _num(g(r, "buyprice")), _num(g(r, "sellprice")), pnl,
                    str(g(r, "boughttimestamp", "boughtat", "entrytime") or ""), str(g(r, "soldtimestamp", "soldat", "exittime") or ""))
            cur = conn.execute("INSERT OR IGNORE INTO real_fills (symbol, qty, buy_price, sell_price, pnl, bought_at, sold_at) VALUES (?,?,?,?,?,?,?)", (vals[0], vals[1], vals[2], vals[3], vals[4], vals[5], vals[6]))
            if cur.rowcount:
                added += 1
                total += pnl
            else:
                skipped += 1
    return {"added": added, "skipped": skipped, "added_pnl": round(total, 2)}


def balance_summary() -> dict[str, Any]:
    with db.get_conn() as conn:
        _ensure_balance_tables(conn)
        log = [dict(r) for r in conn.execute("SELECT * FROM real_balance_log ORDER BY id DESC LIMIT 20").fetchall()]
        fills = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl), 0) s, COALESCE(SUM(pnl > 0), 0) w, MIN(sold_at) a, MAX(sold_at) b FROM real_fills").fetchone()
        acct = conn.execute("SELECT id, name, symbol, starting_balance, balance, payout_target FROM accounts WHERE traderspost_linked = 1 LIMIT 1").fetchone()
    out: dict[str, Any] = {"log": log, "fills": {"n": fills["n"], "pnl": round(fills["s"], 2), "wins": int(fills["w"]), "first": fills["a"], "last": fills["b"]}, "account": dict(acct) if acct else None}
    if log:
        out["typed"] = {"balance": log[0]["balance"], "at": log[0]["ts"]}
    if acct:
        start = acct["starting_balance"]
        out["real_from_fills"] = round(start + fills["s"], 2) if fills["n"] else None
        out["simulated"] = acct["balance"]
        ref = out["real_from_fills"] if out["real_from_fills"] is not None else (log[0]["balance"] if log else None)
        out["drift"] = round(ref - acct["balance"], 2) if ref is not None else None
    return out


def _pa() -> dict[str, Any] | None:
    try:
        from app.accounts import pa_rules

        return pa_rules.status()
    except Exception:  # noqa: BLE001 - a display extra must never break the status page
        return None


def _tracked() -> list[dict[str, Any]]:
    try:
        from app.notify import signal_tracker

        return signal_tracker.list_tracked(6)
    except Exception:  # noqa: BLE001
        return []


def status() -> dict[str, Any]:
    from app.notify import traderspost

    wh = os.environ.get(ENV["webhook"])
    return {"webhook": {"configured": bool(wh), "source": _source("webhook") if wh else "none", "masked": mask(wh, 46)}, "telegram": telegram_info(), "signals": recent_signals(15), "tracked": _tracked(), "pa": _pa(), "balance": balance_summary(),
            "ticker_example": traderspost.ticker_for("NQ", "micro")}


# ------------------------------------------------------------------ automatic CSV pickup and balance reminders
import asyncio  # noqa: E402
import hashlib  # noqa: E402
from pathlib import Path  # noqa: E402

INBOX = Path(__file__).resolve().parents[2] / "data_cache" / "tradovate_inbox"
DOWNLOADS = Path.home() / "Downloads"
NAME_RE = re.compile(r"(performance|tradovate|trade[-_ ]?log|fills)", re.I)


def inbox_paths() -> list[Path]:
    """Files worth looking at: anything in the terminal's inbox folder, plus Downloads files whose name says Performance / Tradovate / trade log / fills."""
    INBOX.mkdir(parents=True, exist_ok=True)
    out = list(INBOX.glob("*.csv"))
    try:
        out += [p for p in DOWNLOADS.glob("*.csv") if NAME_RE.search(p.name)]
    except Exception:  # noqa: BLE001
        pass
    return out


def scan_inbox() -> list[dict[str, Any]]:
    """Imports each new matching CSV once (a file is remembered by name, size and modification time). A file without a profit column is remembered and ignored."""
    try:
        seen = set(json.loads(db.meta_get("conn:inbox_seen") or "[]"))
    except Exception:  # noqa: BLE001
        seen = set()
    done: list[dict[str, Any]] = []
    for p in inbox_paths():
        try:
            st = p.stat()
            sig = hashlib.sha1(f"{p.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
            if sig in seen or st.st_size > 5_000_000 or st.st_size < 50:
                continue
            seen.add(sig)
            try:
                r = import_tradovate_csv(p.read_bytes(), strict=True)
                done.append({"file": p.name, **r})
            except ValueError:
                continue  # not a Tradovate performance file
        except Exception:  # noqa: BLE001
            logger.exception("inbox scan failed for %s", p)
    db.meta_set("conn:inbox_seen", json.dumps(sorted(seen)[-300:]))
    return done


async def inbox_loop() -> None:
    from app.notify import ops_alerts

    await asyncio.sleep(90)
    while True:
        try:
            for r in await asyncio.to_thread(scan_inbox):
                b = balance_summary()
                real = b.get("real_from_fills")
                ops_alerts.alert("csv_import", f"\U0001F4C8 Imported {r['added']} Tradovate trades from {r['file']} ({r['added_pnl']:+,.2f} USD)." + (f"\nReal balance from fills: ${real:,.2f}; the simulation says ${b['simulated']:,.2f}; difference {b['drift']:+,.2f}." if real is not None and b.get("simulated") is not None else ""), cooldown=0)
            b = await asyncio.to_thread(balance_summary)
            last = (b.get("typed") or {}).get("at") or b["fills"].get("last")
            stale = True
            if last:
                import datetime as dt

                try:
                    stale = (dt.datetime.utcnow() - dt.datetime.fromisoformat(str(last).replace("/", "-")[:19].replace(" ", "T"))).days >= 3
                except Exception:  # noqa: BLE001
                    stale = False
            if b.get("account") and stale:
                ops_alerts.alert("balance_reminder", "\u23F0 The real balance has not been recorded for 3+ days, so the terminal cannot tell if the simulation matches the broker. Open REAL ACCOUNT SETUP and type the balance, or save the Tradovate Performance CSV in your Downloads folder and it is imported automatically.", cooldown=86400)
        except Exception:  # noqa: BLE001
            logger.exception("connections inbox loop failed")
        await asyncio.sleep(60)


def sync_account_to_real(balance: float) -> dict[str, Any] | None:
    """Set the linked account's terminal balance to the real broker balance (and move its peak / trailing floor with it), so the
    terminal shows what the broker shows. Skipped while the strategy has a position open (the engine is mid-trade)."""
    from app.accounts import apex_rules

    acct = next((a for a in db.list_accounts(include_archived=False) if a.get("traderspost_linked")), None)
    if not acct or acct.get("open_position") is not None or not (balance > 0):
        return None
    peak = max(acct.get("peak_equity") or acct["starting_balance"], balance)
    floor = apex_rules.compute_floor(peak, acct["starting_balance"], acct["max_drawdown"])
    with db.get_conn() as conn:
        conn.execute("UPDATE accounts SET balance = ?, peak_equity = ?, drawdown_floor = ? WHERE id = ?", (float(balance), peak, floor, acct["id"]))
    return {"id": acct["id"], "balance": balance, "peak": peak, "floor": floor}
