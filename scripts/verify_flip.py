"""Re-simulate flipped strategies with a proper, pessimistic fill model (stop first when a bar touches both, gap-aware stops) and compare."""
import json, sys, collections
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from app.strategies import registry, indicators as ind
from app.strategies.base import Trade
from app.backtest.engine import run_backtest
from app.backtest.sizing import default_config
import research_portfolio as rp

def honest_flip(df, base, params):
    """Take the base strategy's entries (time, side) and walk the bars with the flipped stop/target."""
    hold = int(params["max_hold_bars"])
    idx = df.index; o, h, l, c = (df[k].values for k in ("open", "high", "low", "close")); n = len(df)
    pos = {t: i for i, t in enumerate(idx)}
    out = []; blocked = -1
    for t in base.generate_trades(df, params):
        ei = pos[t.entry_time]
        if ei <= blocked: continue
        side = "short" if t.side == "long" else "long"
        entry = t.entry_price
        stop, target = t.target_price, t.stop_price   # swap
        mx = min(ei + hold, n - 1); xi = None
        for j in range(ei, mx + 1):
            hs = l[j] <= stop if side == "long" else h[j] >= stop
            ht = h[j] >= target if side == "long" else l[j] <= target
            if hs:
                xp = min(stop, o[j]) if side == "long" else max(stop, o[j]); xi, xr = j, "stop"; break
            if ht:
                xp = target if (o[j] < target if side == "long" else o[j] > target) else o[j]; xi, xr = j, "target"; break
        if xi is None: xi, xp, xr = mx, float(c[mx]), "timeout"
        out.append(Trade(entry_time=idx[ei], exit_time=idx[xi], side=side, entry_price=entry, exit_price=xp, stop_price=stop, target_price=target, exit_reason=xr, mfe_price=None, mae_price=None))
        blocked = xi
    return out

def stats(rows):
    p = np.array([r[2] for r in rows]); 
    if len(p) < 5: return {"n": len(p)}
    w, l = p[p > 0].sum(), -p[p < 0].sum()
    return {"n": len(p), "net": round(float(p.sum())), "pf": round(float(w / l), 2) if l else 99, "wr": round(float((p > 0).mean()) * 100, 1)}

if __name__ == "__main__":
    data = json.loads((rp.OUT / "cands_more.json").read_text(encoding="utf-8"))["kept"]
    top = collections.defaultdict(list)
    for r in sorted(data, key=lambda r: -r["train"]["pf"]):
        if r["name"].endswith("_flip") and len(top[(r["symbol"], r["name"])]) < 4:
            top[(r["symbol"], r["name"])].append(r)
    for (sym, name), lst in top.items():
        df, _ = rp._load(sym)
        cut = df.index.min() + (df.index.max() - df.index.min()) * 0.6
        base = registry.get(name.replace("_flip", ""))
        for r in lst:
            tr = honest_flip(df, base, r["params"])
            class S: pass
            from app.strategies.base import Strategy
            class Fixed(Strategy):
                def generate_trades(self, d, p): return tr
            res = run_backtest(df, Fixed(name="x", description="", params=[]), r["params"], sym, 50000.0, sizing=default_config(50000.0)).trades
            rows = [(str(t["entry_time"]), str(t["exit_time"]), float(t["pnl"])) for t in res]
            a = [x for x in rows if pd.Timestamp(x[0]) < cut]; b = [x for x in rows if pd.Timestamp(x[0]) >= cut]
            print(sym, name, r["params"], "\n   OLD train", r["train"]["pf"], "test", r["test"]["pf"], "| HONEST train", stats(a), "test", stats(b), flush=True)
