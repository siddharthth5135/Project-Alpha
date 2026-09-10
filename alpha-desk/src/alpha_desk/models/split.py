"""Time-ordered splits with an embargo gap.

Full purged walk-forward is Gate G2/P1.3. P1.2 only needs an honest holdout
that does not leak the label horizon into training.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TimeSplit:
    train_idx: pd.Index
    test_idx: pd.Index
    embargo_rows: int


def time_holdout(
    frame: pd.DataFrame,
    *,
    test_fraction: float = 0.25,
    embargo_bars: int = 150,
) -> TimeSplit:
    if not 0.05 <= test_fraction <= 0.5:
        raise ValueError("test_fraction must be in [0.05, 0.5]")
    n = len(frame)
    if n < embargo_bars + 50:
        raise ValueError(
            f"need more than {embargo_bars + 50} rows for a holdout with embargo, got {n}"
        )
    test_n = max(20, int(n * test_fraction))
    test_start = n - test_n
    train_end = test_start - embargo_bars
    if train_end < 30:
        raise ValueError("training window too small after embargo; need more history")
    ordered = frame.sort_values("ts").reset_index(drop=True)
    train_idx = ordered.index[:train_end]
    test_idx = ordered.index[test_start:]
    return TimeSplit(train_idx=train_idx, test_idx=test_idx, embargo_rows=embargo_bars)
