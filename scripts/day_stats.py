import sys, json, glob, gc
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.strategies.base import Strategy
from app.optimizer import pa_objective as pa
cands = []
for f in sorted(glob.glob(str(rp.OUT / "cands*.json"))): cands += json.loads(open(f, encoding="utf-8").read())["kept"]
def find(sym, name, trn):
    for r in cands:
        if r["symbol"] == sym and r["name"] == name and r["train"]["n"] == trn: return r
WANT = [("NQ orb_retest 2:1", "NQ", "orb_retest", 837), ("NQ orb_retest 2.25:1", "NQ", "orb_retest", 497), ("NQ NY-open breakout filtered 2.6:1", "NQ", "ny_open_breakout_f", 296),
        ("NQ NY-open breakout (plain)", "NQ", "ny_open_breakout", 507), ("NQ VWAP flip", "NQ", "vwap_reversion_flip", 1692), ("ES NY-open breakout", "ES", "ny_open_breakout", 152)]
tg = pa.targets()
for sym in ("NQ", "ES"):
    df = rp._load(sym)[0]; cut = df.index.min() + (df.index.max() - df.index.min()) * 0.6
    days = [str(d.date()) for d in pd.bdate_range(cut.tz_localize(None).normalize(), "2026-10-05")]
    for label, s, name, trn in WANT:
        if s != sym: continue
        r = find(sym, name, trn)
        if not r: print(label, "not found"); continue
        tr = honest_flip(df, registry.get(name[:-5]), r["params"]) if name.endswith("_flip") else registry.get(name).generate_trades(df, r["params"])
        class F(Strategy):
            def generate_trades(self, d, q): return tr
        res = run_backtest(df, F(name="x", description="", params=[]), r["params"], sym, 50000.0, sizing=default_config(50000.0)).trades
        rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"]), sym) for t in res if pd.Timestamp(t["entry_time"]) >= cut]
        d, n = pa.daily_rules(rows, tg["loss_limit"], tg["profit_cap"], 1.0)
        v = np.array([x for x in d.values()])
        w, l = v[v > 0], v[v < 0]
        print(f"{label:36s} trading days {len(v):4d} | WINNING days {len(w):3d} ({len(w)/len(v)*100:.0f}%) avg +${w.mean():,.0f} best +${w.max():,.0f} | LOSING days {len(l):3d} ({len(l)/len(v)*100:.0f}%) avg -${abs(l.mean()):,.0f} worst -${abs(l.min()):,.0f} | trades/trading-day {sum(n.values())/len(v):.1f}", flush=True)
    del df; gc.collect()
