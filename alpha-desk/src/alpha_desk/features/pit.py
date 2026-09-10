"""Point-in-time verification.

Two independent checks, because leakage hides from weak tests:

`assert_point_in_time` — truncation. Build features on bars[0..t] only and
compare the value at t against the full-history build. A feature that peeks
forward disagrees.

`shift_future_attack` — perturbation. Corrupt every bar after t, rebuild, and
require that features at t are unchanged. This catches full-sample statistics
that survive truncation, such as a z-score against the whole series or a
quantile bucket computed on everything.

The same attack applied to labels must produce the opposite result: labels are
*supposed* to move, since their whole job is to describe the future. A label
pipeline that ignores the perturbation is not looking forward at all.
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np
import pandas as pd

from alpha_desk.features.registry import FeatureSet


class PointInTimeViolation(AssertionError):
    pass


def _sample_positions(n: int, warmup: int, samples: int) -> list[int]:
    usable = n - 1
    if usable <= warmup:
        raise ValueError(f"need more than {warmup} warmup bars, got {n}")
    return [int(p) for p in np.linspace(warmup + 1, usable, num=samples, dtype=int)]


def assert_point_in_time(
    feature_set: FeatureSet,
    bars: pd.DataFrame,
    *,
    samples: int = 6,
    tolerance: float = 1e-9,
) -> None:
    """Truncation test: features at t must not depend on bars after t."""
    full = feature_set.build(bars, drop_warmup=False)
    for pos in _sample_positions(len(bars), feature_set.max_lookback, samples):
        truncated = feature_set.build(bars.iloc[: pos + 1], drop_warmup=False)
        expected = full.iloc[pos]
        actual = truncated.iloc[-1]
        for name in feature_set.feature_names:
            a, e = actual[name], expected[name]
            if pd.isna(a) and pd.isna(e):
                continue
            if pd.isna(a) != pd.isna(e) or abs(float(a) - float(e)) > tolerance:
                raise PointInTimeViolation(
                    f"{name} at position {pos} changed when future bars were removed: "
                    f"full={e!r} truncated={a!r}"
                )


def shift_future_attack(
    feature_set: FeatureSet,
    bars: pd.DataFrame,
    *,
    samples: int = 6,
    shock: float = 3.0,
    tolerance: float = 1e-9,
) -> None:
    """Perturbation test: corrupting the future must not move features at t."""
    baseline = feature_set.build(bars, drop_warmup=False)

    for pos in _sample_positions(len(bars), feature_set.max_lookback, samples):
        corrupted = bars.copy()
        future = corrupted.index > pos
        for column in ("open", "high", "low", "close"):
            corrupted.loc[future, column] = corrupted.loc[future, column] * shock
        corrupted.loc[future, "volume"] = corrupted.loc[future, "volume"] * shock

        attacked = feature_set.build(corrupted, drop_warmup=False)
        for name in feature_set.feature_names:
            a, e = attacked.iloc[pos][name], baseline.iloc[pos][name]
            if pd.isna(a) and pd.isna(e):
                continue
            if pd.isna(a) != pd.isna(e) or abs(float(a) - float(e)) > tolerance:
                raise PointInTimeViolation(
                    f"{name} at position {pos} moved when future bars were corrupted: "
                    f"clean={e!r} attacked={a!r}. This feature looks ahead."
                )


def assert_labels_see_the_future(
    label_fn: Callable[[pd.DataFrame], pd.DataFrame],
    bars: pd.DataFrame,
    label_columns: Sequence[str],
    *,
    position: int | None = None,
    shock: float = 3.0,
) -> None:
    """The mirror image: labels must react to a corrupted future.

    A label that survives this test is not describing the future, which means
    the training target is wrong.
    """
    pos = position if position is not None else len(bars) // 2
    baseline = label_fn(bars)

    corrupted = bars.copy()
    future = corrupted.index > pos
    for column in ("open", "high", "low", "close"):
        corrupted.loc[future, column] = corrupted.loc[future, column] * shock
    attacked = label_fn(corrupted)

    unchanged = []
    for name in label_columns:
        a, e = attacked.iloc[pos][name], baseline.iloc[pos][name]
        if pd.isna(a) and pd.isna(e):
            unchanged.append(name)
        elif not pd.isna(a) and not pd.isna(e) and abs(float(a) - float(e)) <= 1e-12:
            unchanged.append(name)
    if len(unchanged) == len(label_columns):
        raise PointInTimeViolation(
            f"labels {list(label_columns)} did not react to a corrupted future at {pos}; "
            "they are not forward-looking"
        )
