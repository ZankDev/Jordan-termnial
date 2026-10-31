"""Portfolio research: can ES + NQ opening-range strategies reach ~1-2 trades a day and a ~$300 average day inside a $200 daily loss limit / $600 daily cap?

Stage 1 (this file, `search`): for ES and NQ and each of orb_retest / ny_open_breakout / orb_fade (and the flipped version of each) draw random parameter sets,
backtest each over the whole verified Dukascopy history (risk-sized micro contracts at $500 risk, costs and slippage), and keep per-candidate stats split into a TRAIN part
(first 60% of the history, used to choose) and a TEST part (last 40%, never used to choose). The trades of every candidate that clears the train screen are saved.
Stage 2 (`combine`): build 1-3 member portfolios from the saved trades, apply the daily rules trade by trade, scale the size, and report trades per day,
average day, share of days at the targets, and the chance of breaching a $2,500 trailing drawdown in 60 trading days - on the TEST period.

usage:  python scripts/research_portfolio.py search [N_per_family]     ->  data_cache/research/cands.json
        python scripts/research_portfolio.py combine                     ->  prints the report
Reads the market data cache only; it never touches the accounts or the live database.
"""
from __future__ import annotations

import collections
import itertools
import json
import multiprocessing as mp
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data_cache" / "research"
FAMILIES = ["orb_retest", "ny_open_breakout", "orb_fade"]
_DF: dict = {}


def _load(symbol: str):
    from app.optimizer import promotion

    if os.environ.get("ALLDAY"):  # 24-hour history: the NY-session cache + the overnight cache, over the window the overnight download covers
        from app.data import dukascopy_source as dk, overnight_source as ov

        files = sorted(ov.OUT.glob(f"{dk.SYMBOL_MAP[symbol]}_5m_*.parquet"))
        df = ov.all_day_bars(symbol, promotion._deep_df(symbol, promotion.deep_plan(symbol)[0]))
        if files:
            import pandas as pd

            ym = sorted(int(f.stem.rsplit("_", 1)[1]) for f in files)           # YYYYMM of every downloaded month
            runs, cur = [], [ym[0]]
            for v in ym[1:]:                                                    # contiguous runs of downloaded months
                nxt = cur[-1] + 1 if cur[-1] % 100 < 12 else (cur[-1] // 100 + 1) * 100 + 1
                if v == nxt:
                    cur.append(v)
                else:
                    runs.append(cur); cur = [v]
            runs.append(cur)
            run = max(runs, key=len)                                            # use the longest contiguous run
            lo = pd.Timestamp(f"{run[0] // 100}-{run[0] % 100:02d}-01", tz="America/New_York")
            last = run[-1]
            hi = pd.Timestamp(f"{last // 100 + (last % 100 == 12)}-{(last % 100) % 12 + 1:02d}-01", tz="America/New_York")
            df = df[(df.index >= lo) & (df.index < hi)]
            print(f"all-day window {symbol}: {lo.date()} -> {hi.date()} ({len(run)} months)", flush=True)
        return df, 0

    months = promotion.deep_plan(symbol)[0]
    return promotion._deep_df(symbol, months), months


def _init(symbols):
    for s in symbols:
        _DF[s] = _load(s)[0]


def _sample(strat, rnd: random.Random) -> dict:
    p = {}
    for sp in strat.params:
        n = int(round((sp.max - sp.min) / sp.step))
        p[sp.name] = sp.cast(sp.min + rnd.randint(0, n) * sp.step)
    if os.environ.get("LATE"):  # trades must be able to live: close the session at 15:00 or 16:00, tight stops
        for pre in ("", "b_"):
            if pre + "session_end_hour" in p:
                p[pre + "session_end_hour"] = rnd.choice([15, 16])
            if pre + "sl_mult" in p:
                p[pre + "sl_mult"] = min(p[pre + "sl_mult"], 0.5 + 0.25 * rnd.randint(0, 3))
    if os.environ.get("RR2") or os.environ.get("RR"):  # force the reward:risk - target exactly RR x the stop
        for pre in ("", "b_"):
            if pre + "sl_mult" in p and pre + "tp_mult" in p:
                p[pre + "tp_mult"] = round(min(5.0, float(os.environ.get("RR", 2)) * p[pre + "sl_mult"]), 2)
    return p


def _run(job):
    symbol, name, params = job
    import numpy as np
    import pandas as pd

    from app.backtest.engine import run_backtest
    from app.backtest.sizing import default_config
    from app.strategies import registry

    df = _DF[symbol]
    try:
        tr = run_backtest(df, registry.get(name), params, symbol, 50_000.0, sizing=default_config(50_000.0)).trades
    except Exception:  # noqa: BLE001
        return None
    if len(tr) < 40:
        return None
    t0, t1 = df.index.min(), df.index.max()
    cut = t0 + (t1 - t0) * 0.6
    rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"])) for t in tr]

    def stats(sel):
        pn = np.array([r[2] for r in sel])
        if len(pn) < 5:
            return {"n": len(pn), "pf": 0.0, "net": float(pn.sum()) if len(pn) else 0.0}
        w, l = pn[pn > 0].sum(), -pn[pn < 0].sum()
        yrs = collections.defaultdict(float)
        for r in sel:
            yrs[r[1][:4]] += r[2]
        return {"n": len(pn), "net": round(float(pn.sum())), "pf": round(float(w / l), 2) if l > 0 else 99.0, "yp": sum(v > 0 for v in yrs.values()), "yn": len(yrs)}

    cutts = pd.Timestamp(cut)
    train = [r for r in rows if pd.Timestamp(r[0]) < cutts]
    test = [r for r in rows if pd.Timestamp(r[0]) >= cutts]
    return {"symbol": symbol, "name": name, "params": params, "train": stats(train), "test": stats(test), "all": stats(rows), "trades": rows}


def search(n_per_family: int = 150, families=None, tag: str = "cands") -> None:
    from app.strategies import registry

    OUT.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(2026)
    jobs = []
    for symbol in os.environ.get("SYMS", "ES,NQ").split(","):
        for fam in (families or FAMILIES):
            for name in ((fam,) if os.environ.get("NOFLIP") else (fam, fam + "_flip")):
                strat = registry.get(name)
                seen = set()
                while len(seen) < n_per_family:
                    p = _sample(strat, rnd)
                    k = json.dumps(p, sort_keys=True)
                    if k in seen:
                        continue
                    seen.add(k)
                    jobs.append((symbol, name, p))
    print(f"{len(jobs)} backtests on {mp.cpu_count()} cores", flush=True)
    t = time.time()
    res = []
    with mp.Pool(min(int(os.environ.get("NPROC", "11")), mp.cpu_count()), initializer=_init, initargs=(tuple(os.environ.get("SYMS", "ES,NQ").split(",")),)) as pool:
        for i, r in enumerate(pool.imap_unordered(_run, jobs, chunksize=4)):
            if r:
                res.append(r)
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)}  {time.time() - t:.0f}s  kept {len(res)}", flush=True)
    # keep what clears a TRAIN screen (never the test): enough trades, a real profit factor, mostly profitable years
    keep = [r for r in res if r["train"]["n"] >= 80 and r["train"]["pf"] >= 1.25 and r["train"].get("yp", 0) >= max(2, int(0.7 * r["train"].get("yn", 1)))]
    keep.sort(key=lambda r: -r["train"]["pf"])
    (OUT / f"{tag}.json").write_text(json.dumps({"all_count": len(res), "kept": keep}), encoding="utf-8")
    print(f"done in {time.time() - t:.0f}s: {len(res)} valid, {len(keep)} pass the train screen", flush=True)
    by = collections.Counter((r["symbol"], r["name"]) for r in keep)
    for k, v in sorted(by.items()):
        print(" ", k, v)


# ------------------------------------------------------------------ stage 2
def _daily_rules(trades, loss_limit=200.0, profit_cap=600.0, scale=1.0):
    """trades: [(entry, exit, pnl, symbol)] -> list of (date, pnl) after the daily rules. A trade is skipped if the day is already at the loss limit / profit cap
    when it would start (only trades that have already closed count), and a trade never overlaps another one on the same symbol."""
    import pandas as pd

    ts = sorted(((pd.Timestamp(a), pd.Timestamp(b), p * scale, s) for a, b, p, s in trades), key=lambda x: x[0])
    last_exit: dict[str, object] = {}
    by_day: dict[str, list] = collections.defaultdict(list)
    out: dict[str, float] = collections.defaultdict(float)
    n: dict[str, int] = collections.defaultdict(int)
    for a, b, p, s in ts:
        day = str(a.date())
        if s in last_exit and a < last_exit[s]:
            continue
        closed = sum(x for (e, x) in by_day[day] if e <= a)
        if closed <= -loss_limit or closed >= profit_cap:
            continue
        by_day[day].append((b, p))
        last_exit[s] = b
        out[day] += p
        n[day] += 1
    return out, n


def combine() -> None:
    import numpy as np
    import pandas as pd

    data = json.loads((OUT / "cands.json").read_text(encoding="utf-8"))
    cands = [r for r in data["kept"] if not r["name"].startswith("orb_retest")]  # old orb_retest numbers came from the invalid fill model
    for extra in ("cands_orbfix.json", "cands_more.json"):
        fx = OUT / extra
        if fx.exists():
            cands += json.loads(fx.read_text(encoding="utf-8"))["kept"]
    print(f"{len(cands)} candidates passed the train screen out of {data['all_count']} valid")
    # TEST window per symbol = after the 60% cut
    cuts = {}
    for s in ("ES", "NQ"):
        df, _ = _load(s)
        cuts[s] = df.index.min() + (df.index.max() - df.index.min()) * 0.6
        print(f"{s}: test window starts {cuts[s].date()}")
    # one best (by TRAIN pf) candidate per (symbol, base family) with its flip counted as its own family
    best = {}
    span = {"ES": 0.6 * 5 * 252, "NQ": 0.6 * 8 * 252}  # trading days in the TRAIN window
    for r in cands:
        if r["train"]["n"] / span[r["symbol"]] < 0.45:
            continue  # frequency screen on TRAIN too: the goal is 1-2 trades a day, so rare-trade configs are not candidates
        k = (r["symbol"], r["name"])
        if k not in best or r["train"]["pf"] > best[k]["train"]["pf"]:
            best[k] = r
    pool = list(best.values())
    for r in pool:
        print(f"  {r['symbol']} {r['name']:22s} train n={r['train']['n']:4d} PF {r['train']['pf']:5.2f} | TEST n={r['test']['n']:4d} PF {r['test']['pf']:5.2f} net ${r['test']['net']:>7,}")

    def test_trades(r):
        c = pd.Timestamp(cuts[r["symbol"]])
        return [(a, b, p, r["symbol"]) for a, b, p in r["trades"] if pd.Timestamp(a) >= c]

    # weekdays in the test window (the denominator of "per day")
    days = pd.bdate_range(max(cuts.values()).tz_localize(None).normalize(), pd.Timestamp.today().normalize())
    days = [str(d.date()) for d in days]
    rng = np.random.default_rng(3)
    results = []
    for size in (1, 2, 3):
        for combo in itertools.combinations(pool, size):
            if len({(r["symbol"], r["name"].replace("_flip", "")) for r in combo}) < size:
                continue  # the same strategy and its own flip is one bet
            trades = [t for r in combo for t in test_trades(r)]
            for scale in (1.0, 1.5, 2.0, 3.0, 4.0, 6.0):
                d, n = _daily_rules(trades, 400.0, 600.0, scale)
                series = np.array([d.get(x, 0.0) for x in days])
                tpd = sum(n.values()) / len(days)
                # 60-trading-day bootstrap of the daily results against a $2,500 trailing drawdown that locks at the start
                idx = rng.integers(0, len(series), size=(2000, 60))
                eq = np.cumsum(series[idx], axis=1)
                peak = np.maximum.accumulate(np.maximum(eq, 0))
                breach = float(((peak - eq) > 2500).any(axis=1).mean())
                results.append({"combo": " + ".join(f"{r['symbol']} {r['name']}" for r in combo), "scale": scale, "tpd": round(tpd, 2), "mean": round(series.mean(), 1),
                                "p300": round(float((series >= 300).mean()) * 100), "ploss": round(float((series <= -400).mean()) * 100), "breach60": round(breach * 100, 1),
                                "days_active": round(float((series != 0).mean()) * 100)})
    (OUT / "combos.json").write_text(json.dumps(results), encoding="utf-8")
    print("\n=== best by average day (trades/day >= 1.0), TEST period only ===")
    ok = [r for r in results if r["tpd"] >= 1.0]
    for r in sorted(ok, key=lambda r: -r["mean"])[:12]:
        print(r)
    print("\n=== best by average day with breach risk <= 10% (any trades/day) ===")
    for r in sorted([r for r in results if r["breach60"] <= 10], key=lambda r: -r["mean"])[:12]:
        print(r)
    print("\n=== highest mean day overall ===")
    for r in sorted(results, key=lambda r: -r["mean"])[:5]:
        print(r)


if __name__ == "__main__":
    mp.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        search(int(sys.argv[2]) if len(sys.argv) > 2 else 150, sys.argv[3].split(",") if len(sys.argv) > 3 else None, sys.argv[4] if len(sys.argv) > 4 else "cands")
    elif len(sys.argv) > 1 and sys.argv[1] == "combine":
        combine()
    else:
        print(__doc__)
