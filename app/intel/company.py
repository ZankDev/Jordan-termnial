"""Company name (as the AI reads it in a story) -> the listed stock on its home exchange, worldwide.

The AI gives the company and its home exchange code (TSE, HKEX, XETRA, TASE...). Yahoo search returns candidates; the one on the
home exchange (by Yahoo suffix) whose name matches best wins, plus the US listing (ADR or primary) when there is one, because it
trades while the home market is closed. Results are cached in memory and in meta (company_symbols) so each name is searched once.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from difflib import SequenceMatcher
from typing import Any

from app import db

logger = logging.getLogger(__name__)
SUFFIX = {"TSE": [".T"], "SSE": [".SS"], "SZSE": [".SZ"], "HKEX": [".HK"], "KRX": [".KS"], "KOSDAQ": [".KQ"], "TWSE": [".TW", ".TWO"], "NSE": [".NS"], "BSE": [".BO"],
          "SGX": [".SI"], "BURSA": [".KL"], "IDX": [".JK"], "SET": [".BK"], "PSE": [".PS"], "HOSE": [".VN"], "ASX": [".AX"], "NZX": [".NZ"], "TASE": [".TA"],
          "TADAWUL": [".SR"], "DFM": [".AE"], "ADX": [".AD", ".AE"], "QSE": [".QA"], "BK": [".KW"], "EGX": [".CA"], "JSE": [".JO"], "LSE": [".L"], "XETRA": [".DE", ".F"],
          "EPA": [".PA"], "AMS": [".AS"], "BRU": [".BR"], "LIS": [".LS"], "DUB": [".IR"], "MIL": [".MI"], "OSL": [".OL"], "BME": [".MC"], "SIX": [".SW"], "VIE": [".VI"],
          "STO": [".ST"], "CPH": [".CO"], "HEL": [".HE"], "TAL": [".TL"], "RIG": [".RG"], "VIL": [".VS"], "WSE": [".WA"], "ATH": [".AT"], "BIST": [".IS"], "TSX": [".TO", ".V"],
          "BMV": [".MX"], "B3": [".SA"], "BYMA": [".BA"], "BCS": [".SN"], "BVC": [".CL"], "BVL": [".LM"], "NYSE": [""], "NASDAQ": [""]}
ALIASES = {"BOVESPA": "B3", "BMFBOVESPA": "B3", "SAUDI": "TADAWUL", "SAU": "TADAWUL", "KOSPI": "KRX", "KSE": "KRX", "JPX": "TSE", "TYO": "TSE", "TOKYO": "TSE",
           "HKSE": "HKEX", "HKG": "HKEX", "FRA": "XETRA", "FWB": "XETRA", "ETR": "XETRA", "LON": "LSE", "EURONEXT": "EPA", "PAR": "EPA", "SWX": "SIX", "TLV": "TASE",
           "TWSE": "TWSE", "TPE": "TWSE", "SHA": "SSE", "SHE": "SZSE", "NSEI": "NSE", "BOM": "BSE", "SGX": "SGX", "IDX": "IDX", "ASX": "ASX", "TSXV": "TSX", "AMEX": "NYSE",
           "NYSEARCA": "NYSE", "ARCA": "NYSE", "NASDAQGS": "NASDAQ", "NMS": "NASDAQ", "JSE": "JSE", "MOEX": "", "BIT": "MIL", "MCE": "BME", "STO": "STO", "OMX": "STO"}
LIKELY = {"digits4": ["TADAWUL", "TSE", "TWSE", "HKEX"], "digits6": ["KRX", "KOSDAQ", "SSE", "SZSE"], "digits5": ["HKEX"],
          "alnum": ["B3"], "alpha": ["LSE", "XETRA", "EPA", "AMS", "SIX", "MIL", "BME", "TSX", "ASX", "TASE", "STO", "CPH", "HEL", "OSL", "NSE", "JSE", "NYSE"]}
US_EXCH = {"NYSE", "NASDAQ", "NYQ", "NMS", "NGM", "NCM", "ASE", "NYSEArca", "PCX", "BTS"}
_lock = threading.Lock()
_mem: dict[str, Any] | None = None
_STRIP = re.compile(r"\b(inc|corp|corporation|co|ltd|limited|plc|ag|sa|se|nv|spa|ab|asa|oyj|group|holdings?|the|company|class [a-z])\b\.?", re.I)


def _norm(s: str) -> str:
    return " ".join(_STRIP.sub(" ", (s or "").lower()).replace(",", " ").replace(".", " ").split())


def _cache() -> dict[str, Any]:
    global _mem
    if _mem is None:
        try:
            _mem = json.loads(db.meta_get("company_symbols") or "{}")
        except ValueError:
            _mem = {}
    return _mem


def _save() -> None:
    try:
        db.meta_set("company_symbols", json.dumps(_cache()))
    except Exception:  # noqa: BLE001
        logger.exception("company cache save failed")


def _matches(name: str, listed: str) -> bool:
    a, b = _norm(name), _norm(listed)
    initials = "".join(w[0] for w in b.split() if w)
    return bool(a) and (a in b or a.replace(" ", "") in (initials, initials + "c") or SequenceMatcher(None, a, b).ratio() >= 0.5)


def _direct(ticker: str, exch: str, name: str) -> dict[str, Any] | None:
    """The AI's ticker guess: on its home exchange first, then on the exchanges that use that ticker format.
    Accepted only if Yahoo has prices AND the listed company name matches the company in the story."""
    from app.api import invest_routes as ir

    t = re.sub(r"[^A-Za-z0-9\-]", "", str(ticker or "")).upper()
    if not t:
        return None
    kind = ("digits" + str(len(t))) if t.isdigit() else "alpha" if t.replace("-", "").isalpha() else "alnum"
    order = ([exch] if exch in SUFFIX else []) + [x for x in LIKELY.get(kind, []) if x != exch]
    for ex in order[:6]:
        for suf in SUFFIX[ex]:
            sym = t + suf
            try:
                h = ir._history(sym, "5d", "1d")
            except Exception:  # noqa: BLE001
                continue
            if not h["candles"]:
                continue
            m = h["meta"]
            listed = m.get("longName") or m.get("shortName") or sym
            if _matches(name, listed):
                return {"name": listed, "symbol": sym, "exchange": m.get("fullExchangeName") or ex}
    return None


def resolve(name: str, exch: str | None = None, ticker: str | None = None) -> dict[str, Any] | None:
    """{name, symbol (home listing), exchange, us (US listing or None)} or None if no listed company matches."""
    from app.api import invest_routes as ir

    name = " ".join(str(name or "").split())[:80]
    if len(name) < 2:
        return None
    exch = (exch or "").upper().strip().replace(" ", "")
    exch = ALIASES.get(exch, exch)
    key = f"{_norm(name)}|{exch}|{str(ticker or '').upper()}"
    with _lock:
        c = _cache()
        if key in c:
            return c[key]
    try:
        res = ir.search(name, 10)["results"]
    except Exception:  # noqa: BLE001
        res = []
    tk = re.sub(r"[^A-Za-z0-9\-]", "", str(ticker or "")).upper()
    confirmed = next((r for r in res if r.get("type") == "EQUITY" and tk and r["symbol"].split(".")[0].upper() == tk
                      and not (r.get("exchange") or "").startswith("OTC")), None)  # search for the name returned exactly the AI's ticker
    direct = ({"name": confirmed.get("name") or name, "symbol": confirmed["symbol"], "exchange": confirmed.get("exchange")} if confirmed
              else _direct(ticker, exch, name) if ticker else None)
    eq = [r for r in res if r.get("type") == "EQUITY"]
    if direct:
        us = next((r for r in eq if "." not in r["symbol"] and not (r.get("exchange") or "").startswith("OTC") and r["symbol"] != direct["symbol"]
                   and SequenceMatcher(None, _norm(direct["name"]), _norm(r.get("name", ""))).ratio() > 0.6), None)
        val = {**direct, "us": us["symbol"] if us else None}
    elif not eq:
        val = None
    else:
        target = _norm(name)

        def score(r: dict[str, Any]) -> float:
            s = SequenceMatcher(None, target, _norm(r.get("name", ""))).ratio()
            if target and target in _norm(r.get("name", "")):
                s += 0.3
            sym = r["symbol"]
            if exch and exch in SUFFIX:
                suf = SUFFIX[exch]
                if (suf == [""] and "." not in sym) or any(sym.endswith(x) for x in suf if x):
                    s += 0.6
            if "." in sym and sym.rsplit(".", 1)[1] in ("F", "SG", "MU", "DU", "BE", "HM", "IL", "VI") and exch not in ("XETRA", "VIE"):
                s -= 0.4  # secondary German/IOB lines
            if sym.endswith("F.SA"):
                s -= 0.5  # B3 fractional-lot line
            if _norm(r.get("name", "")) == target:
                s += 0.4
            if (r.get("exchange") or "").startswith("OTC"):
                s -= 0.5
            return s
        best = max(eq, key=score)
        if score(best) < 0.55:
            val = None
        else:
            us = next((r for r in eq if "." not in r["symbol"] and not (r.get("exchange") or "").startswith("OTC") and r["symbol"] != best["symbol"]
                       and SequenceMatcher(None, _norm(best.get("name", "")), _norm(r.get("name", ""))).ratio() > 0.6), None)
            val = {"name": best.get("name") or name, "symbol": best["symbol"], "exchange": best.get("exchange"), "us": us["symbol"] if us else None}
    with _lock:
        _cache()[key] = val
        if len(_cache()) > 5000:
            for k in list(_cache())[:1000]:
                _cache().pop(k, None)
    _save()
    return val


def names() -> dict[str, str]:
    """symbol -> company name for every resolved company (labels in cards and verdicts)."""
    out: dict[str, str] = {}
    for v in _cache().values():
        if v:
            out[v["symbol"]] = v["name"]
            if v.get("us"):
                out[v["us"]] = v["name"] + " (US)"
    return out
