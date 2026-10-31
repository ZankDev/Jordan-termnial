import sys, json, glob, gc
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.strategies.composed import CombinedStrategy
from app.optimizer import pa_objective as pa
cands = []
for f in sorted(glob.glob(str(rp.OUT / "cands*.json"))): cands += json.loads(open(f, encoding="utf-8").read())["kept"]
def find(sym, name, trn):
    for r in cands:
        if r["symbol"] == sym and r["name"] == name and r["train"]["n"] == trn: return r
PLANS = {"NQ": [("orb_retest", 837), ("ny_open_breakout", 603), ("ny_open_breakout", 507)]}
tg = pa.targets()
FORCE = len(sys.argv) > 1 and sys.argv[1] == 'force'
for sym, plan in PLANS.items():
    df = rp._load(sym)[0]; cut = df.index.min() + (df.index.max() - df.index.min()) * 0.6
    days = [str(d.date()) for d in pd.bdate_range(cut.tz_localize(None).normalize(), "2026-10-05")]
    a = find(sym, *plan[0])
    for nb in plan[1:]:
        b = find(sym, *nb)
        if not (a and b): print(sym, "missing", plan[0], nb); continue
        orb, nyo = registry.get("orb_retest"), registry.get("ny_open_breakout")
        combo = CombinedStrategy("c", [orb, nyo])
        params = {**{"a_" + k: v for k, v in a["params"].items()}, **{"b_" + k: v for k, v in b["params"].items()}}
        print("  RR as found: orb", round(a["params"]["tp_mult"]/a["params"]["sl_mult"],2), "nyob", round(b["params"]["tp_mult"]/b["params"]["sl_mult"],2))
        if FORCE:
            for pre in ("a_", "b_"): params[pre + "tp_mult"] = round(2 * params[pre + "sl_mult"], 2)
        res = run_backtest(df, combo, params, sym, 50000.0, sizing=default_config(50000.0)).trades
        rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"]), sym) for t in res]
        trn = [x for x in rows if pd.Timestamp(x[0]) < cut]; te = [x for x in rows if pd.Timestamp(x[0]) >= cut]
        d, n = pa.daily_rules(te, 600.0, 1200.0, 1.0); v = np.array(list(d.values())); w, l = v[v > 0], v[v < 0]
        m = pa.evaluate(te, days, 1.0, tg, boots=2000)
        print(f"{sym} orb(n={plan[0][1]}) + nyob(n={nb[1]})  train {stats([(x[0],x[1],x[2]) for x in trn])}  TEST {stats([(x[0],x[1],x[2]) for x in te])}")
        print(f"   trading days {len(v)}/{len(days)} | winning days {len(w)} avg +${w.mean():,.0f} | losing days {len(l)} avg -${abs(l.mean()):,.0f} worst -${abs(l.min()):,.0f} | net/day ${m['mean']} | max DD ${m['dd']} | drawdown breach 60d {m['breach60']*100:.0f}%", flush=True)
        import collections
        te_t = [t for t in res if pd.Timestamp(t["entry_time"]) >= cut]
        wn = [t["pnl"] for t in te_t if t["pnl"] > 0]; ls = [t["pnl"] for t in te_t if t["pnl"] <= 0]
        print("   realised: avg win $%.0f, avg loss $%.0f -> real reward:risk %.2f | exits %s" % (np.mean(wn), abs(np.mean(ls)), np.mean(wn) / abs(np.mean(ls)), dict(collections.Counter(t["exit_reason"] for t in te_t))), flush=True)
    del df; gc.collect()
