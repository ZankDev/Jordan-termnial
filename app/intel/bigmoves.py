"""BIG MOVERS research: every US stock day with a close-to-close move of +-10% or more, why it happened, and what (if anything) flagged it the day before.

Data (all free, all checkable):
  universe   Yahoo screener: US-listed common stock, market cap > $300M, price > $3, 3-month average volume > 400k (about 2,400 names, today's list,
             so delisted names are missing = survivorship bias; noted in the report)
  prices     Yahoo daily OHLCV, 10 years, split-adjusted (data_cache/bigmoves/daily/*.parquet)
  catalysts  SEC EDGAR filings per company with their acceptance time: 8-K item 2.02 = earnings, 1.01 = material agreement, 2.01 = deal closed,
             5.02 = management change, 1.03 = bankruptcy, 3.01 = delisting notice, 7.01/8.01 = other news; 424B* / S-1 / S-3 = share offering;
             SC 13D = activist; SC TO-T / DEFM14A = takeover
  market     SPY, QQQ, IWM, ^VIX, sector ETFs
A move day D is the close of D vs the close of D-1. A filing "explains" D when it was accepted after 16:00 ET on D-1 or before 16:00 ET on D.
Features for the "before" study use only data up to the close of D-1. Nothing here trades; research only, not investment advice.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data_cache", "bigmoves")
DAILY = os.path.join(OUT, "daily")
SECD = os.path.join(OUT, "sec")
MARKET = ["SPY", "QQQ", "IWM", "^VIX", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SMH", "XBI", "KRE", "ARKK"]
SECTOR_ETF = {"Technology": "XLK", "Financial Services": "XLF", "Energy": "XLE", "Healthcare": "XLV", "Industrials": "XLI", "Consumer Cyclical": "XLY",
              "Consumer Defensive": "XLP", "Utilities": "XLU", "Basic Materials": "XLB", "Real Estate": "XLRE", "Communication Services": "XLC"}
TH = 0.10


def universe() -> dict[str, dict[str, Any]]:
    return json.load(open(os.path.join(OUT, "universe.json")))


# ------------------------------------------------------------------------------------------------ download
def _fetch_daily(sym: str, force: bool = False) -> bool:
    from app.api import invest_routes as ir

    path = os.path.join(DAILY, sym.replace("^", "_") + ".parquet")
    if os.path.exists(path) and not force and time.time() - os.path.getmtime(path) < 20 * 3600:
        return True
    for a in range(3):
        try:
            h = ir._history(sym, "10y", "1d")
            c = h["candles"]
            if len(c) < 60:
                return False
            df = pd.DataFrame(c, columns=["t", "o", "h", "l", "c", "v"])
            df["d"] = pd.to_datetime(df["t"], unit="s", utc=True).dt.tz_convert(NY).dt.date.astype(str)
            df = df.drop_duplicates("d", keep="last").drop(columns="t")
            df.to_parquet(path)
            return True
        except Exception as e:  # noqa: BLE001
            if "404" in str(e):
                return False
            time.sleep(1.5 * (a + 1))
    return False


def download_prices(workers: int = 8) -> dict[str, int]:
    os.makedirs(DAILY, exist_ok=True)
    syms = list(universe()) + MARKET
    with ThreadPoolExecutor(workers) as ex:
        ok = list(ex.map(_fetch_daily, syms))
    return {"ok": sum(ok), "total": len(syms)}


def _fetch_sec(sym: str) -> bool:
    """All filings (form, items, acceptance time) back to 2015 for one company."""
    from app.api import invest_routes as ir

    path = os.path.join(SECD, sym + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 3 * 86400:
        return True
    ck = ir._cik(sym)
    if not ck:
        return False
    rows: list[list[str]] = []

    def take(block: dict[str, Any]) -> str:
        n = len(block.get("form", []))
        for i in range(n):
            rows.append([block["form"][i], block.get("items", [""] * n)[i] or "", block.get("acceptanceDateTime", [""] * n)[i] or "", block["filingDate"][i]])
        return min(block["filingDate"]) if n else "9999"

    for a in range(3):
        try:
            j = ir._sec_json(f"https://data.sec.gov/submissions/CIK{ck[0]:010d}.json")
            oldest = take(j["filings"]["recent"])
            for f in j["filings"].get("files", []):
                if oldest < "2015-06-01":
                    break
                time.sleep(0.12)
                oldest = min(oldest, take(ir._sec_json(f"https://data.sec.gov/submissions/{f['name']}")))
            keep = [r for r in rows if r[0] in KEEP_FORMS or r[0].startswith("424B")]
            json.dump(keep, open(path, "w"))
            return True
        except Exception:  # noqa: BLE001
            time.sleep(2 * (a + 1))
    return False


KEEP_FORMS = {"8-K", "8-K/A", "6-K", "S-1", "S-3", "S-1/A", "S-3ASR", "F-1", "F-3", "SC 13D", "SC 13D/A", "SC TO-T", "SC TO-I", "DEFM14A", "PREM14A", "SC 14D9", "10-Q", "10-K", "20-F", "25", "25-NSE"}


def download_sec(workers: int = 4) -> dict[str, int]:
    os.makedirs(SECD, exist_ok=True)
    syms = list(universe())

    def one(s: str) -> bool:
        ok = _fetch_sec(s)
        time.sleep(0.3)
        return ok
    with ThreadPoolExecutor(workers) as ex:
        ok = list(ex.map(one, syms))
    return {"ok": sum(ok), "total": len(syms)}


def load(sym: str) -> pd.DataFrame | None:
    path = os.path.join(DAILY, sym.replace("^", "_") + ".parquet")
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df["d"] = pd.to_datetime(df["d"])
    return df.set_index("d").sort_index()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    step = sys.argv[1] if len(sys.argv) > 1 else "prices"
    print(download_prices() if step == "prices" else download_sec())
