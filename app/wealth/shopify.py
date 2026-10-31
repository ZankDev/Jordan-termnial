"""SHOPIFY — how much the user's store makes (read-only).

Connect: store domain (mystore.myshopify.com) + an Admin API access token (shpat_…) from a custom app created in the store:
  Shopify admin → Settings → Apps and sales channels → Develop apps → Create an app → Configure Admin API scopes:
  read_orders (and read_products for product names) → Install app → reveal the Admin API access token once.
The token is stored encrypted at rest (Windows DPAPI, wealth.security) in meta 'shopify_link' and never returned by the API.
Only GET requests to /admin/api/<ver>/orders.json and shop.json are made — nothing can be changed in the store.

Numbers (orders created in the window, cancelled orders excluded):
  gross   = sum of order subtotal before discounts (subtotal_price + total_discounts)
  sales   = sum(total_price)  (what customers paid incl. tax + shipping)
  refunds = sum of refund transactions
  net     = sales - refunds
plus orders, average order value, per-day series and top products. Store currency; also shown in ILS/USD by WEALTH.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections import defaultdict
from typing import Any

import requests

from app import db

API_VERSION = "2025-01"
META = "shopify_link"
_cache: dict[str, tuple[float, Any]] = {}


def _load() -> dict[str, Any]:
    try:
        raw = json.loads(db.meta_get(META) or "{}")
    except ValueError:
        return {}
    if raw.get("enc"):
        from app.wealth.security import decrypt_json

        try:
            return decrypt_json(raw)
        except Exception:  # noqa: BLE001
            return {}
    return raw


def _save(cfg: dict[str, Any]) -> None:
    try:
        from app.wealth.security import encrypt_json

        db.meta_set(META, json.dumps(encrypt_json(cfg)))
    except Exception:  # noqa: BLE001 - DPAPI unavailable: store plain
        db.meta_set(META, json.dumps(cfg))
    _cache.clear()


def _norm_shop(shop: str) -> str:
    s = (shop or "").strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
    if s and "." not in s:
        s += ".myshopify.com"
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*\.myshopify\.com", s):
        raise ValueError("Use your store's myshopify domain, e.g. mystore.myshopify.com")
    return s


def _client_token(shop: str, client_id: str, client_secret: str) -> tuple[str, float]:
    """Dev Dashboard apps (2026+): client-credentials grant -> an Admin API token valid ~24 h."""
    r = requests.post(f"https://{shop}/admin/oauth/access_token", timeout=30, headers={"Accept": "application/json"},
                      data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret})
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code != 200 or not j.get("access_token"):
        detail = j.get("error_description") or j.get("error") or j.get("errors") or r.text[:200]
        raise PermissionError(f"Shopify refused the app credentials ({r.status_code}): {detail}")
    return j["access_token"], time.time() + float(j.get("expires_in") or 86000)


def _fresh(cfg: dict[str, Any], force: bool = False) -> dict[str, Any]:
    """Renew the client-credentials token when it is close to expiry (static shpat_ tokens never expire)."""
    if cfg.get("client_id") and (force or not cfg.get("token") or (cfg.get("expires_at") or 0) - time.time() < 1800):
        cfg["token"], cfg["expires_at"] = _client_token(cfg["shop"], cfg["client_id"], cfg["client_secret"])
        _save(cfg)
    return cfg


def _get(cfg: dict[str, Any], path: str, params: dict[str, Any] | None = None, url: str | None = None) -> requests.Response:
    cfg = _fresh(cfg)
    r = requests.get(url or f"https://{cfg['shop']}/admin/api/{API_VERSION}/{path}", params=params,
                     headers={"X-Shopify-Access-Token": cfg["token"], "Accept": "application/json"}, timeout=30)
    if r.status_code == 401 and cfg.get("client_id"):
        cfg = _fresh(cfg, force=True)
        r = requests.get(url or f"https://{cfg['shop']}/admin/api/{API_VERSION}/{path}", params=params,
                         headers={"X-Shopify-Access-Token": cfg["token"], "Accept": "application/json"}, timeout=30)
    if r.status_code == 401:
        raise PermissionError("Shopify rejected the token (401) — check it or reinstall the app")
    if r.status_code == 403:
        raise PermissionError("The app is missing the read_orders scope (403)")
    r.raise_for_status()
    return r


def connect(shop: str, token: str | None = None, client_id: str | None = None, client_secret: str | None = None) -> dict[str, Any]:
    """Either a static Admin API token (legacy custom app, shpat_…) or a Dev Dashboard app's Client ID + Client secret."""
    shop = _norm_shop(shop)
    token, client_id, client_secret = (token or "").strip(), (client_id or "").strip(), (client_secret or "").strip()
    if client_id and client_secret:
        tok, exp = _client_token(shop, client_id, client_secret)
        cfg = {"shop": shop, "client_id": client_id, "client_secret": client_secret, "token": tok, "expires_at": exp}
    else:
        if not token.startswith(("shpat_", "shpca_", "shppa_")) or len(token) < 20:
            raise ValueError("Paste the app's Client ID + Client secret (Dev Dashboard) or an Admin API token (shpat_…)")
        cfg = {"shop": shop, "token": token}
    info = _get(cfg, "shop.json").json().get("shop", {})
    cfg.update(name=info.get("name"), currency=info.get("currency"), connected_at=time.time())
    _save(cfg)
    return status()


def disconnect() -> dict[str, Any]:
    db.meta_set(META, "")
    _cache.clear()
    return status()


def status() -> dict[str, Any]:
    c = _load()
    return {"connected": bool(c.get("token")), "shop": c.get("shop"), "name": c.get("name"), "currency": c.get("currency"),
            "method": "app credentials (auto-renewing)" if c.get("client_id") else "access token",
            "token_hint": (c["client_id"][:6] + "…" + c["client_id"][-4:]) if c.get("client_id") else ((c["token"][:6] + "…" + c["token"][-4:]) if c.get("token") else None)}


def _orders(cfg: dict[str, Any], since: dt.datetime) -> list[dict[str, Any]]:
    params = {"status": "any", "limit": 250, "created_at_min": since.isoformat(),
              "fields": "id,created_at,cancelled_at,total_price,subtotal_price,total_discounts,total_tax,currency,financial_status,refunds,line_items"}
    out, url = [], None
    for _ in range(40):  # up to 10,000 orders
        r = _get(cfg, "orders.json", params=None if url else params, url=url)
        out += r.json().get("orders", [])
        m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
        if not m:
            break
        url = m.group(1)
    return out


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def summary(days: int = 90) -> dict[str, Any]:
    cfg = _load()
    if not cfg.get("token"):
        return {"connected": False}
    key = f"s{days}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    now = dt.datetime.now(dt.timezone.utc)
    start = min(now - dt.timedelta(days=days), now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=31))
    orders = [o for o in _orders(cfg, start) if not o.get("cancelled_at")]
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {"sales": 0.0, "refunds": 0.0, "orders": 0})
    prods: dict[str, dict[str, float]] = defaultdict(lambda: {"qty": 0, "sales": 0.0})
    by_wd = [{"sales": 0.0, "orders": 0} for _ in range(7)]      # Monday = 0, store-local time
    by_hr = [{"sales": 0.0, "orders": 0} for _ in range(24)]
    extra = {"discounts": 0.0, "tax": 0.0, "units": 0, "paid": 0, "pending": 0}
    for o in orders:
        d = o["created_at"][:10]
        by_day[d]["sales"] += _f(o.get("total_price"))
        by_day[d]["orders"] += 1
        wd, hr = dt.date.fromisoformat(d).weekday(), int(o["created_at"][11:13] or 0)
        by_wd[wd]["sales"] += _f(o.get("total_price")); by_wd[wd]["orders"] += 1
        by_hr[hr]["sales"] += _f(o.get("total_price")); by_hr[hr]["orders"] += 1
        extra["discounts"] += _f(o.get("total_discounts")); extra["tax"] += _f(o.get("total_tax"))
        extra["units"] += sum(int(li.get("quantity") or 0) for li in o.get("line_items") or [])
        extra["paid" if o.get("financial_status") in ("paid", "partially_refunded", "refunded") else "pending"] += 1
        for rf in o.get("refunds") or []:
            for t in rf.get("transactions") or []:
                if t.get("kind") == "refund" and t.get("status") in (None, "success"):
                    by_day[(rf.get("created_at") or o["created_at"])[:10]]["refunds"] += _f(t.get("amount"))
        for li in o.get("line_items") or []:
            p = prods[li.get("title") or "?"]
            p["qty"] += int(li.get("quantity") or 0)
            p["sales"] += _f(li.get("price")) * int(li.get("quantity") or 0)

    def window(d0: dt.date) -> dict[str, float]:
        s = r = n = 0.0
        for d, v in by_day.items():
            if dt.date.fromisoformat(d) >= d0:
                s += v["sales"]; r += v["refunds"]; n += v["orders"]
        return {"sales": round(s, 2), "refunds": round(r, 2), "net": round(s - r, 2), "orders": int(n), "aov": round(s / n, 2) if n else None}

    today = now.date()
    prev_start = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    prev = {"sales": 0.0, "refunds": 0.0, "orders": 0}
    for d, v in by_day.items():
        dd = dt.date.fromisoformat(d)
        if prev_start <= dd < today.replace(day=1):
            prev["sales"] += v["sales"]; prev["refunds"] += v["refunds"]; prev["orders"] += v["orders"]
    series = [{"day": (today - dt.timedelta(days=i)).isoformat()} for i in range(days - 1, -1, -1)]
    for s in series:
        v = by_day.get(s["day"], {"sales": 0.0, "refunds": 0.0, "orders": 0})
        s.update(sales=round(v["sales"], 2), net=round(v["sales"] - v["refunds"], 2), orders=int(v["orders"]))
    out = {"connected": True, "shop": cfg.get("shop"), "name": cfg.get("name"), "currency": cfg.get("currency"),
           "today": window(today), "d7": window(today - dt.timedelta(days=6)), "mtd": window(today.replace(day=1)),
           "prev_month": {"sales": round(prev["sales"], 2), "net": round(prev["sales"] - prev["refunds"], 2), "orders": int(prev["orders"])},
           "d30": window(today - dt.timedelta(days=29)), f"d{days}": window(today - dt.timedelta(days=days - 1)), "series": series,
           "top_products": sorted(({"title": k, **v} for k, v in prods.items()), key=lambda p: -p["sales"])[:10], "at": time.time(),
           "by_weekday": [{"sales": round(x["sales"], 2), "orders": x["orders"]} for x in by_wd],
           "by_hour": [{"sales": round(x["sales"], 2), "orders": x["orders"]} for x in by_hr],
           "totals": {k: round(v, 2) if isinstance(v, float) else v for k, v in extra.items()}, "window_start": start.date().isoformat()}
    _cache[key] = (time.time(), out)
    return out
