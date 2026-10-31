import sys, collections
import pandas as pd
sys.path.insert(0, "scripts")
from verify_flip import *
df = rp._load("ES")[0]
s = registry.get("vwap_reversion_day"); p = s.default_params()
tr = s.generate_trades(df, p)
print("trades", len(tr), collections.Counter(t.exit_reason for t in tr))
ent = collections.Counter(t.entry_time.hour for t in tr); print("entry hours ET", dict(sorted(ent.items())))
ex = collections.Counter((t.exit_time.hour, t.exit_time.minute // 30 * 30) for t in tr if t.exit_reason == "session_close"); print("session_close exit times ET", dict(sorted(ex.items())))
print("any trade crossing 17:00 ET:", sum(1 for t in tr if t.exit_time.normalize() > t.entry_time.normalize() and t.entry_time.time() < pd.Timestamp("17:00").time() or (t.exit_time.time() > pd.Timestamp("16:56").time() and t.exit_time.time() < pd.Timestamp("18:00").time())))
