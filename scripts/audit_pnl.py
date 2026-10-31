import sys
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
sym = "ES"; df = rp._load(sym)[0]
s = registry.get("ny_open_breakout")
res = run_backtest(df, s, s.default_params(), sym, 50000.0, sizing=default_config(50000.0, risk_dollars=300.0)).trades
bad = 0
for t in res:
    d = 1 if t["side"] == "long" else -1
    pv = 5.0 if t["contract"] == "micro" else 50.0
    calc = (t["exit_price"] - t["entry_price"]) * d * t["qty"] * pv - t["commission"]
    if abs(calc - t["pnl"]) > 0.02: bad += 1
print("trades", len(res), "pnl mismatches vs independent recompute:", bad)
st = np.array([t["pnl"] for t in res if t["exit_reason"] == "stop"]); tg = np.array([t["pnl"] for t in res if t["exit_reason"] == "target"])
print("stop-outs: median pnl %.0f (target ~ -300 - commission), p10 %.0f, p90 %.0f" % (np.median(st), np.percentile(st, 10), np.percentile(st, 90)))
print("targets: median pnl %.0f" % np.median(tg))
print("risk_dollars median", np.median([t["risk_dollars"] for t in res]))
