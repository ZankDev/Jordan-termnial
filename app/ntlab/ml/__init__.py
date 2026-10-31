"""NT LAB machine learning — methods from Stefan Jansen, "Machine Learning for Algorithmic Trading" (2nd ed.), built for
this lab (own implementation, not the book's code):

  features.py   alpha factors per 5-min bar: momentum / reversal over several horizons, volatility, ATR, RSI, distance to
                moving averages and session VWAP, opening-range width, overnight gap, trend slope, volume surprise,
                time of day / day of week (book ch. 8-9). Only information known at the bar's close.
  meta.py       META-LABELING (triple-barrier labels = the strategy's own stop / target / time exits, ch. 7): LightGBM learns
                which of a strategy's signals to TAKE or SKIP; trained walk-forward with purging + embargo (ch. 6-7),
                judged only on trades it never trained on, plus the never-searched holdout.
  dsr.py        DEFLATED SHARPE RATIO (ch. 7 / 16): a Sharpe ratio corrected for how many strategies were tried —
                the probability the edge is real and not the best of N lucky draws.
  surrogate.py  ML-GUIDED SEARCH: an ensemble of LightGBM regressors learns spec -> score from every backtest done so far
                and proposes the next candidates where it expects high scores (mean + uncertainty bonus).
"""
