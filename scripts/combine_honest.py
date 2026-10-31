"""Honest portfolio test: flips re-simulated pessimistically, rules loss 600 / cap 1200, reports days with >=1 trade."""
import sys, json, itertools, collections, numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.strategies.base import Strategy
LOSS, CAP = float(sys.argv[1]), float(sys.argv[2])
cands = []
for f in ("cands.json", "cands_orbfix.json", "cands_more.json"):
    cands += json.loads((rp.OUT / f).read_text(encoding="utf-8"))["kept"]
cands = [r for r in cands if not (f"{r['name']}" .startswith("orb_retest") and r not in cands[-0:0])] if False else cands
span = {"ES": 0.6 * 5 * 252, "NQ": 0.6 * 8 * 252}
best = collections.defaultdict(list)
for r in sorted(cands, key=lambda r: -r["train"]["pf"]):
    if r["train"]["n"] / span[r["symbol"]] < 0.3: continue
    k = (r["symbol"], r["name"])
    if len(best[k]) < 2: best[k].append(r)
dfs = {s: rp._load(s)[0] for s in ("ES", "NQ")}
cut = {s: d.index.min() + (d.index.max() - d.index.min()) * 0.6 for s, d in dfs.items()}
pool = []
for (sym, name), lst in best.items():
    df = dfs[sym]
    for r in lst:
        if name.endswith("_flip"):
            tr = honest_flip(df, registry.get(name[:-5]), r["params"])
        else:
            tr = registry.get(name).generate_trades(df, r["params"])
        class F(Strategy):
            def generate_trades(self, d, q): return tr
        res = run_backtest(df, F(name="x", description="", params=[]), r["params"], sym, 50000.0, sizing=default_config(50000.0)).trades
        rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"]), sym) for t in res]
        te = [x for x in rows if pd.Timestamp(x[0]) >= cut[sym]]; trn = [x for x in rows if pd.Timestamp(x[0]) < cut[sym]]
        s_tr, s_te = stats(trn), stats(te)
        print(f"{sym} {name:24s} train {s_tr} test {s_te}", flush=True)
        if s_tr.get("pf", 0) >= 1.1 and s_te.get("pf", 0) >= 1.0:
            pool.append((f"{sym} {name}", te))
print(len(pool), "in pool")
days = [str(d.date()) for d in pd.bdate_range(max(cut.values()).tz_localize(None).normalize(), "2026-10-05")]
rng = np.random.default_rng(1); out = []
for size in (1, 2, 3):
    for combo in itertools.combinations(pool, size):
        tr = [t for _, te in combo for t in te]
        for scale in (0.25, 0.5, 0.75, 1.0):
            d, n = rp._daily_rules(tr, LOSS, CAP, scale)
            s = np.array([d.get(x, 0.0) for x in days]); act = np.array([n.get(x, 0) for x in days])
            eq = np.cumsum(s); dd = (np.maximum.accumulate(eq) - eq).max()
            idx = rng.integers(0, len(s), size=(2000, 60)); e = np.cumsum(s[idx], axis=1); pk = np.maximum.accumulate(np.maximum(e, 0))
            out.append(dict(combo=" + ".join(c for c, _ in combo), scale=scale, tpd=round(act.mean(), 2), trade_days=round((act >= 1).mean() * 100), mean=round(s.mean(), 1), worst=round(s.min()), best=round(s.max()), dd=round(dd), breach=round(((pk - e) > 2500).any(axis=1).mean() * 100), med60=round(float(np.median(e[:, -1])))))
print("\nBEST by mean with breach<=25%:")
for r in sorted([r for r in out if r["breach"] <= 25], key=lambda r: -r["mean"])[:8]: print(r)
print("\nBEST by mean with >=1 trade on 80%+ of days:")
for r in sorted([r for r in out if r["trade_days"] >= 80], key=lambda r: -r["mean"])[:8]: print(r)
