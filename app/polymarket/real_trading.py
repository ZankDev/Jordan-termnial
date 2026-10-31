"""REAL-money trading on the user's own Polymarket account.

Everything else in this vertical is paper. This module is the one place that can move
real funds, so it is deliberately narrow and defensive:

  * OFF by default. Nothing trades until a wallet is connected AND the user arms it.
  * Hard caps that settings cannot exceed (HARD_MAX_STAKE, HARD_MAX_CONCURRENT).
  * A daily loss limit, a per-day trade cap, and an automatic DISARM after repeated
    order errors — a stuck or misbehaving loop must stop itself rather than keep firing.
  * Fill-and-kill market orders with a price ceiling, so a fill can never be far
    worse than the quote the decision was made on.
  * The private key lives in ~/.nostro/polymarket_wallet.json (outside the project, so it
    is never committed), is never returned by any API, and can be wiped with DISCONNECT.

Auth follows the source Node bot exactly (same CLOB v2 client, same signature types):
signature_type 1 = Polymarket email/Magic proxy wallet, 2 = browser-wallet Safe proxy,
0 = plain EOA; `funder` is the proxy/profile address that holds the USDC.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from app import db

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
WALLET_FILE = Path(os.environ.get("NOSTRO_WALLET_FILE") or (Path.home() / ".nostro" / "polymarket_wallet.json"))

HARD_MAX_STAKE = 5.0        # no setting can put more than this into one trade
HARD_MAX_CONCURRENT = 2
MAX_CONSECUTIVE_ERRORS = 3  # then the engine disarms itself

DEFAULT_SETTINGS: dict[str, Any] = {
    "armed": False,
    "stake_usd": 1.0,          # risk per trade
    "tp_min_pct": 25.0,        # take profit is clipped into [25%, 50%] of the entry price
    "tp_max_pct": 50.0,
    "max_concurrent": 1,
    "max_trades_per_day": 30,
    "daily_loss_limit": 5.0,   # stop for the day after losing this many dollars
    "max_slippage": 0.03,      # never pay more than the quoted ask + this
    "max_overround": 0.04,     # skip thin books (up_ask + down_ask - 1 above this)
    "assets": ["btc", "eth", "sol"],
    "params_source": "champion",
}

_lock = threading.RLock()
_client = None
_client_key: tuple | None = None
_state: dict[str, Any] = {"consecutive_errors": 0, "last_error": None, "last_error_at": None,
                          "balance": None, "balance_at": 0.0, "disarm_reason": None}


# ----------------------------------------------------------------------- wallet file
def _read_wallet() -> dict[str, Any]:
    try:
        raw = json.loads(WALLET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if raw.get("enc") == "dpapi":  # encrypted at rest with Windows DPAPI (wealth/security.py)
        try:
            from app.wealth.security import decrypt_json

            return decrypt_json(raw)
        except Exception:  # noqa: BLE001 - unreadable on another machine/user: behave as not connected
            return {}
    return raw


def save_wallet(private_key: str, funder: str, signature_type: int) -> None:
    key = private_key.strip()
    if not key.startswith("0x"):
        key = "0x" + key
    WALLET_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"private_key": key, "funder": funder.strip(), "signature_type": int(signature_type), "saved_at": time.time()}
    try:  # encrypt at rest (Windows DPAPI); fall back to plain JSON where DPAPI is unavailable
        from app.wealth.security import encrypt_json

        payload = encrypt_json(data)
    except Exception:  # noqa: BLE001
        payload = data
    WALLET_FILE.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(WALLET_FILE, 0o600)
    except OSError:
        pass
    reset_client()


def import_from_bot_env() -> dict[str, Any]:
    """Copy PRIVATE_KEY / FUNDER_ADDRESS / SIGNATURE_TYPE out of the Node bot's .env — the
    same account that bot trades. Runs server-side so the key is never shown or sent anywhere."""
    env = Path(__file__).resolve().parents[2] / "PolyMarket" / ".env"
    if not env.exists():
        raise ValueError("PolyMarket/.env not found")
    vals: dict[str, str] = {}
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
    if not vals.get("PRIVATE_KEY"):
        raise ValueError("no PRIVATE_KEY set in PolyMarket/.env")
    save_wallet(vals["PRIVATE_KEY"], vals.get("FUNDER_ADDRESS", ""), int(vals.get("SIGNATURE_TYPE") or 1))
    return status()


def disconnect() -> None:
    """Wipe the stored key and disarm."""
    with _lock:
        try:
            WALLET_FILE.unlink()
        except OSError:
            pass
        reset_client()
        set_settings({"armed": False})


def reset_client() -> None:
    global _client, _client_key
    with _lock:
        _client, _client_key = None, None
        _state.update(balance=None, balance_at=0.0)


def _signer_address(pk: str) -> str:
    from eth_account import Account

    return Account.from_key(pk).address


def is_connected() -> bool:
    return bool(_read_wallet().get("private_key"))


# ----------------------------------------------------------------------- settings
def get_settings() -> dict[str, Any]:
    raw = db.meta_get("pm_real_settings")
    try:
        saved = json.loads(raw) if raw else {}
    except ValueError:
        saved = {}
    return {**DEFAULT_SETTINGS, **saved}


def set_settings(patch: dict[str, Any]) -> dict[str, Any]:
    cur = get_settings()
    for k, v in patch.items():
        if k in DEFAULT_SETTINGS:
            cur[k] = v
    # hard clamps — whatever the UI sends
    cur["stake_usd"] = max(1.0, min(HARD_MAX_STAKE, float(cur["stake_usd"])))
    cur["max_concurrent"] = max(1, min(HARD_MAX_CONCURRENT, int(cur["max_concurrent"])))
    cur["tp_min_pct"] = max(5.0, float(cur["tp_min_pct"]))
    cur["tp_max_pct"] = max(cur["tp_min_pct"], float(cur["tp_max_pct"]))
    cur["daily_loss_limit"] = max(0.0, float(cur["daily_loss_limit"]))
    cur["max_slippage"] = max(0.0, min(0.10, float(cur["max_slippage"])))
    cur["assets"] = [a for a in cur["assets"] if isinstance(a, str)]
    if cur.get("armed") and not is_connected():
        cur["armed"] = False
    db.meta_set("pm_real_settings", json.dumps(cur))
    return cur


def disarm(reason: str) -> None:
    logger.warning("REAL trading disarmed: %s", reason)
    _state["disarm_reason"] = reason
    set_settings({"armed": False})
    try:
        from app.polymarket import notify

        notify.notify_real_event(f"REAL TRADING DISARMED — {reason}")
    except Exception:  # noqa: BLE001
        logger.exception("disarm notify failed")


# ----------------------------------------------------------------------- CLOB client
def get_client():
    """A cached authenticated CLOB v2 client (API creds derived from the wallet key)."""
    global _client, _client_key
    with _lock:
        w = _read_wallet()
        if not w.get("private_key"):
            raise RuntimeError("no wallet connected")
        ident = (w["private_key"], w.get("funder"), w.get("signature_type"))
        if _client is not None and _client_key == ident:
            return _client
        from py_clob_client_v2 import ClobClient

        st = int(w.get("signature_type") if w.get("signature_type") is not None else 1)
        funder = w.get("funder") or None
        boot = ClobClient(CLOB_HOST, CHAIN_ID, key=w["private_key"], signature_type=st, funder=funder)
        creds = None
        for attempt in range(4):
            try:
                creds = boot.create_or_derive_api_key()
                if creds is not None and (getattr(creds, "api_secret", None) or getattr(creds, "secret", None)):
                    break
            except Exception as e:  # noqa: BLE001
                logger.warning("CLOB credential derivation failed (%s): %s", attempt + 1, e)
            creds = None
            time.sleep(1.5 * (attempt + 1))
        if creds is None:
            raise RuntimeError("could not derive CLOB API credentials (Polymarket unreachable, or region-blocked)")
        _client = ClobClient(CLOB_HOST, CHAIN_ID, key=w["private_key"], creds=creds, signature_type=st, funder=funder,
                             retry_on_error=True)
        _client_key = ident
        return _client


def balance(force: bool = False) -> float | None:
    """USDC collateral in the trading account (cached 20 s)."""
    now = time.time()
    if not force and _state["balance"] is not None and now - _state["balance_at"] < 20:
        return _state["balance"]
    from py_clob_client_v2 import AssetType, BalanceAllowanceParams

    try:
        b = get_client().get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        val = float(b.get("balance")) / 1e6 if isinstance(b, dict) else None
    except Exception as e:  # noqa: BLE001
        _state["last_error"], _state["last_error_at"] = f"balance: {e}", now
        return _state["balance"]
    _state.update(balance=val, balance_at=now)
    return val


def status(check_balance: bool = True) -> dict[str, Any]:
    w = _read_wallet()
    s = get_settings()
    out: dict[str, Any] = {
        "connected": bool(w.get("private_key")), "signature_type": w.get("signature_type"),
        "funder": w.get("funder"), "settings": s, "consecutive_errors": _state["consecutive_errors"],
        "last_error": _state["last_error"], "last_error_at": _state["last_error_at"],
        "disarm_reason": _state["disarm_reason"], "hard_max_stake": HARD_MAX_STAKE,
        "balance": _state["balance"], "reachable": None, "address": None,
    }
    if w.get("private_key"):
        try:
            out["address"] = _signer_address(w["private_key"])
        except Exception as e:  # noqa: BLE001
            out["last_error"] = f"invalid private key: {e}"
            return out
        if check_balance:
            try:
                bal = balance(force=True)
                out["balance"], out["reachable"] = bal, bal is not None
            except Exception as e:  # noqa: BLE001
                out["last_error"], out["reachable"] = str(e), False
    out["today"] = today_summary()
    out["open_positions"] = list_trades(open_only=True)
    return out


# ----------------------------------------------------------------------- orders
def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _record_error(msg: str) -> None:
    _state["consecutive_errors"] += 1
    _state["last_error"], _state["last_error_at"] = msg, time.time()
    if _state["consecutive_errors"] >= MAX_CONSECUTIVE_ERRORS and get_settings()["armed"]:
        disarm(f"{MAX_CONSECUTIVE_ERRORS} order errors in a row — last: {msg[:160]}")


def _is_no_match(text: str) -> bool:
    t = text.lower()
    return "no orders found to match" in t or "fak orders are partially filled or killed" in t


def _ok() -> None:
    _state["consecutive_errors"] = 0


def buy(token_id: str, usd: float, max_price: float) -> dict[str, Any]:
    """Fill-and-kill market BUY of `usd` dollars, never above `max_price` per share.
    Returns {ok, shares, spent, avg_price, raw, error}."""
    from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

    try:
        resp = get_client().create_and_post_market_order(
            MarketOrderArgs(token_id=token_id, amount=float(usd), side=Side.BUY,
                            price=max(0.001, min(0.99, float(max_price))), order_type=OrderType.FAK),
            None, OrderType.FAK)
    except Exception as e:  # noqa: BLE001
        if _is_no_match(str(e)):
            # Not a failure: a fill-and-kill order that found nothing at/under the price ceiling is
            # simply killed (no fill, no cost) — the book moved. Must not count toward auto-disarm.
            return {"ok": True, "shares": 0.0, "spent": 0.0, "avg_price": None, "error": "no fill (nothing at or under the price ceiling)"}
        _record_error(f"buy failed: {e}")
        return {"ok": False, "error": str(e)}
    if not isinstance(resp, dict) or resp.get("success") is False or resp.get("error") or resp.get("errorMsg"):
        err = json.dumps(resp)[:300] if resp is not None else "empty response"
        if _is_no_match(err):
            return {"ok": True, "shares": 0.0, "spent": 0.0, "avg_price": None, "error": "no fill (nothing at or under the price ceiling)"}
        _record_error(f"buy rejected: {err}")
        return {"ok": False, "error": err, "raw": resp}
    shares, spent = _num(resp.get("takingAmount")), _num(resp.get("makingAmount")) or float(usd)
    _ok()
    if shares <= 0:
        return {"ok": True, "shares": 0.0, "spent": 0.0, "avg_price": None, "raw": resp, "error": "no fill (price moved)"}
    return {"ok": True, "shares": shares, "spent": spent, "avg_price": spent / shares, "raw": resp}


def sell(token_id: str, shares: float, min_price: float | None = None) -> dict[str, Any]:
    """Fill-and-kill market SELL of `shares`; min_price=None accepts any price (used for the
    last attempt — getting out matters more than the last cent)."""
    from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

    try:
        resp = get_client().create_and_post_market_order(
            MarketOrderArgs(token_id=token_id, amount=float(shares), side=Side.SELL,
                            price=max(0.001, min(0.99, float(min_price))) if min_price else 0.001, order_type=OrderType.FAK),
            None, OrderType.FAK)
    except Exception as e:  # noqa: BLE001
        _record_error(f"sell failed: {e}")
        return {"ok": False, "error": str(e)}
    if not isinstance(resp, dict) or resp.get("success") is False or resp.get("error") or resp.get("errorMsg"):
        err = json.dumps(resp)[:300] if resp is not None else "empty response"
        _record_error(f"sell rejected: {err}")
        return {"ok": False, "error": err, "raw": resp}
    sold, got = _num(resp.get("makingAmount")), _num(resp.get("takingAmount"))
    _ok()
    return {"ok": True, "shares": sold, "proceeds": got, "avg_price": (got / sold) if sold else None, "raw": resp}


# ----------------------------------------------------------------------- ledger
def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def open_trade(sess: dict[str, Any], side: str, token_id: str, fill: dict[str, Any], strike: float | None,
               flip_prob: float | None, params_hash: str | None) -> int:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO polymarket_real_trades (session_slug, asset, side, token_id, status, opened_at, start_ts, end_ts, "
            "strike, entry_price, shares, cost, flip_prob, params_hash) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sess["slug"], sess["asset"], side, token_id, _now(), sess["start_ts"], sess["end_ts"], strike,
             fill["avg_price"], fill["shares"], fill["spent"], flip_prob, params_hash),
        )
        return cur.lastrowid


def close_trade(trade_id: int, exit_price: float, proceeds: float, reason: str, estimate: bool = False,
                peak_pct: float | None = None, note: str | None = None) -> dict[str, Any]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM polymarket_real_trades WHERE id = ?", (trade_id,)).fetchone()
        pnl = proceeds - row["cost"]
        conn.execute(
            "UPDATE polymarket_real_trades SET status='closed', closed_at=?, exit_price=?, proceeds=?, pnl=?, exit_reason=?, "
            "settled_estimate=?, peak_pct=?, note=? WHERE id=?",
            (_now(), exit_price, proceeds, pnl, reason, int(estimate), peak_pct, note, trade_id),
        )
        return dict(conn.execute("SELECT * FROM polymarket_real_trades WHERE id = ?", (trade_id,)).fetchone())


def get_trade(trade_id: int) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM polymarket_real_trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None


def set_peak(trade_id: int, peak_pct: float | None) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE polymarket_real_trades SET peak_pct = ? WHERE id = ?", (peak_pct, trade_id))


def reduce_position(trade_id: int, shares: float, cost: float) -> None:
    """After a partial sell: the open row keeps only the unsold shares and their cost."""
    with db.get_conn() as conn:
        conn.execute("UPDATE polymarket_real_trades SET shares = ?, cost = ? WHERE id = ?", (shares, cost, trade_id))


def record_partial(row: dict[str, Any], sold: float, cost_sold: float, proceeds: float, reason: str) -> None:
    """The sold part of a partially-filled exit, booked as its own closed row."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO polymarket_real_trades (session_slug, asset, side, token_id, status, opened_at, closed_at, start_ts, "
            "end_ts, strike, entry_price, shares, cost, exit_price, proceeds, pnl, exit_reason, note, params_hash) "
            "VALUES (?, ?, ?, ?, 'closed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'partial exit', ?)",
            (row["session_slug"], row["asset"], row["side"], row["token_id"], row["opened_at"], _now(), row["start_ts"],
             row["end_ts"], row["strike"], row["entry_price"], sold, cost_sold, (proceeds / sold) if sold else 0.0, proceeds,
             proceeds - cost_sold, reason, row.get("params_hash")),
        )


def list_trades(open_only: bool = False, limit: int = 60) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        q = "SELECT * FROM polymarket_real_trades" + (" WHERE status='open'" if open_only else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in conn.execute(q, (limit,)).fetchall()]


def today_summary() -> dict[str, Any]:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT pnl, status FROM polymarket_real_trades WHERE substr(opened_at,1,10) = ?", (today,)).fetchall()
        allrows = conn.execute("SELECT pnl FROM polymarket_real_trades WHERE status='closed'").fetchall()
    closed = [r["pnl"] for r in rows if r["status"] == "closed" and r["pnl"] is not None]
    s = get_settings()
    pnl = sum(closed)
    return {"trades": len(rows), "closed": len(closed), "pnl": round(pnl, 4),
            "wins": sum(1 for p in closed if p > 0), "loss_remaining": round(max(0.0, s["daily_loss_limit"] + min(0.0, pnl)), 2),
            "all_time_pnl": round(sum(r["pnl"] for r in allrows if r["pnl"] is not None), 4),
            "all_time_trades": len(allrows)}


def can_trade_now() -> tuple[bool, str]:
    s = get_settings()
    if not s["armed"]:
        return False, "not armed"
    if not is_connected():
        return False, "no wallet"
    t = today_summary()
    if t["pnl"] <= -s["daily_loss_limit"]:
        disarm(f"daily loss limit reached (${t['pnl']:.2f} today)")
        return False, "daily loss limit"
    if t["trades"] >= s["max_trades_per_day"]:
        return False, "daily trade cap"
    return True, "ok"
