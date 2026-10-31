"""Insider buying from SEC Form 4: open-market purchases (transaction code P) by officers and directors.

For each company in the universe: the SEC submissions file lists its recent Form 4s; each new one is parsed once (cached in the DB) for
non-derivative transactions with code P (a purchase in the open market; grants, option exercises and tax sales are ignored).
CLUSTER = at least 2 different insiders buying, or at least $250k bought, within 14 days. Academic work finds open-market insider purchases,
especially clusters, are followed by above-market returns on average; it is a tilt, not a guarantee.
"""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
SCHEMA = ("CREATE TABLE IF NOT EXISTS insider_tx (accession TEXT, symbol TEXT, filed TEXT, owner TEXT, role TEXT, tx_date TEXT, shares REAL, price REAL, value REAL, "
          "PRIMARY KEY (accession, owner, tx_date, shares))",
          "CREATE TABLE IF NOT EXISTS insider_seen (accession TEXT PRIMARY KEY, symbol TEXT, at INTEGER)")


def ensure() -> None:
    with db.get_conn() as conn:
        for s in SCHEMA:
            conn.execute(s)


def _get(url: str) -> requests.Response | None:
    from app.api import invest_routes as ir

    time.sleep(0.12)  # SEC asks for <= 10 requests a second
    try:
        r = requests.get(url, headers=ir.SEC_UA, timeout=20)
        return r if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def _parse(xml: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    owners = []
    for ro in root.findall("reportingOwner"):
        name = (ro.findtext("reportingOwnerId/rptOwnerName") or "").strip()
        rel = ro.find("reportingOwnerRelationship")
        role = []
        if rel is not None:
            if (rel.findtext("isDirector") or "").strip() in ("1", "true"):
                role.append("Director")
            if (rel.findtext("isOfficer") or "").strip() in ("1", "true"):
                role.append((rel.findtext("officerTitle") or "Officer").strip())
            if (rel.findtext("isTenPercentOwner") or "").strip() in ("1", "true"):
                role.append("10% owner")
        owners.append((name, ", ".join(role)))
    out = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if (tx.findtext("transactionCoding/transactionCode") or "").strip() != "P":
            continue
        try:
            sh = float(tx.findtext("transactionAmounts/transactionShares/value") or 0)
            px = float(tx.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        d = (tx.findtext("transactionDate/value") or "")[:10]
        for name, role in owners[:1]:
            out.append({"owner": name, "role": role, "tx_date": d, "shares": sh, "price": px, "value": round(sh * px, 2)})
    return out


def refresh(symbols: list[str], days: int = 21, max_filings: int = 400) -> dict[str, int]:
    """Scan recent Form 4s for these symbols (new filings only)."""
    from app.api import invest_routes as ir

    ensure()
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    parsed = found = 0
    for sym in symbols:
        ck = ir._cik(sym)
        if not ck:
            continue
        r = _get(f"https://data.sec.gov/submissions/CIK{ck[0]:010d}.json")
        if r is None:
            continue
        rec = r.json().get("filings", {}).get("recent", {})
        for form, acc, filed, doc in zip(rec.get("form", []), rec.get("accessionNumber", []), rec.get("filingDate", []), rec.get("primaryDocument", [])):
            if form != "4" or filed < cutoff:
                continue
            with db.get_conn() as conn:
                if conn.execute("SELECT 1 FROM insider_seen WHERE accession=?", (acc,)).fetchone():
                    continue
            raw = re.sub(r"^xsl[^/]+/", "", doc)  # primaryDocument points at the XSL-rendered view; the XML sits next to it
            x = _get(f"https://www.sec.gov/Archives/edgar/data/{ck[0]}/{acc.replace('-', '')}/{raw}")
            parsed += 1
            txs = []
            if x is not None:
                try:
                    txs = _parse(x.content)
                except ET.ParseError:
                    txs = []
            with db.get_conn() as conn:
                conn.execute("INSERT OR IGNORE INTO insider_seen (accession, symbol, at) VALUES (?,?,?)", (acc, sym, int(time.time())))
                for t in txs:
                    conn.execute("INSERT OR IGNORE INTO insider_tx (accession, symbol, filed, owner, role, tx_date, shares, price, value) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (acc, sym, filed, t["owner"], t["role"], t["tx_date"], t["shares"], t["price"], t["value"]))
                    found += 1
            if parsed >= max_filings:
                return {"parsed": parsed, "purchases": found}
    return {"parsed": parsed, "purchases": found}


def clusters(days: int = 14) -> list[dict[str, Any]]:
    ensure()
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM insider_tx WHERE tx_date >= ? ORDER BY tx_date DESC", (cutoff,)).fetchall()]
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["symbol"], []).append(r)
    out = []
    for sym, txs in by.items():
        owners = {t["owner"] for t in txs}
        total = sum(t["value"] for t in txs)
        out.append({"symbol": sym, "insiders": len(owners), "value": round(total), "last": max(t["tx_date"] for t in txs), "cluster": len(owners) >= 2 or total >= 250_000,
                    "buys": [{k: t[k] for k in ("owner", "role", "tx_date", "shares", "price", "value")} for t in txs[:6]]})
    out.sort(key=lambda x: (-x["cluster"], -x["value"]))
    return out
