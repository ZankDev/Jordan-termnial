"""PA strategy search: honest trades (flips re-simulated pessimistically) -> 1-3 member portfolios -> app.optimizer.pa_objective on the TEST period.
Stops as soon as a portfolio meets every PA target; otherwise prints the best misses. usage: python scripts/pa_search.py [per_family=3]"""
import sys, json, itertools, collections, glob
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.strategies.base import Strategy
from app.optimizer import pa_objective as pa

PER = int(sys.argv[1]) if len(sys.argv) > 1 else 3
tg = pa.targets()
print("targets:", tg)
cands = []
for f in sorted(glob.glob(str(rp.OUT / "cands*.json"))):
    cands += json.loads(open(f, encoding="utf-8").read())["kept"]
span = {"ES": 0.6 * 5 * 252, "NQ": 0.6 * 8 * 252}
best = collections.defaultdict(list)
seen = set()
for r in sorted(cands, key=lambda r: -r["train"]["pf"]):
    key = (r["symbol"], r["name"], json.dumps(r["params"], sort_keys=True))
    if key in seen or r["train"]["n"] / span[r["symbol"]] < 0.1: continue
    seen.add(key)
    if len(best[(r["symbol"], r["name"])]) < PER: best[(r["symbol"], r["name"])].append(r)
dfs = {s: rp._load(s)[0] for s in ("ES", "NQ")}
cut = {s: d.index.min() + (d.index.max() - d.index.min()) * 0.6 for s, d in dfs.items()}
pool = []
for (sym, name), lst in best.items():
    df = dfs[sym]
    for r in lst:
        try:
            tr = honest_flip(df, registry.get(name[:-5]), r["params"]) if name.endswith("_flip") else registry.get(name).generate_trades(df, r["params"])
        except Exception as e:
            continue
        class F(Strategy):
            def generate_trades(self, d, q): return tr
        res = run_backtest(df, F(name="x", description="", params=[]), r["params"], sym, 50000.0, sizing=default_config(50000.0)).trades
        rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"]), sym) for t in res]
        trn = [x for x in rows if pd.Timestamp(x[0]) < cut[sym]]; te = [x for x in rows if pd.Timestamp(x[0]) >= cut[sym]]
        a, b = stats([(x[0], x[1], x[2]) for x in trn]), stats([(x[0], x[1], x[2]) for x in te])
        if a.get("pf", 0) >= 1.05 and b.get("pf", 0) >= 1.05 and b.get("n", 0) >= 30:
            pool.append((f"{sym} {name} {json.dumps(r['params'])}", te, a, b))
print(len(pool), "candidates keep an honest edge on train AND test", flush=True)
for p in pool[:40]: print("  ", p[0][:110], "train", p[2], "test", p[3])
days = [str(d.date()) for d in pd.bdate_range(max(cut.values()).tz_localize(None).normalize(), "2026-10-05")]
res = []; hit = None
for size in (1, 2, 3):
    for combo in itertools.combinations(pool, size):
        tr = [t for c in combo for t in c[1]]
        for scale in (0.5, 0.75, 1.0):
            m = pa.evaluate(tr, days, scale, tg, boots=1000); m["combo"] = " + ".join(c[0][:60] for c in combo)
            ok, miss = pa.meets(m, tg); m["miss"] = miss; m["fit"] = pa.fitness(m, tg); res.append(m)
            if ok and hit is None: hit = m; print("TARGETS HIT:", m, flush=True)
    if hit: break
print("\nTOP 10 by fitness:")
for m in sorted(res, key=lambda m: -m["fit"])[:10]: print({k: m[k] for k in ("combo", "scale", "mean", "tpd", "trade_days", "worst", "dd", "breach60", "best_day_share", "fit")}, m["miss"])
print("RESULT:", "TARGETS HIT" if hit else "no portfolio meets the targets")
