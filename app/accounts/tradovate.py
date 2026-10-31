"""Read-only Tradovate link: OAuth sign-in, then the real account's cash balance / net liquidation / P&L.

Nothing here can place or cancel an order — it only calls the auth endpoints and the account/cash-balance
READ endpoints, so connecting a live account to the terminal cannot trade it.

Flow (OAuth authorization code, the one Tradovate documents for third-party apps):
  1. connect()  saves the app credentials (client id / secret / redirect uri) and returns the sign-in URL;
  2. the user approves on Tradovate, which redirects to /api/nostro/tradovate/callback?code=...;
  3. exchange_code() POSTs the code to  <host>/v1/auth/oauthtoken  (falls back to <host>/auth/oauthtoken)
     and stores the access token + expiry;
  4. balances() reads  /account/list  and  /cashBalance/getcashbalancesnapshot  for every account,
     cached for CACHE_SECONDS. A token close to expiry is renewed automatically; if that is impossible the
     status flips to "reauthorize" and the page shows a one-click RECONNECT.
A raw access token can also be pasted (set_token) — handy for a first test without the OAuth app.

Storage: everything lives in the local SQLite `meta` table (one JSON blob). Secrets are never returned by
status(); only masked hints are.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any
from urllib.parse import urlencode

import requests

from app import db

META_KEY = "tradovate_link"
HOSTS = {"live": "https://live.tradovateapi.com", "demo": "https://demo.tradovateapi.com"}
AUTHORIZE_URL = "https://trader.tradovate.com/oauth"
DEFAULT_REDIRECT = "http://localhost:8000/api/nostro/tradovate/callback"
TIMEOUT = 12
CACHE_SECONDS = 20
RENEW_MARGIN = 600            # renew a token that expires within this many seconds

_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "data": None}


# ------------------------------------------------------------------ storage
def _load() -> dict[str, Any]:
    raw = db.meta_get(META_KEY)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _save(cfg: dict[str, Any]) -> None:
    db.meta_set(META_KEY, json.dumps(cfg))
    _cache.update(ts=0.0, data=None)


def _mask(s: str | None) -> str | None:
    if not s:
        return None
    return (s[:3] + "…" + s[-3:]) if len(s) > 8 else "•••"


def _host(cfg: dict[str, Any]) -> str:
    return HOSTS.get(cfg.get("env") or "live", HOSTS["live"])


def is_connected() -> bool:
    cfg = _load()
    return bool(cfg.get("access_token"))


def status() -> dict[str, Any]:
    cfg = _load()
    exp = cfg.get("expires_at") or 0
    connected = bool(cfg.get("access_token"))
    if not connected:
        state = "configured" if cfg.get("client_id") else "not_connected"
    elif exp and exp < time.time():
        state = "reauthorize"
    else:
        state = "connected"
    return {
        "state": state,
        "env": cfg.get("env") or "live",
        "client_id": cfg.get("client_id"),
        "client_secret_hint": _mask(cfg.get("client_secret")),
        "redirect_uri": cfg.get("redirect_uri") or DEFAULT_REDIRECT,
        "token_hint": _mask(cfg.get("access_token")),
        "expires_in": int(exp - time.time()) if exp else None,
        "connected_at": cfg.get("connected_at"),
        "last_error": cfg.get("last_error"),
        "authorize_url": _authorize_url(cfg) if cfg.get("client_id") else None,
    }


# ------------------------------------------------------------------ connect / auth
def _authorize_url(cfg: dict[str, Any]) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code", "client_id": cfg["client_id"],
        "redirect_uri": cfg.get("redirect_uri") or DEFAULT_REDIRECT,
    })


def connect(client_id: str, client_secret: str, redirect_uri: str | None, env: str) -> dict[str, Any]:
    """Save the OAuth app credentials; returns the URL the user must open to approve access."""
    client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
    if client_secret == "__keep__":  # the form leaves the secret blank once it is saved
        client_secret = _load().get("client_secret") or ""
    if not client_id or not client_secret:
        raise ValueError("Client ID and client secret are both required (Tradovate → Application Settings → API access).")
    if env not in HOSTS:
        raise ValueError("env must be 'live' or 'demo'")
    cfg = _load()
    keep = {k: cfg[k] for k in ("access_token", "expires_at", "refresh_token", "connected_at") if k in cfg} \
        if cfg.get("client_id") == client_id else {}
    cfg = {**keep, "client_id": client_id, "client_secret": client_secret, "env": env,
           "redirect_uri": (redirect_uri or DEFAULT_REDIRECT).strip(), "last_error": None}
    _save(cfg)
    return status()


def _post_token(host: str, payload: dict[str, str]) -> dict[str, Any]:
    """POST the token endpoint; tries the /v1 path first (as documented for this app), then the bare one."""
    last = ""
    for path in ("/v1/auth/oauthtoken", "/auth/oauthtoken"):
        try:
            r = requests.post(host + path, data=payload, headers={"Accept": "application/json"}, timeout=TIMEOUT)
        except requests.RequestException as e:
            last = f"network error: {e}"
            continue
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 404:
            last = f"{path} → 404"
            continue
        if r.ok and isinstance(body, dict) and body.get("access_token"):
            return body
        detail = (body.get("error_description") or body.get("errorText") or body.get("error") or r.text[:160]) if isinstance(body, dict) else r.text[:160]
        raise RuntimeError(f"Tradovate refused the sign-in ({r.status_code}): {detail}")
    raise RuntimeError(f"Could not reach the Tradovate token endpoint ({last})")


def _store_token(cfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    ttl = float(body.get("expires_in") or 4800)
    cfg.update(access_token=body["access_token"], expires_at=time.time() + ttl, last_error=None,
               connected_at=cfg.get("connected_at") or time.strftime("%Y-%m-%d %H:%M:%S"))
    if body.get("refresh_token"):
        cfg["refresh_token"] = body["refresh_token"]
    _save(cfg)
    return cfg


def exchange_code(code: str) -> dict[str, Any]:
    cfg = _load()
    if not cfg.get("client_id"):
        raise ValueError("Save your Client ID and secret first.")
    body = _post_token(_host(cfg), {
        "grant_type": "authorization_code", "code": code, "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"], "redirect_uri": cfg.get("redirect_uri") or DEFAULT_REDIRECT,
    })
    _store_token(cfg, body)
    return status()


def set_token(token: str, env: str = "live") -> dict[str, Any]:
    """Paste-a-token fallback (no expiry known, so it is treated as valid for 75 minutes)."""
    token = (token or "").strip().removeprefix("Bearer ").strip()
    if len(token) < 20:
        raise ValueError("That does not look like an access token.")
    cfg = _load()
    cfg.update(env=env if env in HOSTS else "live")
    cfg = _store_token(cfg, {"access_token": token, "expires_in": 4500})
    return status()


def disconnect() -> dict[str, Any]:
    cfg = _load()
    keep = {k: cfg[k] for k in ("client_id", "client_secret", "env", "redirect_uri") if k in cfg}
    _save(keep)
    return status()


def _renew(cfg: dict[str, Any]) -> bool:
    """Try to extend the session without the user. Returns True if a fresh token is now stored."""
    host = _host(cfg)
    try:
        if cfg.get("refresh_token") and cfg.get("client_id"):
            body = _post_token(host, {"grant_type": "refresh_token", "refresh_token": cfg["refresh_token"],
                                      "client_id": cfg["client_id"], "client_secret": cfg.get("client_secret", "")})
            _store_token(cfg, body)
            return True
        r = requests.get(host + "/v1/auth/renewaccesstoken", headers={"Authorization": f"Bearer {cfg['access_token']}"}, timeout=TIMEOUT)
        j = r.json() if r.ok else {}
        tok = j.get("accessToken") or j.get("access_token")
        if tok:
            _store_token(cfg, {"access_token": tok, "expires_in": 4500})
            return True
    except Exception as e:  # noqa: BLE001
        cfg["last_error"] = f"token renewal failed: {e}"
        _save(cfg)
    return False


# ------------------------------------------------------------------ read the real account
def _get(cfg: dict[str, Any], path: str) -> Any:
    r = requests.get(_host(cfg) + "/v1" + path, headers={"Authorization": f"Bearer {cfg['access_token']}", "Accept": "application/json"}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise PermissionError("Tradovate rejected the token (expired or revoked)")
    r.raise_for_status()
    return r.json()


def _post(cfg: dict[str, Any], path: str, body: dict[str, Any]) -> Any:
    r = requests.post(_host(cfg) + "/v1" + path, json=body, headers={"Authorization": f"Bearer {cfg['access_token']}", "Accept": "application/json"}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise PermissionError("Tradovate rejected the token (expired or revoked)")
    r.raise_for_status()
    return r.json()


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _shape(acct: dict[str, Any], snap: dict[str, Any] | None) -> dict[str, Any]:
    snap = snap or {}
    cash = _num(snap.get("totalCashValue"))
    if cash is None:
        cash = _num(snap.get("cashBalance"))
    return {
        "id": acct.get("id"), "name": acct.get("name"), "active": acct.get("active", True),
        "cash_balance": cash,
        "net_liq": _num(snap.get("netLiq")) if snap.get("netLiq") is not None else _num(snap.get("totalCashValue")),
        "realized_pnl": _num(snap.get("realizedPnL")), "open_pnl": _num(snap.get("openPnL")),
        "week_pnl": _num(snap.get("weekRealizedPnL")),
        "margin_used": _num(snap.get("initialMargin")), "day_pnl": (_num(snap.get("realizedPnL")) or 0.0) + (_num(snap.get("openPnL")) or 0.0),
    }


def fills(account_id: str, force: bool = False) -> dict[str, Any]:
    """{'state', 'fills': [...], 'error'} for one real sub-account — never raises, same
    contract as balances(). Read-only (GET), same as every other function in this module.

    UNVERIFIED: this repo has never called a Tradovate order/fill endpoint before now.
    /v1/fill/list is Tradovate's documented list-all-fills endpoint; it is NOT scoped to
    one account server-side, so results are filtered client-side by accountId below. If
    this 404s or the field names don't match (expected: id, accountId, orderId, action,
    symbol via contract lookup, price, qty, timestamp), check Tradovate's current API
    reference and adjust before trusting this for anything beyond display."""
    with _lock:
        cfg = _load()
        if not cfg.get("access_token"):
            return {"state": status()["state"], "fills": [], "error": None}
        if (cfg.get("expires_at") or 0) - time.time() < RENEW_MARGIN:
            _renew(cfg)
            cfg = _load()
        try:
            raw = _get(cfg, "/fill/list") or []
            rows = [f for f in raw if str(f.get("accountId")) == str(account_id)]
            rows.sort(key=lambda f: f.get("timestamp") or "", reverse=True)
            return {"state": "connected", "fills": rows, "error": None}
        except PermissionError as e:
            cfg["last_error"], cfg["expires_at"] = str(e), 1.0
            _save(cfg)
            return {"state": "reauthorize", "fills": [], "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"state": "connected", "fills": [], "error": f"Tradovate fills unreachable: {e}"}


def balances(force: bool = False) -> dict[str, Any]:
    """{'state', 'accounts': [...], 'error'} — never raises; the page renders whatever state comes back."""
    with _lock:
        if not force and _cache["data"] is not None and time.time() - _cache["ts"] < CACHE_SECONDS:
            return _cache["data"]
        cfg = _load()
        if not cfg.get("access_token"):
            return {"state": status()["state"], "accounts": [], "error": None}
        if (cfg.get("expires_at") or 0) - time.time() < RENEW_MARGIN:
            _renew(cfg)
            cfg = _load()
        out: dict[str, Any] = {"state": "connected", "accounts": [], "error": None, "env": cfg.get("env") or "live", "ts": time.time()}
        try:
            for a in _get(cfg, "/account/list") or []:
                try:
                    snap = _post(cfg, "/cashBalance/getcashbalancesnapshot", {"accountId": a["id"]})
                except PermissionError:
                    raise
                except Exception as e:  # noqa: BLE001
                    snap, out["error"] = None, f"balance for {a.get('name')}: {e}"
                out["accounts"].append(_shape(a, snap))
            if cfg.get("last_error"):
                cfg["last_error"] = None
                _save(cfg)
        except PermissionError as e:
            out.update(state="reauthorize", error=str(e))
            cfg["last_error"], cfg["expires_at"] = str(e), 1.0  # mark the token dead so status() asks for a reconnect
            _save(cfg)
        except Exception as e:  # noqa: BLE001
            out.update(error=f"Tradovate unreachable: {e}")
        _cache.update(ts=time.time(), data=out)
        return out
