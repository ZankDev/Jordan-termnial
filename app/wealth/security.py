"""SECURITY — daily database backups, encryption of the Polymarket wallet key at rest, optional PIN lock for money pages.

Backups: sqlite online-backup API (a consistent copy even while the server writes) to data_cache/backups/app_<date>.db,
         once a day, keeping the newest KEEP_BACKUPS. Also on demand from the page.
Wallet:  the private key file (~/.nostro/polymarket_wallet.json) is re-written encrypted with Windows DPAPI — bound to this
         Windows user on this machine, so a copied file is useless elsewhere. real_trading reads both formats; the key itself is
         never returned by any API.
PIN:     optional 4-8 digit PIN (PBKDF2-hashed in meta). When set, the money APIs (/api/wealth/*, /api/polymarket/real/*,
         /api/nostro/tradovate/*) need an unlock token (cookie) obtained with the PIN; it lasts UNLOCK_HOURS.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from app import db

BACKUP_DIR = Path(__file__).resolve().parents[2] / "data_cache" / "backups"
KEEP_BACKUPS = 14
UNLOCK_HOURS = 12
_tokens: dict[str, float] = {}


# ------------------------------------------------------------------ backups
def backup_now(tag: str = "") -> dict[str, Any]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"app_{dt.datetime.now():%Y%m%d_%H%M%S}{('_' + tag) if tag else ''}.db"
    dst = BACKUP_DIR / name
    src = sqlite3.connect(str(db.DB_PATH) if hasattr(db, "DB_PATH") else "data_cache/app.db")
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    files = sorted(BACKUP_DIR.glob("app_*.db"))
    for f in files[:-KEEP_BACKUPS]:
        try:
            f.unlink()
        except OSError:
            pass
    db.meta_set("last_backup_at", str(time.time()))
    return {"file": name, "size_mb": round(dst.stat().st_size / 1e6, 1)}


def backups() -> list[dict[str, Any]]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return [{"file": f.name, "size_mb": round(f.stat().st_size / 1e6, 1), "at": f.stat().st_mtime}
            for f in sorted(BACKUP_DIR.glob("app_*.db"), reverse=True)]


def backup_if_due() -> dict[str, Any] | None:
    last = float(db.meta_get("last_backup_at") or 0)
    if time.time() - last >= 20 * 3600:
        return backup_now("daily")
    return None


# ------------------------------------------------------------------ DPAPI (Windows)
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI is Windows-only")
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(_blob(data)), "nostro", None, None, None, 0x1, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(_blob(data)), None, None, None, None, 0x1, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def encrypt_json(obj: dict[str, Any]) -> dict[str, Any]:
    return {"enc": "dpapi", "blob": base64.b64encode(dpapi_protect(json.dumps(obj).encode())).decode(), "saved_at": time.time()}


def decrypt_json(obj: dict[str, Any]) -> dict[str, Any]:
    if obj.get("enc") != "dpapi":
        return obj
    return json.loads(dpapi_unprotect(base64.b64decode(obj["blob"])).decode())


def wallet_status() -> dict[str, Any]:
    from app.polymarket import real_trading as rt

    f = rt.WALLET_FILE
    if not f.exists():
        return {"exists": False, "encrypted": None}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exists": True, "encrypted": None}
    return {"exists": True, "encrypted": raw.get("enc") == "dpapi", "path": str(f)}


def encrypt_wallet() -> dict[str, Any]:
    """Re-write the wallet file DPAPI-encrypted (no-op when already encrypted). Verifies it decrypts to the same content."""
    from app.polymarket import real_trading as rt

    f = rt.WALLET_FILE
    if not f.exists():
        return {"ok": False, "reason": "no wallet file"}
    raw = json.loads(f.read_text(encoding="utf-8"))
    if raw.get("enc") == "dpapi":
        return {"ok": True, "already": True}
    enc = encrypt_json(raw)
    if decrypt_json(enc) != raw:
        return {"ok": False, "reason": "verification failed - left unchanged"}
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(enc), encoding="utf-8")
    os.replace(tmp, f)
    rt.reset_client()
    return {"ok": True, "already": False}


# ------------------------------------------------------------------ PIN lock
def _hash(pin: str, salt: bytes) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)).decode()


def pin_enabled() -> bool:
    return bool(db.meta_get("wealth_pin"))


def set_pin(pin: str | None, current: str | None = None) -> dict[str, Any]:
    if pin_enabled() and not check_pin(current or ""):
        raise PermissionError("current PIN is wrong")
    if not pin:
        db.meta_set("wealth_pin", "")
        return {"enabled": False}
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValueError("PIN must be 4-8 digits")
    salt = secrets.token_bytes(16)
    db.meta_set("wealth_pin", json.dumps({"salt": base64.b64encode(salt).decode(), "hash": _hash(pin, salt)}))
    return {"enabled": True}


def check_pin(pin: str) -> bool:
    raw = db.meta_get("wealth_pin")
    if not raw:
        return True
    d = json.loads(raw)
    return hmac.compare_digest(_hash(pin, base64.b64decode(d["salt"])), d["hash"])


def unlock(pin: str) -> str | None:
    if not check_pin(pin):
        return None
    t = secrets.token_urlsafe(24)
    _tokens[t] = time.time() + UNLOCK_HOURS * 3600
    return t


def token_ok(t: str | None) -> bool:
    if not pin_enabled():
        return True
    return bool(t) and _tokens.get(t, 0) > time.time()


PROTECTED = ("/api/wealth/", "/api/polymarket/real/", "/api/nostro/tradovate/")
OPEN = ("/api/wealth/security/unlock", "/api/wealth/security/status")


def status() -> dict[str, Any]:
    last = float(db.meta_get("last_backup_at") or 0)
    return {"pin_enabled": pin_enabled(), "wallet": wallet_status(), "last_backup_at": last or None, "backups": backups()[:KEEP_BACKUPS],
            "keep": KEEP_BACKUPS, "backup_dir": str(BACKUP_DIR), "server_bind": "127.0.0.1 (this computer only)"}
