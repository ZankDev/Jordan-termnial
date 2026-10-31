"""Data fetching + pure calculations for the Telegram /report PDF (report_pdf.py renders it).

Every fetch is isolated: a failed source returns None and its PDF section is simply skipped.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

SECTOR_ETF = {"Technology": "XLK", "Financial Services": "XLF", "Energy": "XLE", "Healthcare": "XLV", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
              "Industrials": "XLI", "Utilities": "XLU", "Basic Materials": "XLB", "Real Estate": "XLRE", "Communication Services": "XLC"}
DILUTION_FORMS = ("S-3", "S-1", "424B", "S-3ASR", "F-3")
POS_WORDS = ("beat", "surge", "soar", "jump", "rally", "record", "upgrade", "raises", "growth", "wins", "win", "partnership", "approval", "profit", "gain", "bullish", "breakthrough", "expands")
NEG_WORDS = ("miss", "plunge", "fall", "drop", "slump", "downgrade", "lawsuit", "probe", "cut", "loss", "offering", "dilution", "bearish", "warning", "fraud", "sell-off", "selloff", "delay", "recall", "investigation")

SEC_UA = {"User-Agent": "NOSTRO Terminal personal-research tool nostro@example.com", "Accept-Encoding": "gzip, deflate"}
# Form 4 transactionCode -> (label, counts as buy/sell/other per report_calc.insider_stats'
# BUY_WORDS/SELL_WORDS text match). Only genuine open-market P/S transactions count as a
# buy/sell signal - grants, exercises, gifts and tax withholding are compensation mechanics,
# not a conviction signal, so their label deliberately avoids the words "purchase"/"sale".
_F4_CODES = {
    "P": "Open market purchase", "S": "Open market sale", "A": "Award / grant", "M": "Option exercise",
    "G": "Gift", "F": "Tax withholding (shares delivered)", "C": "Conversion", "D": "Disposition to issuer",
    "I": "Discretionary transaction", "J": "Other acquisition/disposition",
}


# ------------------------------------------------------------------ fetching
def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:  # noqa: BLE001
        return None


def _options(inv, sym: str) -> dict[str, Any] | None:
    base = inv.api_options(sym)
    exps = base.get("expirations") or []
    if not exps:
        return None
    want = time.time() + 14 * 86400
    pick = next((e for e in exps if e >= want), exps[-1])
    return base if pick == base.get("date") else inv.api_options(sym, pick)


def _peer_extra(inv, sym: str, n_val: int = 5, n_hist: int = 3) -> dict[str, Any]:
    """Peers (up to 10) + valuation rows for the first n_val + 2y history for the first n_hist."""
    peers = (inv.api_peers(sym) or {}).get("peers") or []
    out: dict[str, Any] = {"peers": peers, "val": {}, "hist": {}}
    if not peers:
        return out
    with ThreadPoolExecutor(max_workers=8) as ex:
        fv = {p["symbol"]: ex.submit(_safe, inv._qs, p["symbol"], "summaryDetail,defaultKeyStatistics,financialData") for p in peers[:n_val]}
        fh = {p["symbol"]: ex.submit(_safe, inv._safe_history, p["symbol"], "2y", "1d") for p in peers[:n_hist]}
        out["val"] = {k: f.result(timeout=30) for k, f in fv.items()}
        out["hist"] = {k: f.result(timeout=30) for k, f in fh.items()}
    return out


def _short_volume(sym: str) -> list[tuple[dt.date, float, float]] | None:
    from app.api import flow_routes as fr

    rows = []
    for d, m in fr._trade_days(20):
        v = m.get(sym.upper())
        if v:
            rows.append((d, v[0], v[1]))
    return rows or None


def _parse_form4(xml_bytes: bytes) -> list[dict[str, Any]]:
    """nonDerivativeTable transactions only (actual common-stock buys/sells) - derivative
    (option/RSU) transactions are compensation mechanics, not an open-market conviction
    signal, and are left out here the same way Yahoo's feed treats them as 'other'."""
    root = ET.fromstring(xml_bytes)
    owner_el = root.find("reportingOwner")
    if owner_el is None:
        return []
    name = (owner_el.findtext("reportingOwnerId/rptOwnerName") or "").strip()
    rel = owner_el.find("reportingOwnerRelationship")
    title = (rel.findtext("officerTitle") or "").strip() if rel is not None else ""
    relation = title or ("Director" if rel is not None and rel.findtext("isDirector") == "true" else
                          "10% owner" if rel is not None and rel.findtext("isTenPercentOwner") == "true" else
                          "Officer" if rel is not None and rel.findtext("isOfficer") == "true" else "Other")
    out = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        date_s = tx.findtext("transactionDate/value")
        code = (tx.findtext("transactionCoding/transactionCode") or "").strip()
        shares_s = tx.findtext("transactionAmounts/transactionShares/value")
        price_s = tx.findtext("transactionAmounts/transactionPricePerShare/value")
        if not date_s or not shares_s:
            continue
        try:
            ts = dt.datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
            shares = float(shares_s)
            price = float(price_s) if price_s else None
        except ValueError:
            continue
        out.append({
            "name": name, "relation": relation, "date": ts, "shares": shares,
            "value": shares * price if price else None,
            "text": f"{_F4_CODES.get(code, code or 'Transaction')} at ${price:,.2f}" if price else _F4_CODES.get(code, code or "Transaction"),
        })
    return out


def _form4_transactions(inv, sym: str, max_filings: int = 25) -> list[dict[str, Any]] | None:
    """Real, authoritative insider transactions parsed directly from SEC Form 4 XML filings -
    the actual primary source Yahoo's own insiderTransactions feed is itself derived from,
    used here instead of (or as a cross-check on) that derived feed. Capped to the most
    recent `max_filings` Form 4s in the last 12 months, fetched concurrently; a company with
    unusually heavy insider activity may have more filings than this in a year, in which case
    this is the most-recent subset rather than the complete year."""
    _t0 = time.time()
    try:
        listing = inv.api_filings(sym, form="4")
    except Exception:  # noqa: BLE001
        return None
    items = listing.get("items") or []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)
    recent = [it for it in items if it.get("filed") and dt.datetime.strptime(it["filed"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc) >= cutoff]
    recent = recent[:max_filings]
    if not recent:
        return None

    def _one(item: dict[str, Any]) -> list[dict[str, Any]]:
        url = item.get("url") or ""
        base, _, doc = url.rpartition("/")
        # primaryDocument from SEC's submissions API often points at the XSLT-rendered HTML
        # view (e.g. ".../xslF345X06/form4.xml"); the raw machine-readable XML of the same
        # filing always sits at the filing's own root directory under just the basename.
        root_dir = url.split("/xslF345X06/")[0] if "/xslF345X06/" in url else base
        raw_url = f"{root_dir}/{doc}"
        try:
            r = requests.get(raw_url, headers=SEC_UA, timeout=6)
            r.raise_for_status()
            return _parse_form4(r.content)
        except Exception:  # noqa: BLE001
            return []

    # bounded well under fetch_all's own overall deadline: SEC has been seen taking 10s+ per
    # request under load (same symptom as the Yahoo slowness fixed in yfinance_source.py), so
    # this returns whatever completed rather than blocking the whole report on stragglers -
    # report_fund.insiders() falls back to Yahoo's feed when this comes back empty/partial.
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(_one, item) for item in recent]
        for f in futs:
            try:
                out.extend(f.result(timeout=max(0.1, 25 - (time.time() - _t0))))
            except Exception:  # noqa: BLE001
                pass
    return out or None


def _opt_near(inv, sym: str) -> dict[str, Any] | None:
    """Chain for the nearest expiry that is at least 3 days out (the main chain is ~2 weeks out)."""
    base = inv.api_options(sym)
    exps = base.get("expirations") or []
    pick = next((e for e in exps if e >= time.time() + 3 * 86400), None)
    if not pick:
        return None
    return base if pick == base.get("date") else inv.api_options(sym, pick)


def fetch_all(inv, sym: str, sec: dict[str, Any], is_coin: bool, deadline: float = 75.0, progress=None, on_result=None) -> dict[str, Any]:
    """Everything the report needs, fetched in parallel. Keys missing/None = source failed or ran past the deadline."""
    from concurrent.futures import wait

    from app.api import flow_routes as fr
    from app.api import market_routes as mr
    from app.api import onchain_routes as oc
    from app.notify import report_alt, report_extra, report_sec

    bench = "BTC-USD" if is_coin else "SPY"
    sector = (sec.get("profile") or {}).get("sector")
    etf = SECTOR_ETF.get(sector or "")
    hist = lambda s, r="2y": (lambda: inv._safe_history(s, r, "1d"))  # noqa: E731
    jobs: dict[str, Any] = {
        "hist5": lambda: inv._safe_history(sym, "5y", "1d"),
        "bench5": lambda: inv._safe_history(bench, "5y", "1d"),
        "news": lambda: inv._news(sym, True)[:30],
    }
    if is_coin:
        base = sym.upper().replace("-USD", "")
        jobs.update({"coin": lambda: inv.api_crypto_coin(sym), "fng": lambda: oc.api_fng(90), "perps": lambda: oc.api_perps(200), "trending": oc.api_trending,
                     "cglobal": inv.api_crypto_global, "seas": lambda: mr.api_seasonality(sym), "h_eth": hist("ETH-USD"), "h_spy": hist("SPY"), "h_gold": hist("GC=F")})
        jobs.update({"macro": inv.api_macro, "global_macro": report_extra.global_macro, "cot": report_extra.cot_positioning,
                     "deribit": lambda: report_extra.deribit_options(base), "llama": lambda: report_extra.defillama(base)})
        if base == "BTC":
            jobs["btcnet"] = oc.api_btcnet
    else:
        jobs.update({
            "analysts": lambda: inv.api_analysts(sym),
            "fin": lambda: inv.api_financials(sym),
            "holders": lambda: inv.api_holders(sym),
            "options": lambda: _options(inv, sym),
            "opt_near": lambda: _opt_near(inv, sym),
            "peer_extra": lambda: _peer_extra(inv, sym),
            "filings": lambda: inv.api_filings(sym),
            "form4_tx": lambda: _form4_transactions(inv, sym),
            "global_macro": report_extra.global_macro,
            "cot": report_extra.cot_positioning,
            "ark": lambda: report_extra.ark_holdings(sym),
            "divs": lambda: inv.api_dividends(sym),
            "keystats": lambda: inv._qs(sym, "defaultKeyStatistics,majorHoldersBreakdown"),
            "sec": lambda: report_sec.build(inv, sym),
            "subj_val": lambda: inv._qs(sym, "summaryDetail,defaultKeyStatistics,financialData"),
            "raw": lambda: inv._qs(sym, "upgradeDowngradeHistory,earningsTrend,insiderTransactions", ttl=900),
            "seas": lambda: mr.api_seasonality(sym),
            "optvol": lambda: mr.api_options_vol(sym),
            "uoa": lambda: mr._uoa_one(sym),
            "ats": lambda: fr.api_ats(sym),
            "sv": lambda: _short_volume(sym),
            "macro": inv.api_macro,
            "h_qqq": hist("QQQ"), "h_tnx": hist("^TNX"),
        })
        co_name = sec.get("name") or sym  # not "name": the loop below reuses that variable and the lambdas would see its last value
        cik_of = lambda: (inv._cik(sym) or (None,))[0]  # noqa: E731
        jobs.update({
            "finra_si": lambda: report_alt.short_interest(sym),
            "cboe": lambda: report_alt.cboe_options(sym, sec.get("price")),
            "sec_lang": lambda: (lambda c: report_alt.sec_language(c) if c else None)(cik_of()),
            "xrank": lambda: (lambda c: report_alt.xbrl_rank(c) if c else None)(cik_of()),
            "wiki": lambda: report_alt.wiki_attention(co_name),
            "stwits": lambda: report_alt.stocktwits(sym),
            "hn": lambda: report_alt.hackernews(co_name),
            "gdelt": lambda: report_alt.gdelt_tone(co_name),
            "usasp": lambda: report_alt.usaspending(co_name),
            "imf": report_alt.imf_outlook,
            "nhtsa": lambda: report_alt.nhtsa_recalls(sym),
        })
        if (sector or "").lower() in ("healthcare", "health care"):
            jobs["trials"] = lambda: report_alt.clinical_trials(co_name)
        if etf:
            jobs["h_sector"] = hist(etf)
        for name, s in (("m_spy", "SPY"), ("m_qqq", "QQQ"), ("m_iwm", "IWM"), ("m_vix", "^VIX"), ("m_tnx", "^TNX")):
            jobs[name] = (lambda s=s: inv._mon_row(s, ""))
        if etf:
            jobs["m_sector"] = lambda: inv._mon_row(etf, f"{sector} ({etf})")
    ex = ThreadPoolExecutor(max_workers=28)
    def _run(k: str, fn):
        r = _safe(fn)
        if on_result:  # lets the interactive DEEP DIVE page show each source the moment it answers
            try:
                on_result(k, r)
            except Exception:  # noqa: BLE001
                pass
        return r

    futs = {k: ex.submit(_run, k, fn) for k, fn in jobs.items()}
    t_end = time.time() + deadline
    while True:
        done = sum(1 for f in futs.values() if f.done())
        if progress:
            try:
                progress(done, len(futs))
            except Exception:  # noqa: BLE001
                pass
        left = t_end - time.time()
        if done == len(futs) or left <= 0:
            break
        wait(list(futs.values()), timeout=min(1.0, left))
    out = {k: (f.result() if f.done() else None) for k, f in futs.items()}
    out["_late"] = [k for k, f in futs.items() if not f.done()]
    out["_etf"] = etf
    ex.shutdown(wait=False, cancel_futures=True)
    return out


# ------------------------------------------------------------------ indicators
def ema(c: list[float], n: int) -> list[float]:
    if not c:
        return []
    k, out = 2 / (n + 1), [c[0]]
    for x in c[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def macd(c: list[float]) -> tuple[list[float], list[float], list[float]]:
    e12, e26 = ema(c, 12), ema(c, 26)
    line = [a - b for a, b in zip(e12, e26)]
    sig = ema(line, 9)
    return line, sig, [a - b for a, b in zip(line, sig)]


def boll(c: list[float], n: int = 20, k: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid: list[float | None] = []
    up: list[float | None] = []
    lo: list[float | None] = []
    for i in range(len(c)):
        if i + 1 < n:
            mid.append(None), up.append(None), lo.append(None)
            continue
        w = c[i + 1 - n:i + 1]
        m = sum(w) / n
        sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
        mid.append(m), up.append(m + k * sd), lo.append(m - k * sd)
    return mid, up, lo


def atr(candles: list[list[float]], n: int = 14) -> float | None:
    if len(candles) <= n:
        return None
    tr = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i][2], candles[i][3], candles[i - 1][4]
        if None in (h, l, pc):
            continue
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(tr) < n:
        return None
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def stoch(candles: list[list[float]], n: int = 14) -> tuple[float | None, float | None]:
    if len(candles) < n + 2:
        return None, None
    ks = []
    for i in range(n - 1, len(candles)):
        w = candles[i + 1 - n:i + 1]
        hh, ll = max(x[2] for x in w if x[2] is not None), min(x[3] for x in w if x[3] is not None)
        ks.append(100 * (candles[i][4] - ll) / (hh - ll) if hh > ll else 50.0)
    return ks[-1], sum(ks[-3:]) / 3


def pivots(candles: list[list[float]]) -> dict[str, float] | None:
    if len(candles) < 2:
        return None
    _, _, h, l, c, _ = candles[-2]
    if None in (h, l, c):
        return None
    p = (h + l + c) / 3
    return {"S2": p - (h - l), "S1": 2 * p - h, "P": p, "R1": 2 * p - l, "R2": p + (h - l)}


def swing_levels(candles: list[list[float]], price: float, look: int = 160, w: int = 5) -> tuple[list[float], list[float]]:
    """Nearest swing-high resistances above and swing-low supports below price (clustered within 2%)."""
    seg = candles[-look:]
    hi, lo = [], []
    for i in range(w, len(seg) - w):
        win = seg[i - w:i + w + 1]
        if seg[i][2] is not None and seg[i][2] == max(x[2] for x in win if x[2] is not None):
            hi.append(seg[i][2])
        if seg[i][3] is not None and seg[i][3] == min(x[3] for x in win if x[3] is not None):
            lo.append(seg[i][3])

    def cluster(vals: list[float]) -> list[float]:
        out: list[float] = []
        for v in sorted(vals):
            if out and abs(v / out[-1] - 1) < 0.02:
                out[-1] = (out[-1] + v) / 2
            else:
                out.append(v)
        return out
    res = [v for v in cluster(hi) if v > price * 1.005][:3]
    sup = [v for v in cluster(lo) if v < price * 0.995][-3:][::-1]
    return res, sup


def signal_count(cl: list[float], candles: list[list[float]]) -> tuple[int, int, list[str]]:
    """Bullish / bearish tally from standard rules; returns (bull, bear, notes)."""
    from app.notify.report_pdf import _rsi, _sma  # local import: avoid cycle at module load

    bull = bear = 0
    notes: list[str] = []
    p = cl[-1]

    def add(cond_bull: bool | None, text: str) -> None:
        nonlocal bull, bear
        if cond_bull is None:
            return
        bull += cond_bull
        bear += (not cond_bull)
        notes.append(("+ " if cond_bull else "- ") + text)
    for n in (20, 50, 200):
        m = _sma(cl, n)
        if m:
            add(p > m, f"price {'above' if p > m else 'below'} SMA{n}")
    s50, s200 = _sma(cl, 50), _sma(cl, 200)
    if s50 and s200:
        add(s50 > s200, "SMA50 " + ("above" if s50 > s200 else "below") + " SMA200")
    if len(cl) > 35:
        line, sig, _ = macd(cl)
        add(line[-1] > sig[-1], f"MACD {'above' if line[-1] > sig[-1] else 'below'} signal")
    r = _rsi(cl)
    if r is not None:
        add(r > 50, f"RSI {r:.0f} ({'>' if r > 50 else '<'} 50)")
    mid, up, lo = boll(cl)
    if up[-1] and lo[-1] and mid[-1]:
        add(p > mid[-1], "price " + ("above" if p > mid[-1] else "below") + " Bollinger midline")
    k, _d = stoch(candles)
    if k is not None:
        add(k > 50, f"Stochastic {k:.0f}")
    return bull, bear, notes


# ------------------------------------------------------------------ risk / performance
def _day(ts: float) -> dt.date:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


def daily_returns(candles: list[list[float]]) -> dict[dt.date, float]:
    out: dict[dt.date, float] = {}
    for a, b in zip(candles, candles[1:]):
        if a[4] and b[4]:
            out[_day(b[0])] = b[4] / a[4] - 1
    return out


def risk_stats(candles: list[list[float]], bench: list[list[float]] | None, days: int = 252) -> dict[str, Any]:
    r = daily_returns(candles[-(days + 1):])
    vals = list(r.values())
    out: dict[str, Any] = {}
    if len(vals) < 20:
        return out
    m = sum(vals) / len(vals)
    sd = (sum((x - m) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5
    down = [x for x in vals if x < 0]
    dsd = (sum(x * x for x in down) / len(vals)) ** 0.5 if down else 0
    out["ann_ret"] = ((1 + m) ** 252 - 1) * 100
    out["ann_vol"] = sd * math.sqrt(252) * 100
    out["sharpe"] = m / sd * math.sqrt(252) if sd else None
    out["sortino"] = m / dsd * math.sqrt(252) if dsd else None
    s = sorted(vals)
    out["var95"] = s[int(len(s) * 0.05)] * 100
    out["best"], out["worst"] = max(vals) * 100, min(vals) * 100
    out["pos_days"] = 100 * sum(1 for x in vals if x > 0) / len(vals)
    if bench:
        br = daily_returns(bench[-(days + 1):])
        common = [d for d in r if d in br]
        if len(common) >= 30:
            x = [br[d] for d in common]
            y = [r[d] for d in common]
            mx, my = sum(x) / len(x), sum(y) / len(y)
            cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) - 1)
            vx = sum((a - mx) ** 2 for a in x) / (len(x) - 1)
            vy = sum((b - my) ** 2 for b in y) / (len(y) - 1)
            if vx and vy:
                beta = cov / vx
                out["beta"] = beta
                out["corr"] = cov / math.sqrt(vx * vy)
                out["alpha"] = ((my - beta * mx) * 252) * 100
    return out


def drawdown_series(candles: list[list[float]]) -> tuple[list[dt.datetime], list[float]]:
    ts, dd, peak = [], [], 0.0
    for c in candles:
        if c[4] is None:
            continue
        peak = max(peak, c[4])
        ts.append(dt.datetime.fromtimestamp(c[0]))
        dd.append((c[4] / peak - 1) * 100)
    return ts, dd


def monthly_returns(candles: list[list[float]], months: int = 12) -> list[tuple[str, float]]:
    last: dict[tuple[int, int], float] = {}
    for c in candles:
        if c[4] is None:
            continue
        d = dt.datetime.fromtimestamp(c[0])
        last[(d.year, d.month)] = c[4]
    keys = sorted(last)
    out = []
    for a, b in zip(keys, keys[1:]):
        out.append((dt.date(b[0], b[1], 1).strftime("%b %y"), (last[b] / last[a] - 1) * 100))
    return out[-months:]


def yearly_returns(candles: list[list[float]]) -> list[tuple[str, float]]:
    by: dict[int, list[float]] = {}
    for c in candles:
        if c[4] is not None:
            by.setdefault(dt.datetime.fromtimestamp(c[0]).year, []).append(c[4])
    out, prev_close = [], None
    now_y = dt.datetime.now().year
    for y in sorted(by):
        base = prev_close if prev_close else by[y][0]
        out.append((f"{y}{' YTD' if y == now_y else ''}", (by[y][-1] / base - 1) * 100))
        prev_close = by[y][-1]
    return out[-5:]


# ------------------------------------------------------------------ fundamentals
def fin_series(fin: dict[str, Any] | None) -> tuple[list[str], dict[str, list[float | None]]]:
    """(period end dates oldest->newest, {key: values oldest->newest})."""
    if not fin or not fin.get("available") or not fin.get("rows"):
        return [], {}
    periods = fin["periods"][::-1]
    return periods, {r["key"]: r["values"][::-1] for r in fin["rows"]}


def _g(s: dict[str, list[float | None]], k: str, i: int) -> float | None:
    v = s.get(k)
    return v[i] if v and -len(v) <= i < len(v) else None


def piotroski(s: dict[str, list[float | None]]) -> list[tuple[str, bool | None]]:
    """The 9 Piotroski F-score tests for the latest vs prior fiscal year. None = not enough data."""
    def ratio(a: float | None, b: float | None) -> float | None:
        return a / b if a is not None and b else None

    roa1, roa0 = ratio(_g(s, "netinc", -1), _g(s, "assets", -1)), ratio(_g(s, "netinc", -2), _g(s, "assets", -2))
    cr1, cr0 = ratio(_g(s, "curassets", -1), _g(s, "curliab", -1)), ratio(_g(s, "curassets", -2), _g(s, "curliab", -2))
    lev1, lev0 = ratio(_g(s, "debt", -1) or 0, _g(s, "assets", -1)), ratio(_g(s, "debt", -2) or 0, _g(s, "assets", -2))
    gm1, gm0 = ratio(_g(s, "gross", -1), _g(s, "revenue", -1)), ratio(_g(s, "gross", -2), _g(s, "revenue", -2))
    at1, at0 = ratio(_g(s, "revenue", -1), _g(s, "assets", -1)), ratio(_g(s, "revenue", -2), _g(s, "assets", -2))
    sh1, sh0 = _g(s, "shares", -1), _g(s, "shares", -2)
    ni, ocf = _g(s, "netinc", -1), _g(s, "ocf", -1)

    def cmp(a: float | None, b: float | None, gt: bool) -> bool | None:
        if a is None or b is None:
            return None
        return a > b if gt else a < b
    return [
        ("Positive net income", None if ni is None else ni > 0),
        ("Positive operating cash flow", None if ocf is None else ocf > 0),
        ("Return on assets improving", cmp(roa1, roa0, True)),
        ("Cash flow exceeds net income", None if ni is None or ocf is None else ocf > ni),
        ("Leverage (debt/assets) falling", cmp(lev1, lev0, False)),
        ("Current ratio improving", cmp(cr1, cr0, True)),
        ("No share dilution", None if sh1 is None or sh0 is None else sh1 <= sh0 * 1.005),
        ("Gross margin improving", cmp(gm1, gm0, True)),
        ("Asset turnover improving", cmp(at1, at0, True)),
    ]


def cagr(first: float | None, last: float | None, years: int) -> float | None:
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def runway_months(cash: float | None, fcf: float | None) -> float | None:
    if cash is None or fcf is None or fcf >= 0:
        return None
    return cash / (-fcf) * 12


# ------------------------------------------------------------------ options
def option_stats(o: dict[str, Any] | None) -> dict[str, Any] | None:
    if not o or not (o.get("calls") or o.get("puts")):
        return None
    calls, puts, price = o["calls"], o["puts"], o.get("price")
    num = lambda v: v or 0  # noqa: E731
    coi, poi = sum(num(c["oi"]) for c in calls), sum(num(p["oi"]) for p in puts)
    cv, pv = sum(num(c["vol"]) for c in calls), sum(num(p["vol"]) for p in puts)
    out: dict[str, Any] = {"date": o.get("date"), "call_oi": coi, "put_oi": poi, "call_vol": cv, "put_vol": pv,
                           "pc_oi": poi / coi if coi else None, "pc_vol": pv / cv if cv else None}
    strikes = sorted({c["strike"] for c in calls + puts if c.get("strike") is not None})
    if strikes:
        def pain(k: float) -> float:
            return sum(num(c["oi"]) * max(0.0, k - c["strike"]) for c in calls) + sum(num(p["oi"]) * max(0.0, p["strike"] - k) for p in puts)
        out["max_pain"] = min(strikes, key=pain)
    if price:
        near = sorted((c for c in calls + puts if c.get("iv") and c.get("strike")), key=lambda c: abs(c["strike"] - price))[:4]
        ivs = [c["iv"] for c in near if 0.01 < c["iv"] < 10]
        if ivs:
            iv = sum(ivs) / len(ivs)
            dte = max(1, (o.get("date") or time.time()) - time.time()) / 86400
            out["iv"] = iv * 100
            out["dte"] = dte
            out["exp_move"] = price * iv * math.sqrt(dte / 365)
    out["top_calls"] = sorted(calls, key=lambda c: num(c["oi"]), reverse=True)[:5]
    out["top_puts"] = sorted(puts, key=lambda p: num(p["oi"]), reverse=True)[:5]
    return out


# ------------------------------------------------------------------ news tone
def news_tone(news: list[dict[str, Any]] | None) -> tuple[int, int, int]:
    pos = neg = neu = 0
    for n in news or []:
        t = (n.get("title") or "").lower()
        p = sum(1 for w in POS_WORDS if re.search(rf"\b{re.escape(w)}", t))
        q = sum(1 for w in NEG_WORDS if re.search(rf"\b{re.escape(w)}", t))
        if p > q:
            pos += 1
        elif q > p:
            neg += 1
        else:
            neu += 1
    return pos, neg, neu
