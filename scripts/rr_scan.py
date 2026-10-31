import sys, json, glob, collections
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.strategies.base import Strategy
def rr(p):
    sl = p.get("sl_mult", p.get("b_sl_mult")); tp = p.get("tp_mult", p.get("b_tp_mult"))
    return (tp / sl) if sl and tp else None
cands = []
for f in sorted(glob.glob(str(rp.OUT / "cands*.json"))): cands += json.loads(open(f, encoding="utf-8").read())["kept"]
sel = [r for r in cands if rr(r["params"]) is not None and 1.8 <= rr(r["params"]) <= 2.6 and not r["name"].endswith("_flip") and r["train"]["n"] >= 60]
print(len(sel), "candidates with target/stop between 1 and 2 (non-flip)")
import gc
dfs = {}; cut = {}
def get(sym):
    for k in list(dfs):
        if k != sym: del dfs[k]; gc.collect()
    if sym not in dfs:
        dfs[sym] = rp._load(sym)[0]; d = dfs[sym]; cut[sym] = d.index.min() + (d.index.max() - d.index.min()) * 0.6
    return dfs[sym]
out = []
for r in sorted(sel, key=lambda r: (r['symbol'], -r['train']['pf']))[:36]:
    sym, name = r["symbol"], r["name"]
    df_ = get(sym)
    tr = registry.get(name).generate_trades(df_, r["params"])
    class F(Strategy):
        def generate_trades(self, d, q): return tr
    res = run_backtest(df_, F(name="x", description="", params=[]), r["params"], sym, 50000.0, sizing=default_config(50000.0)).trades
    rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"])) for t in res]
    te = [x for x in rows if pd.Timestamp(x[0]) >= cut[sym]]; trn = [x for x in rows if pd.Timestamp(x[0]) < cut[sym]]
    days = max(1, np.busday_count(cut[sym].date(), pd.Timestamp("2026-10-05").date()))
    st = stats(te)
    out.append((st.get("net", 0) / days, sym, name, round(rr(r["params"]), 2), stats(trn), st, round(st["n"] / days, 2)))
for o in sorted(out, key=lambda o: -o[0])[:12]: print(f"${o[0]:5.1f}/day  {o[1]} {o[2]:22s} RR {o[3]}  train {o[4]}  test {o[5]}  trades/day {o[6]}")
