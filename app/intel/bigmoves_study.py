"""BIG MOVERS study: features known the day before, the event table, causes, distributions and follow-through. See bigmoves.py for the data.

Build:  python -m app.intel.bigmoves_study build      -> data_cache/bigmoves/events.parquet, control.parquet, dist.parquet
Everything in the "before" features is known at the close of D-1, except the earnings flag: earnings dates are scheduled and published weeks ahead,
so "reports tomorrow" is legitimately known; the date itself is taken from the 8-K item 2.02 acceptance time (after 16:00 ET -> next session).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import numpy as np
import pandas as pd

from app.intel.bigmoves import MARKET, OUT, SECD, TH, load, universe

log = logging.getLogger(__name__)

FEATS = ["rv20", "rv_ratio", "atr_pct", "rng_ratio", "bbw_pct", "vol_ratio", "ret5", "ret20", "ret60", "from_hi", "from_lo", "n_big", "n_big_up", "n_big_dn",
         "clv1", "dollar_vol", "logcap", "price", "vix", "vix_chg", "spy1", "earn_next", "days_since_earn", "shelf30", "obv_div", "wy_acc", "wy_dist", "spring",
         "upthrust", "beta"]
TAG_8K = (("1.01", "agreement"), ("2.01", "deal_closed"), ("1.03", "bankruptcy"), ("3.01", "delisting"), ("5.02", "mgmt_change"), ("4.02", "restatement"),
          ("2.06", "impairment"), ("3.02", "equity_sale"), ("7.01", "8k_news"), ("8.01", "8k_news"))


def _sec_list(sym: str) -> list[tuple[pd.Timestamp, str, str]]:
    """(first trading-day candidate, form, items). Accepted at/after 16:00 ET -> the next day (snapped to a session later)."""
    path = os.path.join(SECD, sym + ".json")
    if not os.path.exists(path):
        return []
    out = []
    for form, items, acc, fdate in json.load(open(path)):
        if fdate < "2015-06-01":
            continue
        try:
            t = pd.Timestamp(acc[:19]) if acc else pd.Timestamp(fdate + " 12:00")  # EDGAR acceptance times are Eastern despite the Z suffix
        except Exception:  # noqa: BLE001
            t = pd.Timestamp(fdate + " 12:00")
        out.append((t.normalize() + (pd.Timedelta(days=1) if t.hour >= 16 else pd.Timedelta(0)), form, items or ""))
    return out


def _pct_rank(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n // 2).rank(pct=True)


def stock_frame(sym: str, mkt: dict[str, pd.DataFrame], meta: dict[str, Any], df: pd.DataFrame | None = None) -> pd.DataFrame | None:
    df = load(sym) if df is None else df
    if df is None or len(df) < 300:
        return None
    df = df[df["c"] > 0].copy()
    o, h, l, c, v = (df[k].astype(float) for k in "ohlcv")
    pc = c.shift(1)
    r = c / pc - 1
    lr = np.log(c / pc)
    f = pd.DataFrame(index=df.index)
    f["sym"] = sym
    f["r"], f["gap"], f["intra"] = r, o / pc - 1, c / o - 1
    f["clv"] = ((c - l) / (h - l)).where(h > l, 0.5)
    f["o"], f["h"], f["l"], f["c"], f["v"], f["pc"] = o, h, l, c, v, pc
    rv20, rv120 = lr.rolling(20, min_periods=15).std(), lr.rolling(120, min_periods=80).std()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    big = (r.abs() >= TH).astype(float)
    obv = (np.sign(lr).fillna(0) * v).cumsum()
    hi252, lo252 = c.rolling(252, min_periods=120).max(), c.rolling(252, min_periods=120).min()
    hi40, lo40 = h.rolling(40).max(), l.rolling(40).min()
    pos1y = (c - lo252) / (hi252 - lo252)
    rng40 = hi40 / lo40 - 1
    obv_sl = (obv - obv.shift(40)) / (v.rolling(40).mean() * 40)
    px_sl = c / c.shift(40) - 1
    spy = mkt["SPY"]["c"].reindex(df.index).ffill()
    spr = spy.pct_change()
    vix = mkt["^VIX"]["c"].reindex(df.index).ffill()
    X = pd.DataFrame(index=df.index)
    X["rv20"] = rv20
    X["rv_ratio"] = rv20 / rv120
    X["atr_pct"] = tr.rolling(14, min_periods=10).mean() / c
    X["rng_ratio"] = (c.rolling(10).max() / c.rolling(10).min() - 1) / (c.rolling(60).max() / c.rolling(60).min() - 1)
    X["bbw_pct"] = _pct_rank(c.rolling(20).std() * 4 / c.rolling(20).mean(), 252)
    X["vol_ratio"] = v.rolling(5).mean() / v.rolling(50).mean()
    X["ret5"], X["ret20"], X["ret60"] = c.pct_change(5), c.pct_change(20), c.pct_change(60)
    X["from_hi"], X["from_lo"] = c / hi252 - 1, c / lo252 - 1
    X["n_big"] = big.rolling(252, min_periods=60).sum()
    X["n_big_up"] = (r >= TH).astype(float).rolling(252, min_periods=60).sum()
    X["n_big_dn"] = (r <= -TH).astype(float).rolling(252, min_periods=60).sum()
    X["clv1"] = f["clv"]
    X["dollar_vol"] = np.log10((c * v).rolling(20).mean() + 1)
    X["logcap"] = np.log10(meta.get("cap") or 1e9)
    X["price"] = np.log10(c)
    X["vix"], X["vix_chg"] = vix, vix.pct_change()
    X["spy1"] = spr
    X["obv_div"] = obv_sl - px_sl * 5
    X["wy_acc"] = ((pos1y < 0.35) & (rng40 < 0.7 * rng40.shift(40)) & (obv_sl > 0.05) & (px_sl < 0.03)).astype(float)
    X["wy_dist"] = ((pos1y > 0.65) & (rng40 < 0.7 * rng40.shift(40)) & (obv_sl < -0.05) & (px_sl > -0.03)).astype(float)
    X["spring"] = ((l < lo40.shift(1)) & (c > lo40.shift(1))).astype(float).rolling(3).max()
    X["upthrust"] = ((h > hi40.shift(1)) & (c < hi40.shift(1))).astype(float).rolling(3).max()
    X["beta"] = r.rolling(120, min_periods=80).cov(spr) / spr.rolling(120, min_periods=80).var()
    # SEC catalysts snapped to sessions
    days = df.index
    n = len(df)
    earn = np.zeros(n, bool)
    shelf = np.zeros(n)
    tags: list[set[str]] = [set() for _ in range(n)]
    has_sec = os.path.exists(os.path.join(SECD, sym + ".json"))
    for d, form, items in _sec_list(sym):
        i = int(days.searchsorted(d))
        if i >= n:
            continue
        if form in ("8-K", "8-K/A") and "2.02" in items:
            earn[i] = True
            tags[i].add("earnings")
        elif form == "8-K":
            for it, name in TAG_8K:
                if it in items:
                    tags[i].add(name)
        elif form.startswith("424B"):
            tags[i].add("offering")
        elif form in ("S-1", "S-3", "F-1", "F-3", "S-1/A", "S-3ASR"):
            tags[i].add("registration")
            shelf[i] = 1
        elif form in ("SC TO-T", "SC TO-I", "DEFM14A", "PREM14A", "SC 14D9"):
            tags[i].add("takeover")
        elif form in ("SC 13D", "SC 13D/A"):
            tags[i].add("activist")
        elif form in ("10-Q", "10-K", "20-F", "6-K"):
            tags[i].add("periodic")
    f["has_sec"] = has_sec
    f["earn"] = earn
    f["tags"] = [",".join(sorted(t)) for t in tags]
    f["tags_prev"] = [""] + f["tags"].tolist()[:-1]
    ei = np.where(earn, np.arange(n), -10 ** 6)
    last = np.maximum.accumulate(ei)
    X["days_since_earn"] = np.minimum(np.arange(n) - np.r_[-10 ** 6, last[:-1]], 400).astype(float)
    X["shelf30"] = pd.Series(shelf, index=days).rolling(30, min_periods=1).max()
    earn_next = pd.Series(earn.astype(float), index=days)
    X = X.shift(1)
    X["earn_next"] = earn_next
    X["days_since_earn"] = X["days_since_earn"]
    f = f.join(X)
    f["dv20"] = (c * v).rolling(20).mean().shift(1)
    f["vrel"] = v / v.rolling(50).mean().shift(1)
    for k in (1, 2, 5, 20, 60):
        f[f"fwd{k}"] = c.shift(-k) / c - 1
        f[f"spy_fwd{k}"] = spy.shift(-k) / spy - 1
    f["spy_r"] = spr
    f["sector"] = meta.get("sector") or ""
    f["industry"] = meta.get("industry") or ""
    f["cap"] = meta.get("cap")
    f["name"] = meta.get("name") or sym
    ok = (f["pc"] >= 3) & (f["dv20"] >= 3e6) & (f["r"].abs() < 3) & (f["v"] > 0)
    return f[ok & (f.index >= "2016-06-01")]


def build(sample_frac: float = 0.06, seed: int = 7) -> dict[str, Any]:
    U = universe()
    mkt = {s: d for s in MARKET if (d := load(s)) is not None}
    rng = np.random.default_rng(seed)
    evs, ctrl, dist, breadth = [], [], [], []
    for n, sym in enumerate(U):
        try:
            f = stock_frame(sym, mkt, U[sym])
        except Exception as e:  # noqa: BLE001
            log.warning("frame %s: %s", sym, e)
            continue
        if f is None or f.empty:
            continue
        big = f["r"].abs() >= TH
        evs.append(f[big])
        ctrl.append(f[(rng.random(len(f)) < sample_frac) & ~big])
        z = np.log1p(f["r"]) / f["rv20"]
        dist.append(pd.DataFrame({"sym": sym, "r": f["r"].astype("float32"), "z": z.astype("float32"), "earn": f["earn"], "sector": f["sector"]}))
        breadth.append(pd.DataFrame({"r": f["r"].astype("float32"), "industry": f["industry"], "sector": f["sector"]}))
        if n % 250 == 0:
            log.info("built %d/%d", n, len(U))
    B = pd.concat(breadth)
    B.index.name = "d"
    B = B.reset_index()
    day = B.groupby("d")["r"].agg(n="size", up5=lambda x: (x >= 0.05).mean(), dn5=lambda x: (x <= -0.05).mean(), med="median")
    ind = B.groupby(["d", "industry"])["r"].agg(ind_med="median", ind_n="size").reset_index()
    sec = B.groupby(["d", "sector"])["r"].agg(sec_med="median").reset_index()
    E = pd.concat(evs)
    E.index.name = "d"
    E = E.reset_index().merge(day, left_on="d", right_index=True, how="left").merge(ind, on=["d", "industry"], how="left").merge(sec, on=["d", "sector"], how="left")
    C = pd.concat(ctrl)
    C.index.name = "d"
    C = C.reset_index()
    Dd = pd.concat(dist)
    Dd.index.name = "d"
    E.to_parquet(os.path.join(OUT, "events.parquet"))
    C.to_parquet(os.path.join(OUT, "control.parquet"))
    Dd.reset_index().to_parquet(os.path.join(OUT, "dist.parquet"))
    day.to_parquet(os.path.join(OUT, "breadth.parquet"))
    return {"events": len(E), "control": len(C), "dist_rows": len(Dd), "sample_frac": sample_frac}


# ------------------------------------------------------------------------------------------------ cause classification
def classify(e: pd.Series) -> tuple[str, str]:
    """(primary cause, note) from filings + market/sector breadth. Priority: hard company catalyst > market > sector > other filings > unexplained."""
    tags = set(filter(None, (e.get("tags") or "").split(","))) | set(filter(None, (e.get("tags_prev") or "").split(",")))
    sgn = 1 if e["r"] > 0 else -1
    mkt_day = abs(e.get("spy_r") or 0) >= 0.03 or (e.get("up5" if sgn > 0 else "dn5") or 0) >= 0.15
    ind_move = (e.get("ind_n") or 0) >= 4 and sgn * (e.get("ind_med") or 0) >= 0.04
    sec_move = sgn * (e.get("sec_med") or 0) >= 0.03
    if e.get("earn") or "earnings" in (e.get("tags") or ""):
        return "Earnings", "8-K item 2.02 results"
    if "takeover" in tags:
        return "M&A / takeover", "tender offer or merger proxy filed"
    if tags & {"bankruptcy", "delisting", "restatement"}:
        return "Distress filing", ", ".join(sorted(tags & {"bankruptcy", "delisting", "restatement"}))
    if sgn < 0 and tags & {"offering", "equity_sale"}:
        return "Share offering (dilution)", "424B prospectus / unregistered sale"
    if mkt_day:
        return "Market-wide day", f"SPY {(e.get('spy_r') or 0) * 100:+.1f}%, {(e.get('up5' if sgn > 0 else 'dn5') or 0) * 100:.0f}% of stocks moved 5%+ the same way"
    if ind_move or sec_move:
        return "Sector / industry move", f"{e.get('industry')} median {(e.get('ind_med') or 0) * 100:+.1f}%"
    if tags & {"agreement", "deal_closed", "activist"}:
        return "Deal / agreement / activist", ", ".join(sorted(tags & {"agreement", "deal_closed", "activist"}))
    if tags & {"8k_news", "mgmt_change", "impairment", "offering", "registration", "equity_sale"}:
        return "Other company filing", ", ".join(sorted(tags & {"8k_news", "mgmt_change", "impairment", "offering", "registration", "equity_sale"}))
    if not e.get("has_sec"):
        return "No SEC data (foreign/ADR)", ""
    return "No filing (news / analyst / flow)", ""


def load_events() -> pd.DataFrame:
    E = pd.read_parquet(os.path.join(OUT, "events.parquet"))
    cc = E.apply(classify, axis=1, result_type="expand")
    E["cause"], E["cause_note"] = cc[0], cc[1]
    E["dir"] = np.where(E["r"] > 0, "UP", "DOWN")
    E["gap_share"] = (E["gap"] / E["r"]).clip(-1, 2)
    for k in (1, 5, 20, 60):
        E[f"ab{k}"] = E[f"fwd{k}"] - E[f"spy_fwd{k}"]
    return E


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:] == ["build"]:
        print(build())
