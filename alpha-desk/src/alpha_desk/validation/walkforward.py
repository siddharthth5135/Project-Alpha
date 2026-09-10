"""Purged walk-forward validation (P1.3).

Random / shuffled K-fold is forbidden on any promote path. Embargo and purge
keep label horizons from leaking across the train/test cut.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


class PromotePathError(RuntimeError):
    """Raised when a disallowed validation scheme is used for promotion."""


FORBIDDEN_PROMOTE_SCHEMES = frozenset(
    {
        "random_kfold",
        "kfold",
        "shuffled_kfold",
        "random",
        "iid",
        "sklearn_kfold",
    }
)


def assert_promote_scheme_allowed(scheme: str, *, for_promote: bool) -> None:
    normalized = scheme.strip().lower().replace("-", "_").replace(" ", "_")
    if for_promote and normalized in FORBIDDEN_PROMOTE_SCHEMES:
        raise PromotePathError(
            f"scheme {scheme!r} is forbidden on a promote path. "
            "Use walk_forward_purged (playbook LAW 6 / Gate G3)."
        )
    if for_promote and "walk_forward" not in normalized and "purged" not in normalized:
        raise PromotePathError(
            f"scheme {scheme!r} is not an approved promote path; "
            "expected walk_forward_purged"
        )


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_idx: pd.Index
    test_idx: pd.Index
    train_end: int
    test_start: int
    test_end: int
    embargo_rows: int
    purged_rows: int


def purged_walk_forward(
    frame: pd.DataFrame,
    *,
    n_folds: int = 4,
    embargo_bars: int = 150,
    horizon_bars: int = 8,
    min_train_rows: int = 400,
    min_test_rows: int = 80,
) -> list[WalkForwardFold]:
    """Expanding-window purged walk-forward.

    For test fold starting at index T:
      - purge: drop train rows whose label window reaches into the test set
        (row i uses bars i+1..i+H, so require i + H < T)
      - embargo: train_end = T - embargo_bars (also drops the gap before test)
    """
    if n_folds < 2:
        raise ValueError("n_folds must be at least 2")
    ordered = frame.sort_values("ts").reset_index(drop=True)
    n = len(ordered)
    # Leave room for initial train + embargo + at least one test block.
    usable = n - embargo_bars - min_train_rows
    if usable < n_folds * min_test_rows:
        raise ValueError(
            f"not enough rows for {n_folds} folds with embargo={embargo_bars}: "
            f"have {n}, need roughly {min_train_rows + embargo_bars + n_folds * min_test_rows}"
        )

    test_block = usable // n_folds
    folds: list[WalkForwardFold] = []
    for fold_id in range(n_folds):
        test_start = min_train_rows + embargo_bars + fold_id * test_block
        test_end = n if fold_id == n_folds - 1 else test_start + test_block
        if test_end - test_start < min_test_rows and fold_id < n_folds - 1:
            continue

        train_end = test_start - embargo_bars
        # Purge: labels that touch the test window cannot stay in train.
        max_train_for_purge = test_start - horizon_bars
        effective_train_end = min(train_end, max_train_for_purge)
        if effective_train_end < min_train_rows // 2:
            raise ValueError(f"fold {fold_id}: train window collapsed after purge")

        train_idx = ordered.index[:effective_train_end]
        test_idx = ordered.index[test_start:test_end]
        purged = max(0, train_end - effective_train_end)
        folds.append(
            WalkForwardFold(
                fold_id=fold_id,
                train_idx=train_idx,
                test_idx=test_idx,
                train_end=effective_train_end,
                test_start=test_start,
                test_end=test_end,
                embargo_rows=embargo_bars,
                purged_rows=purged,
            )
        )
    if len(folds) < 2:
        raise ValueError("walk-forward produced fewer than 2 usable folds")
    return folds
