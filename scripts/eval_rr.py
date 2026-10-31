"""Evaluate saved candidates at $300 risk: win rate, exit mix (target/stop/session close), winning/losing days, trades per day."""
import sys, json, gc, collections
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.optimizer import pa_objective as pa
files = sys.argv[1].split(","); RISK = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
cands = []
for f in files: cands += json.loads((rp.OUT / f).read_text(encoding="utf-8"))["kept"]
tg = pa.targets(); out = []
for sym in ("NQ", "ES"):
    mine = [r for r in cands if r["symbol"] == sym and r["train"]["pf"] >= 1.1]
    if not mine: continue
    df = rp._load(sym)[0]; cut = df.index.min() + (df.index.max() - df.index.min()) * 0.6
    ndays = len([d for d in pd.bdate_range(cut.tz_localize(None).normalize(), "2026-10-05")])
    for r in mine:
        res = run_backtest(df, registry.get(r["name"]), r["params"], sym, 50000.0, sizing=default_config(50000.0, risk_dollars=RISK)).trades
        te = [t for t in res if pd.Timestamp(t["entry_time"]) >= cut]
        if len(te) < 40: continue
        rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"]), sym) for t in te]
        d, n = pa.daily_rules(rows, 600.0, 1200.0, 1.0); v = np.array(list(d.values())); w, l = v[v > 0], v[v < 0]
        if not len(w) or not len(l): continue
        ex0 = collections.Counter(t['exit_reason'] for t in te)
        if (ex0['target'] + ex0['stop']) / len(te) < 0.70: continue   # real stop/target trades only
        pnl = np.array([t["pnl"] for t in te]); ex = collections.Counter(t["exit_reason"] for t in te)
        out.append(dict(sym=sym, name=r["name"], n=len(te), wr=round((pnl > 0).mean() * 100), pf=round(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum(), 2), net=round(pnl.sum()),
                        tgt=round(ex["target"] / len(te) * 100), stp=round(ex["stop"] / len(te) * 100), tpd=round(len(te) / ndays, 2),
                        wd=len(w), avg_w=round(w.mean()), ld=len(l), avg_l=round(abs(l.mean())), worst=round(abs(l.min())), params=r["params"]))
    del df; gc.collect()
for o in sorted(out, key=lambda o: -o["net"])[:12]:
    print(f"{o['sym']} {o['name']:20s} test trades {o['n']:4d} ({o['tpd']}/day) WR {o['wr']}% PF {o['pf']} net ${o['net']:,} | exits: target {o['tgt']}% stop {o['stp']}% | win days {o['wd']} avg +${o['avg_w']} | loss days {o['ld']} avg -${o['avg_l']} worst -${o['worst']}")
json.dump(out, open(rp.OUT / "eval_rr_last.json", "w"))
