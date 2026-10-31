import sys, json, collections
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
c = json.loads((rp.OUT / "cands_rr2.json").read_text(encoding="utf-8"))["kept"]
r = [x for x in c if x["symbol"] == "NQ" and x["name"] == "orb_retest"][0]
print("params", r["params"])
df = rp._load("NQ")[0]
strat = registry.get("orb_retest")
tr = strat.generate_trades(df, r["params"])
print("raw strategy trades:", len(tr), collections.Counter(t.exit_reason for t in tr))
for t in tr[:5]:
    print(t.side, t.entry_time, "entry", round(t.entry_price, 1), "stop", round(t.stop_price, 1), "target", round(t.target_price, 1), "exit", round(t.exit_price, 1), t.exit_reason, "| stop dist", round(abs(t.entry_price - t.stop_price), 1), "target dist", round(abs(t.target_price - t.entry_price), 1))
d = np.array([abs(t.entry_price - t.stop_price) for t in tr]); e = np.array([abs(t.target_price - t.entry_price) for t in tr])
print("median stop dist pts", np.median(d), "median target dist pts", np.median(e))
day_rng = df.groupby(df.index.date).apply(lambda x: x.high.max() - x.low.min()); print("median NQ daily range pts", float(day_rng.median()))
