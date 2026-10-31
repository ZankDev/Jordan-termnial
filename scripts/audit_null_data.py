import sys, collections
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
from app.strategies.engine_utils import simulate_trades
from app.strategies import indicators as ind
from app.strategies.base import Strategy
rng = np.random.default_rng(7)
for sym in ("ES", "NQ"):
    df = rp._load(sym)[0]
    # ---- data sanity
    bad_hl = ((df.high < df[["open", "close"]].max(axis=1) - 1e-9) | (df.low > df[["open", "close"]].min(axis=1) + 1e-9)).sum()
    ret = df.close.pct_change().abs()
    per_day = df.groupby(df.index.date).size()
    print(f"{sym}: bars {len(df)}, duplicates {df.index.duplicated().sum()}, bad OHLC {bad_hl}, bars with |ret|>1% {int((ret>0.01).sum())}, >3% {int((ret>0.03).sum())}, median bars/day {per_day.median():.0f}, days with <60 bars {(per_day<60).sum()} of {len(per_day)}")
    # ---- null test: random entries, symmetric random walk expectation: PF ~ 1 before costs for any RR
    atr = ind.atr(df, 14); idx = df.index
    mins = idx.hour * 60 + idx.minute
    ok = np.where((mins >= 9 * 60 + 40) & (mins <= 14 * 60))[0]
    for rr in (1.0, 1.5, 2.0):
        res = []
        days = pd.Series(range(len(df)), index=idx).groupby(idx.date)
        entries = pd.Series(0, index=idx)
        pick = []
        for d, g in days:
            cand = [i for i in g.values if i in set(ok)] if False else g.values[(mins[g.values] >= 9*60+40) & (mins[g.values] <= 14*60)]
            if len(cand): pick.append(rng.choice(cand))
        sides = rng.choice([-1, 1], size=len(pick))
        entries.iloc[pick] = sides
        class R(Strategy):
            def generate_trades(self, d, p): return simulate_trades(d, entries, atr, 1.0, rr, 60)
        from app.strategies.base import Strategy
        r = run_backtest(df, R(name="r", description="", params=[]), {}, sym, 50000.0, sizing=default_config(50000.0, risk_dollars=300.0))
        pn = np.array([t["pnl"] for t in r.trades]); w, l = pn[pn > 0].sum(), -pn[pn < 0].sum()
        ex = collections.Counter(t["exit_reason"] for t in r.trades)
        print(f"   null RR {rr}: n={len(pn)} PF {w/l:.3f} net ${pn.sum():,.0f} exits {dict(ex)} (expect PF a bit under 1.0 from costs)", flush=True)
