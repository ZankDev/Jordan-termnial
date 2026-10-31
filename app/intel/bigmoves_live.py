"""BIG MOVERS live layer: today's watchlist from the trained model, upcoming earnings with options-implied move vs the stock's own history,
dealer gamma for the top names, the FINRA short-interest test, and news headlines for the case studies.

Gamma levels and implied moves only exist for today (free data has no option-chain history), so they are shown as a live overlay, not backtested.
Research only, not investment advice.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import pandas as pd

from app.intel.bigmoves import MARKET, OUT, TH, load, universe
from app.intel.bigmoves_study import FEATS

log = logging.getLogger(__name__)


def _quotes(syms: list[str]) -> dict[str, dict[str, Any]]:
    from app.api import invest_routes as ir

    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(syms), 40):
        try:
            j = ir._yget("https://query1.finance.yahoo.com/v7/finance/quote", {"symbols": ",".join(syms[i:i + 40])})
            for q in (j.get("quoteResponse") or {}).get("result", []):
                out[q["symbol"]] = q
        except Exception as e:  # noqa: BLE001
            log.warning("quotes: %s", e)
        time.sleep(0.2)
    return out


def score_today(R: dict[str, Any]) -> list[dict[str, Any]]:
    """P(+-10% next session) for every stock, from the model trained on 2016-2021 (features as of the latest close)."""
    from app.intel import bigmoves_study as S
    from app.intel.bigmoves_analysis import _prep

    sc = R["model"]["_scaler"]
    U = universe()
    mkt = {s: d for s in MARKET if (d := load(s)) is not None}
    q = _quotes(list(U))
    now = time.time()
    rows = []
    for sym, meta in U.items():
        df = load(sym)
        if df is None or len(df) < 300:
            continue
        nxt = df.index[-1] + pd.offsets.BDay(1)
        df.loc[nxt] = df.iloc[-1]
        try:
            f = S.stock_frame(sym, mkt, meta, df=df)
        except Exception:  # noqa: BLE001
            f = None
        if f is None or f.empty or f.index[-1] != nxt:
            continue
        row = f.iloc[[-1]].copy()
        qq = q.get(sym, {})
        et = qq.get("earningsTimestampStart") or qq.get("earningsTimestamp")
        earn_days = (et - now) / 86400 if et else None
        row["earn_next"] = 1.0 if earn_days is not None and -0.3 <= earn_days <= 1.6 else 0.0
        X = _prep(row)[sc["feats"]].astype(float)
        X = X.clip(pd.Series(sc["lo"]), pd.Series(sc["hi"]), axis=1)
        X = ((X - pd.Series(sc["mu"])) / pd.Series(sc["sd"])).fillna(0)
        s = float(np.r_[1.0, X.to_numpy()[0]] @ np.array(sc["b"]))
        rows.append({"sym": sym, "name": meta.get("name"), "sector": meta.get("sector"), "cap": meta.get("cap"), "score": s,
                     "p": 1 / (1 + np.exp(-s)), "earn_days": None if earn_days is None else round(earn_days, 1),
                     "earn_date": dt.datetime.fromtimestamp(et).strftime("%a %d %b") if et else None,
                     "rv20": float(row["rv20"].iloc[0]), "n_big": float(row["n_big"].iloc[0]), "bbw_pct": float(row["bbw_pct"].iloc[0]),
                     "price": qq.get("regularMarketPrice"), "chg": qq.get("regularMarketChangePercent"), "short": None})
    rows.sort(key=lambda x: -x["score"])
    return rows


def earnings_hist(sym: str) -> dict[str, Any]:
    D = _dist()
    g = D[(D["sym"] == sym) & D["earn"]]
    if g.empty:
        return {}
    a = g["r"].abs()
    return {"n": int(len(g)), "med": float(a.median()), "mean": float(a.mean()), "p10": float((a >= TH).mean()), "max_up": float(g["r"].max()), "max_dn": float(g["r"].min())}


_D: dict[str, pd.DataFrame] = {}


def _dist() -> pd.DataFrame:
    if "d" not in _D:
        _D["d"] = pd.read_parquet(os.path.join(OUT, "dist.parquet"), columns=["sym", "r", "earn"])
    return _D["d"]


def implied_move(sym: str, after_ts: float | None) -> dict[str, Any]:
    """ATM straddle of the first expiry after the earnings date / spot = the market's priced move to that expiry."""
    from app.api import invest_routes as ir

    first = ir.api_options(sym)
    S = first.get("price")
    exps = first.get("expirations") or []
    if not S or not exps:
        return {}
    tgt = [e for e in exps if after_ts is None or e >= after_ts - 3600]
    if not tgt:
        return {}
    e = tgt[0]
    ch = first if e == first.get("date") else ir.api_options(sym, e)
    calls, puts = ch.get("calls", []), ch.get("puts", [])
    if not calls or not puts:
        return {}
    k = min((c["strike"] for c in calls), key=lambda x: abs(x - S))
    mid = lambda o: (o.get("bid") or 0) and (o.get("ask") or 0) and (o["bid"] + o["ask"]) / 2 or o.get("last") or 0  # noqa: E731
    c = next((o for o in calls if o["strike"] == k), None)
    p = next((o for o in puts if o["strike"] == k), None)
    if not c or not p:
        return {}
    st = mid(c) + mid(p)
    return {"spot": S, "strike": k, "expiry": dt.datetime.utcfromtimestamp(e).strftime("%d %b"), "straddle": st, "move": st / S if S else None,
            "iv": ((c.get("iv") or 0) + (p.get("iv") or 0)) / 2}


def earnings_board(scores: list[dict[str, Any]], days: float = 8, top: int = 22) -> list[dict[str, Any]]:
    from app.invest import gamma

    cands = [s for s in scores if s["earn_days"] is not None and -0.3 <= s["earn_days"] <= days]
    # most interesting first: big history of earnings moves + liquid
    for s in cands:
        s["hist"] = earnings_hist(s["sym"])
    cands = [s for s in cands if s["hist"].get("n", 0) >= 4]
    cands.sort(key=lambda s: -(s["hist"]["med"] * (1 + s["hist"]["p10"])))
    out = []

    def one(s: dict[str, Any]) -> dict[str, Any] | None:
        try:
            q = _quotes([s["sym"]]).get(s["sym"], {})
            et = q.get("earningsTimestampStart") or q.get("earningsTimestamp")
            im = implied_move(s["sym"], et)
            g = gamma.compute(s["sym"])
            return {**s, "implied": im, "gamma": {k: g.get(k) for k in ("regime", "net_gex", "zero_gamma", "call_wall", "put_wall", "spot")} if g.get("ok") else {}}
        except Exception as e:  # noqa: BLE001
            log.warning("board %s: %s", s["sym"], e)
            return None
    with ThreadPoolExecutor(4) as ex:
        for r in ex.map(one, cands[:top]):
            if r:
                out.append(r)
    return out


def gamma_top(scores: list[dict[str, Any]], n: int = 12) -> list[dict[str, Any]]:
    from app.invest import gamma

    out = []
    for s in [x for x in scores if x["earn_days"] is None or x["earn_days"] > 1.6][:n]:
        try:
            g = gamma.compute(s["sym"])
            if g.get("ok"):
                out.append({**s, "gamma": {k: g.get(k) for k in ("regime", "net_gex", "zero_gamma", "call_wall", "put_wall", "spot")}})
        except Exception:  # noqa: BLE001
            pass
    return out


# ------------------------------------------------------------------------------------------------ short interest (FINRA, 2026 only)
def short_interest_test() -> dict[str, Any]:
    from app.intel.bigmoves_study import load_events

    files = sorted(glob.glob(os.path.join(os.path.dirname(OUT), "finra_si", "*.csv")))
    if not files:
        return {}
    snaps = []
    for f in files:
        x = pd.read_csv(f, sep="|", usecols=["symbolCode", "currentShortPositionQuantity", "averageDailyVolumeQuantity", "daysToCoverQuantity", "changePercent", "settlementDate"])
        snaps.append(x)
    S = pd.concat(snaps)
    S["settlementDate"] = pd.to_datetime(S["settlementDate"])
    S["pub"] = S["settlementDate"] + pd.Timedelta(days=9)  # FINRA publishes ~7 business days after settlement
    U = universe()
    E = load_events()
    C = pd.read_parquet(os.path.join(OUT, "control.parquet"))
    lo = S["pub"].min()
    rows = []
    for df, y in ((E, 1), (C, 0)):
        d = df[df["d"] >= lo][["sym", "d", "r"]].copy()
        d["y"] = y
        rows.append(d)
    A = pd.concat(rows).sort_values("d")
    S = S.rename(columns={"symbolCode": "sym"}).sort_values("pub")
    A = pd.merge_asof(A, S[["sym", "pub", "daysToCoverQuantity", "currentShortPositionQuantity", "changePercent"]], left_on="d", right_on="pub", by="sym", direction="backward")
    A = A.dropna(subset=["daysToCoverQuantity"])
    q = _quotes(list(U)[:0])  # no-op (float from profile is slow; days-to-cover is used instead)
    del q
    A["w"] = np.where(A["y"] == 1, 1.0, 1 / 0.06)
    A["dtc_b"] = pd.cut(A["daysToCoverQuantity"], [0, 1, 2, 4, 7, 12, 1e9], labels=["<1", "1-2", "2-4", "4-7", "7-12", "12+"])
    out = []
    for b, g in A.groupby("dtc_b", observed=True):
        out.append({"dtc": str(b), "rate": round(g["y"].sum() / g["w"].sum(), 5), "up": round(((g["y"] == 1) & (g["r"] > 0)).sum() / g["w"].sum(), 5),
                    "events": int(g["y"].sum())})
    base = A["y"].sum() / A["w"].sum()
    return {"rows": out, "base": round(base, 5), "from": str(lo.date()), "n_events": int(A["y"].sum())}


# ------------------------------------------------------------------------------------------------ case-study news
def headlines(sym: str, name: str, day: str, n: int = 4) -> list[dict[str, Any]]:
    from app.api import invest_routes as ir

    d = pd.Timestamp(day)
    a, b = (d - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), (d + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    out = []
    for q in (f'"{sym}" stock after:{a} before:{b}', f'{(name or sym).split(",")[0]} shares after:{a} before:{b}'):
        try:
            for it in ir._google_news(q):
                t = pd.Timestamp(it["ts"], unit="s")
                if d - pd.Timedelta(days=1.5) <= t <= d + pd.Timedelta(days=2):
                    if it["title"] not in [o["title"] for o in out]:
                        out.append({"title": it["title"], "source": it["source"], "ts": int(it["ts"])})
        except Exception as e:  # noqa: BLE001
            log.warning("news %s: %s", sym, e)
        if len(out) >= n:
            break
        time.sleep(0.6)
    return out[:n]


def pick_cases(E: pd.DataFrame, per: int = 1, since: str = "2026-06-01") -> list[dict[str, Any]]:
    """One or two clear, recent examples per cause and direction (largest liquid ones), plus classic market-wide days."""
    rec = E[E["d"] >= since].copy()
    rec = rec[rec["cap"] > 1.5e9]
    picks = []
    for (c, d), g in rec.groupby(["cause", "dir"]):
        g = g.sort_values("dv20", ascending=False).head(40).sort_values("r", key=lambda x: -x.abs())
        for _, e in g.head(per).iterrows():
            picks.append(e)
    return [p.to_dict() for p in picks]


def run(R: dict[str, Any]) -> dict[str, Any]:
    from app.intel.bigmoves_study import load_events

    L: dict[str, Any] = {"ts": int(time.time())}
    scores = score_today(R)
    L["watch"] = [{k: v for k, v in s.items()} for s in scores[:40]]
    L["n_scored"] = len(scores)
    log.info("scored %d", len(scores))
    L["earnings"] = earnings_board(scores)
    log.info("earnings board %d", len(L["earnings"]))
    L["gamma_top"] = gamma_top(scores)
    try:
        L["short"] = short_interest_test()
    except Exception as e:  # noqa: BLE001
        log.warning("si: %s", e)
        L["short"] = {}
    E = load_events()
    cases = pick_cases(E)
    for cse in cases:
        cse["news"] = headlines(cse["sym"], cse.get("name"), str(pd.Timestamp(cse["d"]).date()))
    L["cases"] = [{k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in c.items() if not isinstance(v, (np.ndarray,))} for c in cases]
    json.dump(L, open(os.path.join(OUT, "live.json"), "w"), indent=1, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else (o.item() if hasattr(o, "item") else str(o)))
    return L


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    R = json.load(open(os.path.join(OUT, "results.json")))
    run(R)


NEWS_TYPES = [
    ("Analyst up/downgrade or target", ("upgrade", "downgrade", "price target", "initiat", "overweight", "underweight", "outperform", "buy rating", "sell rating", "neutral")),
    ("Earnings / guidance (own or pre-announced)", ("earnings", "guidance", "outlook", "revenue", "quarter", "results", "forecast", "eps", "beats", "misses")),
    ("Deal / M&A / partnership", ("acquire", "acquisition", "merger", "buyout", "takeover", "deal", "partnership", "partner", "stake", "bid", "agreement")),
    ("Contract / order / government", ("contract", "order", "award", "pentagon", "government", "army", "navy", "nasa", "department of")),
    ("FDA / clinical / drug", ("fda", "trial", "phase", "approval", "drug", "data readout", "clinical")),
    ("Offering / financing", ("offering", "priced", "dilut", "convertible", "raise", "private placement")),
    ("Short report / fraud / legal", ("short seller", "short report", "fraud", "lawsuit", "investigation", "sec probe", "subpoena", "doj")),
    ("AI / tech hype", (" ai ", "artificial intelligence", "data center", "nvidia", "openai", "quantum", "chip")),
    ("Crypto", ("bitcoin", "crypto", "ether", "btc", "stablecoin")),
    ("Macro / policy / tariffs / rates", ("tariff", "fed ", "rate cut", "inflation", "cpi", "jobs report", "trump", "china", "sanction", "oil price")),
    ("Sector / peer sympathy", ("shares of", "peers", "sector", "sympathy", "rival", "industry")),
]


def news_type(titles: list[str]) -> str:
    t = " " + " ".join(titles).lower() + " "
    best, score = "Other news", 0
    for name, kws in NEWS_TYPES:
        sc = sum(t.count(k) for k in kws)
        if sc > score:
            best, score = name, sc
    return best


def news_audit(since: str = "2026-07-15", min_cap: float = 2e9, limit: int = 160) -> dict[str, Any]:
    """Headlines for recent stock-specific +-10% days that left no SEC filing: what kind of news was it, or was there none?"""
    from app.intel.bigmoves_study import load_events

    E = load_events()
    g = E[(E["d"] >= since) & (E["cap"] >= min_cap) & E["cause"].isin(["No filing (news / analyst / flow)", "Sector / industry move", "No SEC data (foreign/ADR)"])]
    g = g.sort_values("dv20", ascending=False).head(limit)
    rows = []
    for _, e in g.iterrows():
        hs = headlines(e["sym"], e.get("name"), str(e["d"].date()), n=5)
        typ = news_type([h["title"] for h in hs]) if hs else "No headline found"
        rows.append({"sym": e["sym"], "d": str(e["d"].date()), "r": float(e["r"]), "cause": e["cause"], "type": typ, "n_head": len(hs),
                     "head": hs[0]["title"] if hs else "", "ab5": None if pd.isna(e["ab5"]) else float(e["ab5"]), "clv": float(e["clv"]), "vrel": float(e["vrel"]) if pd.notna(e["vrel"]) else None})
    A = pd.DataFrame(rows)
    summ = []
    if not A.empty:
        for t, x in A.groupby("type"):
            summ.append({"type": t, "n": int(len(x)), "share": round(len(x) / len(A), 3), "up": int((x["r"] > 0).sum()), "avg_abs": round(float(x["r"].abs().mean()), 4),
                         "ab5": None if x["ab5"].dropna().empty else round(float(x["ab5"].mean()), 4)})
        summ.sort(key=lambda r: -r["n"])
    out = {"since": since, "n": len(rows), "summary": summ, "rows": rows}
    json.dump(out, open(os.path.join(OUT, "news_audit.json"), "w"), indent=1)
    return out


UPW = ("soar", "surge", "jump", "rall", "gain", "rise", "climb", "spike", "rocket", "skyrocket", "higher", "pop", "boost", "lift", "up ", "record high", "beat")
DNW = ("fall", "drop", "plunge", "sink", "tumble", "slide", "lower", "crash", "slump", "plummet", "down ", "sell-off", "selloff", "miss", "cut", "warn", "lowest")


def _dir_score(titles: list[str], up: bool) -> int:
    t = " ".join(titles).lower() + " "
    u, d = sum(t.count(w) for w in UPW), sum(t.count(w) for w in DNW)
    return (u - d) if up else (d - u)


def refresh_cases(since: str = "2026-05-01", tries: int = 4) -> list[dict[str, Any]]:
    """Re-pick the case studies: per cause and direction, the first liquid example whose headlines agree with the direction of the move."""
    from app.intel.bigmoves_study import load_events

    E = load_events()
    rec = E[(E["d"] >= since) & (E["cap"] > 1.5e9)]
    picks = []
    for (c, d), g in rec.groupby(["cause", "dir"]):
        g = g.sort_values("dv20", ascending=False).head(30).sort_values("r", key=lambda x: -x.abs())
        best = None
        for _, e in g.head(tries).iterrows():
            hs = headlines(e["sym"], e.get("name"), str(e["d"].date()), n=6)
            sc = _dir_score([h["title"] for h in hs], e["r"] > 0)
            if best is None or sc > best[0]:
                best = (sc, e, hs)
            if sc >= 2:
                break
        if best:
            sc, e, hs = best
            ok = [h for h in hs if _dir_score([h["title"]], e["r"] > 0) >= 0][:4]
            row = {k: (str(v) if isinstance(v, pd.Timestamp) else (None if isinstance(v, float) and not np.isfinite(v) else v)) for k, v in e.to_dict().items()}
            row["news"] = ok
            picks.append(row)
    L = json.load(open(os.path.join(OUT, "live.json")))
    L["cases"] = picks
    json.dump(L, open(os.path.join(OUT, "live.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return picks
