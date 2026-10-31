"""Walk-forward folds over POOLED, cross-asset resolved sessions — the session-count
counterpart of app/optimizer/search.py's _split_k_fold_walk_forward, which splits by
calendar day (meaningless here: a "day" of buzzer sessions has hundreds of 5-minute
sessions, not a handful of futures bars). Folds are ordered oldest-test-block first,
each with strictly more training history than the last, exactly like the futures
version — just indexed by session count instead of unique dates.
"""
from __future__ import annotations

from typing import Any


def build_session_folds(
    sessions: list[dict[str, Any]], k_folds: int = 3, test_frac: float = 0.15
) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """sessions must already be sorted ascending by end_ts (store.list_resolved_sessions
    does this). Degrades to fewer folds (never zero, unless there's too little data
    for even one) exactly like the futures version — the trainer must handle an
    empty return honestly ("not enough sessions yet"), never fabricate a fold."""
    n = len(sessions)
    block = max(1, int(n * test_frac))
    folds: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for i in range(k_folds):
        test_end = n - i * block
        test_start = test_end - block
        if test_start <= 0:
            break
        folds.append((sessions[:test_start], sessions[test_start:test_end]))
    folds.reverse()
    return folds
