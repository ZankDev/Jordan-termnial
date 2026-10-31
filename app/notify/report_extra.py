"""Extra free data sources for the /report PDF: ECB and BIS official statistics APIs, and ARK Invest's
published daily fund holdings. Every function is bounded by short request timeouts and returns None on
failure, so a slow or changed source only drops its own section."""
from __future__ import annotations

import csv
import io
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 8

# (label, ECB dataflow, series key, unit)
ECB_SERIES = [
    ("ECB deposit facility rate", "FM", "D.U2.EUR.4F.KR.DFR.LEV", "%"),
    ("Euro area inflation (HICP, y/y)", "ICP", "M.U2.N.000000.4.ANR", "%"),
    ("EUR/USD reference rate", "EXR", "D.USD.EUR.SP00.A", ""),
]
# (label, BIS dataflow, series key, unit)
BIS_SERIES = [
    ("US policy rate", "WS_CBPOL", "D.US", "%"),
    ("Euro area policy rate", "WS_CBPOL", "D.XM", "%"),
    ("UK policy rate", "WS_CBPOL", "D.GB", "%"),
    ("Japan policy rate", "WS_CBPOL", "D.JP", "%"),
    ("US dollar broad effective exchange rate (index)", "WS_EER", "D.N.B.US", ""),
]
ARK_BASE = "https://assets.ark-funds.com/fund-documents/funds-etf-csv/"
ARK_FUNDS = {"ARKK": "ARK_INNOVATION_ETF_ARKK", "ARKW": "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW",
             "ARKG": "ARK_GENOMIC_REVOLUTION_ETF_ARKG", "ARKF": "ARK_FINTECH_INNOVATION_ETF_ARKF"}


def _ecb_one(spec: tuple[str, str, str, str]) -> dict[str, Any] | None:
    label, flow, key, unit = spec
    try:
        r = requests.get(f"https://data-api.ecb.europa.eu/service/data/{flow}/{key}", params={"lastNObservations": 2, "format": "jsondata"}, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()
        series = next(iter(j["dataSets"][0]["series"].values()))
        times = j["structure"]["dimensions"]["observation"][0]["values"]
        obs = sorted(((int(i), v[0]) for i, v in series["observations"].items()), key=lambda x: x[0])
        i, v = obs[-1]
        prev = obs[-2][1] if len(obs) > 1 else None
        return {"label": label, "value": v, "prev": prev, "date": times[i]["id"], "unit": unit, "source": "ECB"}
    except Exception:  # noqa: BLE001
        return None


def _bis_one(spec: tuple[str, str, str, str]) -> dict[str, Any] | None:
    label, flow, key, unit = spec
    try:
        r = requests.get(f"https://stats.bis.org/api/v1/data/{flow}/{key}", params={"lastNObservations": 2, "format": "csv"}, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        rows = [x for x in csv.DictReader(io.StringIO(r.text)) if x.get("OBS_VALUE")]
        rows.sort(key=lambda x: x["TIME_PERIOD"])
        last = rows[-1]
        return {"label": label, "value": float(last["OBS_VALUE"]), "prev": float(rows[-2]["OBS_VALUE"]) if len(rows) > 1 else None,
                "date": last["TIME_PERIOD"], "unit": unit, "source": "BIS"}
    except Exception:  # noqa: BLE001
        return None


def global_macro() -> list[dict[str, Any]] | None:
    """Euro-area and global rates/inflation/FX from the ECB and BIS official APIs (no keys needed)."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        fa = [ex.submit(_ecb_one, s) for s in ECB_SERIES]
        fb = [ex.submit(_bis_one, s) for s in BIS_SERIES]
        rows = [f.result() for f in fa + fb]
    rows = [r for r in rows if r]
    return rows or None


COT_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
COT_MARKETS = [("E-mini S&P 500", "E-MINI S&P 500"), ("Nasdaq-100 (consolidated)", "NASDAQ-100 Consolidated")]


def cot_positioning() -> list[dict[str, Any]] | None:
    """CFTC Commitments of Traders (financial futures): leveraged-fund and asset-manager net positions in index futures, latest two weeks."""
    out = []
    for label, name in COT_MARKETS:
        try:
            r = requests.get(COT_URL, params={"$limit": 2, "$order": "report_date_as_yyyy_mm_dd DESC", "$where": f"contract_market_name='{name}'"}, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            rows = r.json()
            if not rows:
                continue

            def nets(x: dict[str, Any]) -> tuple[int, int]:
                return (int(float(x["lev_money_positions_long"])) - int(float(x["lev_money_positions_short"])),
                        int(float(x["asset_mgr_positions_long"])) - int(float(x["asset_mgr_positions_short"])))
            lev, am = nets(rows[0])
            plev, pam = nets(rows[1]) if len(rows) > 1 else (None, None)
            out.append({"market": label, "date": rows[0]["report_date_as_yyyy_mm_dd"][:10], "lev_net": lev, "lev_prev": plev, "am_net": am, "am_prev": pam,
                        "oi": int(float(rows[0]["open_interest_all"]))})
        except Exception:  # noqa: BLE001
            continue
    return out or None


def _ark_one(item: tuple[str, str], ticker: str) -> dict[str, Any] | None:
    fund, name = item
    try:
        r = requests.get(f"{ARK_BASE}{name}_HOLDINGS.csv", headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        for x in csv.DictReader(io.StringIO(r.text)):
            if (x.get("ticker") or "").strip().upper() == ticker:
                num = lambda s: float(str(s).replace("$", "").replace(",", "").replace("%", "")) if s else None  # noqa: E731
                return {"fund": fund, "company": x.get("company"), "shares": num(x.get("shares")), "value": num(x.get("market value ($)")),
                        "weight": num(x.get("weight (%)")), "date": x.get("date")}
    except Exception:  # noqa: BLE001
        return None
    return None


def ark_holdings(sym: str) -> list[dict[str, Any]] | None:
    """Which of ARK's published ETFs (ARKK/ARKW/ARKG/ARKF) currently hold this ticker, at what weight.
    ARK publishes only a current snapshot, with no trade history."""
    ticker = sym.upper().replace("-", ".")
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = [f.result() for f in [ex.submit(_ark_one, it, ticker) for it in ARK_FUNDS.items()]]
    res = [r for r in res if r]
    return res or None


# ------------------------------------------------------------------ crypto: Deribit options and DefiLlama
DERIBIT = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"


def deribit_options(base: str) -> dict[str, Any] | None:
    """Option open interest, put/call, ATM implied volatility and max pain per expiry from Deribit's public API (BTC and ETH only)."""
    import datetime as dt

    if base not in ("BTC", "ETH"):
        return None
    try:
        r = requests.get(DERIBIT, params={"currency": base, "kind": "option"}, headers=UA, timeout=12)
        r.raise_for_status()
        rows = r.json()["result"]
    except Exception:  # noqa: BLE001
        return None
    spot = next((float(x["estimated_delivery_price"]) for x in rows if x.get("estimated_delivery_price")), None)
    if not spot:
        return None
    by: dict[dt.date, dict[str, Any]] = {}
    for x in rows:
        try:
            _b, ds, k, cp = x["instrument_name"].split("-")
            d, strike = dt.datetime.strptime(ds, "%d%b%y").date(), float(k)
        except Exception:  # noqa: BLE001
            continue
        e = by.setdefault(d, {"strikes": {}, "call_oi": 0.0, "put_oi": 0.0, "call_vol": 0.0, "put_vol": 0.0})
        oi, vol = float(x.get("open_interest") or 0), float(x.get("volume") or 0)
        s = e["strikes"].setdefault(strike, {"c": 0.0, "p": 0.0, "iv": None})
        if cp == "C":
            e["call_oi"] += oi
            e["call_vol"] += vol
            s["c"] += oi
        else:
            e["put_oi"] += oi
            e["put_vol"] += vol
            s["p"] += oi
        if x.get("mark_iv"):
            s["iv"] = float(x["mark_iv"])
    out = []
    for d in sorted(by):
        e = by[d]
        if e["call_oi"] + e["put_oi"] <= 0:
            continue
        ks = sorted(e["strikes"])
        pain = min(ks, key=lambda S: sum(e["strikes"][k]["c"] * max(0.0, S - k) + e["strikes"][k]["p"] * max(0.0, k - S) for k in ks))
        atm = min((k for k in ks if e["strikes"][k]["iv"]), key=lambda k: abs(k - spot), default=None)
        out.append({"date": d.isoformat(), "dte": (d - dt.date.today()).days, "call_oi": e["call_oi"], "put_oi": e["put_oi"], "pc": e["put_oi"] / e["call_oi"] if e["call_oi"] else None,
                    "oi_usd": (e["call_oi"] + e["put_oi"]) * spot, "max_pain": pain, "atm_iv": e["strikes"][atm]["iv"] if atm else None,
                    "pc_vol": e["put_vol"] / e["call_vol"] if e["call_vol"] else None})
    if not out:
        return None
    tc, tp = sum(x["call_oi"] for x in out), sum(x["put_oi"] for x in out)
    return {"base": base, "spot": spot, "expiries": out, "total_oi_usd": (tc + tp) * spot, "pc": tp / tc if tc else None}


def defillama(base: str) -> dict[str, Any] | None:
    """DeFi context from DefiLlama (free, no key): total value locked on the coin's own chain, the biggest chains, and global stablecoin supply."""
    out: dict[str, Any] = {}
    try:
        chains = requests.get("https://api.llama.fi/v2/chains", headers=UA, timeout=12).json()
        chains = sorted((c for c in chains if c.get("tvl")), key=lambda c: -c["tvl"])
        out["top"] = [{"name": c["name"], "tvl": c["tvl"], "symbol": c.get("tokenSymbol")} for c in chains[:8]]
        mine = next(((i, c) for i, c in enumerate(chains) if (c.get("tokenSymbol") or "").upper() == base), None)
        if mine:
            out["chain"] = {"name": mine[1]["name"], "tvl": mine[1]["tvl"], "rank": mine[0] + 1, "n": len(chains), "share": mine[1]["tvl"] / sum(c["tvl"] for c in chains)}
        out["total_tvl"] = sum(c["tvl"] for c in chains)
    except Exception:  # noqa: BLE001
        pass
    try:
        j = requests.get("https://stablecoins.llama.fi/stablecoins", params={"includePrices": "false"}, headers=UA, timeout=20).json()["peggedAssets"]
        cur = sum((a.get("circulating") or {}).get("peggedUSD") or 0 for a in j)
        prev = sum((a.get("circulatingPrevMonth") or {}).get("peggedUSD") or 0 for a in j)
        top = sorted(j, key=lambda a: -((a.get("circulating") or {}).get("peggedUSD") or 0))[:5]
        out["stable"] = {"total": cur, "prev_month": prev, "change_pct": (cur / prev - 1) * 100 if prev else None,
                         "top": [{"symbol": a["symbol"], "supply": (a.get("circulating") or {}).get("peggedUSD") or 0} for a in top]}
    except Exception:  # noqa: BLE001
        pass
    return out or None
