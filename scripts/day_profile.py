"""Good-day / bad-day profile of every saved candidate: honest test period, $300 risk, daily stop 600 / cap 1200, only real stop/target trades."""
import sys, json, glob, gc, collections
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.optimizer import pa_objective as pa
cands = []
for f in sorted(glob.glob(str(rp.OUT / "cands*.json"))):
    if "day_" in f: continue
    cands += json.loads(open(f, encoding="utf-8").read())["kept"]
seen, uniq = set(), []
for r in sorted(cands, key=lambda r: -r["train"]["pf"]):
    k = (r["symbol"], r["name"], json.dumps(r["params"], sort_keys=True))
    if k in seen or r["name"].endswith("_flip"): continue     # flips are biased: excluded
    seen.add(k); uniq.append(r)
out = []
for sym in ("NQ", "ES"):
    mine = [r for r in uniq if r["symbol"] == sym][:70]
    df = rp._load(sym)[0]; cut = df.index.min() + (df.index.max() - df.index.min()) * 0.6
    for r in mine:
        try: res = run_backtest(df, registry.get(r["name"]), r["params"], sym, 50000.0, sizing=default_config(50000.0, risk_dollars=300.0)).trades
        except Exception: continue
        te = [t for t in res if pd.Timestamp(t["entry_time"]) >= cut]
        if len(te) < 60: continue
        ex = collections.Counter(t["exit_reason"] for t in te)
        if (ex["target"] + ex["stop"]) / len(te) < 0.70: continue            # real stop/target trades only
        rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"]), sym) for t in te]
        d, n = pa.daily_rules(rows, 600.0, 1200.0, 1.0); v = np.array(list(d.values())); w, l = v[v > 0], v[v < 0]
        if len(w) < 20 or len(l) < 20: continue
        pnl = np.array([t["pnl"] for t in te]); wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        out.append(dict(sym=sym, name=r["name"], trades=len(te), wr=round((pnl > 0).mean() * 100), real_rr=round(wins.mean() / abs(losses.mean()), 2), tgt=round(ex["target"] / len(te) * 100), stp=round(ex["stop"] / len(te) * 100),
                        days=len(v), win_day_pct=round(len(w) / len(v) * 100), good=round(w.mean()), bad=round(abs(l.mean())), best=round(w.max()), worst=round(abs(l.min())), net=round(pnl.sum()), params=r["params"]))
    del df; gc.collect()
json.dump(out, open(rp.OUT / "day_profile.json", "w"))
print(len(out), "candidates with real stop/target trades")
for o in sorted(out, key=lambda o: -(o["win_day_pct"] / 100 * o["good"] - (1 - o["win_day_pct"] / 100) * o["bad"]))[:10]:
    print(f"{o['sym']} {o['name']:20s} trades {o['trades']:4d} WR {o['wr']}% realRR {o['real_rr']} (target {o['tgt']}% stop {o['stp']}%) | days {o['days']}: GOOD {o['win_day_pct']}% avg +${o['good']} (best +${o['best']}) | BAD avg -${o['bad']} (worst -${o['worst']}) | net ${o['net']:,}")
