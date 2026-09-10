"""Shared column helpers for trainers."""

from __future__ import annotations

import pandas as pd

# Points-based ATR leaks instrument scale when pooling; models use atr_14_pct.
EXCLUDE_FROM_MODEL = frozenset({"atr_14"})

META_COLUMNS = frozenset(
    {
        "ts",
        "symbol",
        "source",
        "feature_set_hash",
        "label_def_id",
        "y_fwd_ret_H",
        "y_hit_stop_long",
        "y_hit_stop_short",
        "y_outcome_long",
        "y_outcome_short",
    }
)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    cols = [
        c
        for c in frame.columns
        if c not in META_COLUMNS and c not in EXCLUDE_FROM_MODEL and not c.startswith("y_")
    ]
    return sorted(cols)


def matrix(frame: pd.DataFrame, columns: list[str]):
    import numpy as np

    return np.asarray(frame[columns], dtype=float)
