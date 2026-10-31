"""Scoring epoch: which scoring/sizing regime a stored score belongs to.

Scores from different epochs are NOT comparable, and comparing them was one root cause of
a champion that never upgraded (a champion crowned under an older, larger score scale
could never be beaten by scores produced afterwards).

1 = old sqrt(252)-annualised Sharpe
2 = edge_t_stat, mean-minus-std across walk-forward folds (run 43 onward), 1 NQ/ES mini per trade
3 = same score on risk-sized (micro-contract) P&L - see app/backtest/sizing.py
"""
SCORING_EPOCH = 3
