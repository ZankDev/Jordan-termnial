"""NT LAB — strategy search with NinjaTrader 8 as the backtest provider.

  spec.py      the candidate format (ninjatrader/SPEC.md), random generation, mutation, crossover
  engine.py    reference engine: the exact NostroLab rules in Python (cross-check, and the local provider)
  data.py      historical bars for the local provider (Dukascopy S&P 500 / Nasdaq-100 5-min as ES / NQ proxies)
  providers.py NinjaTraderProvider (jobs -> Strategy Analyzer -> result files) and LocalProvider (reference engine)
  metrics.py   statistics, in-sample / out-of-sample split, walk-forward folds, Apex 50K simulation, robust score
  store.py     every batch, candidate and result in SQLite (ntlab_* tables)
  search.py    the loop: generate -> backtest -> score -> keep the robust -> mutate / cross / explore -> repeat
  mcp_server.py  MCP (stdio) so an agent can drive all of it as tools
  ml/          machine learning (Jansen, "ML for Algorithmic Trading"): features, meta-labeling, Deflated Sharpe, ML-guided search
"""
try:  # LightGBM must load before pyarrow (parquet): on Windows the other order crashes LightGBM's native code
    import lightgbm  # noqa: F401
except ImportError:  # the lab works without ML
    pass
