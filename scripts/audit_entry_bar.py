import sys, json, importlib.util
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
def load_old(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
old_nyob = load_old("old_nyob", "C:/Users/yarde/.claude/jobs/66b87be5/tmp/old_nyob.py").NyOpenBreakout()
old_orb = load_old("old_orb", "C:/Users/yarde/.claude/jobs/66b87be5/tmp/old_orb.py").OrbRetest()
new_nyob, new_orb = registry.get("ny_open_breakout"), registry.get("orb_retest")
for sym in ("ES", "NQ"):
    df = rp._load(sym)[0]
    for label, old, new, p in (("ny_open_breakout", old_nyob, new_nyob, new_nyob.default_params()), ("orb_retest", old_orb, new_orb, new_orb.default_params())):
        out = []
        for tag, s in (("OLD", old), ("FIXED", new)):
            res = run_backtest(df, s, p, sym, 50000.0, sizing=default_config(50000.0, risk_dollars=300.0)).trades
            pn = np.array([t["pnl"] for t in res]); w, l = pn[pn > 0].sum(), -pn[pn < 0].sum()
            out.append(f"{tag}: n={len(pn)} PF {w/l:.2f} net ${pn.sum():,.0f} stops {sum(t['exit_reason']=='stop' for t in res)}")
        print(sym, label, " | ".join(out), flush=True)
