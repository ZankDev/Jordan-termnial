import sys, numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.strategies.base import Strategy
CASES = [("NQ", "vwap_reversion", {'dev_mult': 3.25, 'atr_period': 9, 'sl_mult': 3.75, 'tp_mult': 3.5, 'max_hold_bars': 132}),
         ("NQ", "rsi_reversal", {'rsi_period': 25, 'oversold': 20, 'overbought': 70, 'atr_period': 12, 'sl_mult': 3.0, 'tp_mult': 2.25, 'max_hold_bars': 96})]
allr = []
for sym, nm, p in CASES:
    df, _ = rp._load(sym); cut = df.index.min() + (df.index.max() - df.index.min()) * 0.6
    tr = honest_flip(df, registry.get(nm), p)
    class F(Strategy):
        def generate_trades(self, d, q): return tr
    res = run_backtest(df, F(name="x", description="", params=[]), p, sym, 50000.0, sizing=default_config(50000.0)).trades
    rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"]), sym) for t in res if pd.Timestamp(t["entry_time"]) >= cut]
    allr.append(rows)
    by_year = {}
    for r in res: by_year[str(r["exit_time"])[:4]] = by_year.get(str(r["exit_time"])[:4], 0) + r["pnl"]
    print(nm, "years", {k: round(v) for k, v in by_year.items()})
days = [str(d.date()) for d in pd.bdate_range("2023-07-19", "2026-10-05")]
rng = np.random.default_rng(1)
for label, sel in (("vwap_flip", [0]), ("rsi_flip", [1]), ("both", [0, 1])):
    tr = [t for i in sel for t in allr[i]]
    for scale in (0.5, 1.0, 2.0):
        d, n = rp._daily_rules(tr, 400.0, 600.0, scale)
        s = np.array([d.get(x, 0.0) for x in days])
        eq = np.cumsum(s); dd = (np.maximum.accumulate(eq) - eq).max()
        idx = rng.integers(0, len(s), size=(3000, 60)); e = np.cumsum(s[idx], axis=1); pk = np.maximum.accumulate(np.maximum(e, 0))
        print(f"{label:10s} x{scale}: tpd {sum(n.values())/len(days):.2f} mean ${s.mean():6.1f}/day  best ${s.max():.0f} worst ${s.min():.0f}  maxDD ${dd:,.0f}  >=300: {(s>=300).mean()*100:.0f}%  breach60 {((pk-e)>2500).any(axis=1).mean()*100:.0f}%  60d profit median ${np.median(e[:,-1]):,.0f}")
