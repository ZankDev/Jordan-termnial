import sys, json, numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.strategies.composed import CombinedStrategy
cands = json.loads((rp.OUT / "cands.json").read_text(encoding="utf-8"))["kept"]
fvg = registry.get("fair_value_gap"); nyob = registry.get("ny_open_breakout")
print("fvg params:", [(p.name, p.default, p.min, p.max) for p in fvg.params])
for sym in ("ES", "NQ"):
    df = rp._load(sym)[0]
    best = [r for r in cands if r["symbol"] == sym and r["name"] == "ny_open_breakout"]
    pn = best[0]["params"] if best else {p.name: p.default for p in nyob.params}
    pf = {p.name: p.default for p in fvg.params}
    for label, strat, params in (("nyob", nyob, pn), ("fvg(default)", fvg, pf), ("combo", CombinedStrategy("c", [nyob, fvg]), {**{"a_" + k: v for k, v in pn.items()}, **{"b_" + k: v for k, v in pf.items()}})):
        res = run_backtest(df, strat, params, sym, 50000.0, sizing=default_config(50000.0)).trades
        rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"])) for t in res]
        yrs = {}
        for r in rows: yrs[r[1][:4]] = yrs.get(r[1][:4], 0) + r[2]
        days = len({r[0][:10] for r in rows})
        print(sym, label, stats(rows), "trade-days", days, "years", {k: round(v) for k, v in yrs.items()}, flush=True)
