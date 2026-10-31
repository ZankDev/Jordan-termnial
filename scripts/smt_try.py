import sys, time
sys.path.insert(0, "scripts")
from verify_flip import *
sym = sys.argv[1] if len(sys.argv) > 1 else "ES"
df = rp._load(sym)[0]
s = registry.get("smt_divergence"); p = s.default_params()
t = time.time(); tr = s.generate_trades(df, p); print(sym, "trades", len(tr), "in", round(time.time() - t), "s")
res = run_backtest(df, s, p, sym, 50000.0, sizing=default_config(50000.0)).trades
rows = [(str(x["entry_time"]), str(x["exit_time"]), float(x["pnl"])) for x in res]
print(stats(rows))
