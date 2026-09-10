"""P1.3 validation harness tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_desk.data.providers.synthetic import SyntheticProvider
from alpha_desk.datasets.builder import build_supervised_table
from alpha_desk.features.catalogue import build_feature_set_v0
from alpha_desk.labels.definitions import default_label_definition
from alpha_desk.validation.report import build_report, score_fold
from alpha_desk.validation.walkforward import (
    PromotePathError,
    assert_promote_scheme_allowed,
    purged_walk_forward,
)


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    bars = SyntheticProvider(seed=7).fetch("NIFTY", "15m", "2025-01-01", "2026-07-01")
    fs = build_feature_set_v0()
    out, _ = build_supervised_table(
        bars,
        symbol="NIFTY",
        source="synthetic",
        feature_set=fs,
        label_definition=default_label_definition(),
    )
    return out


def test_random_kfold_promote_fails_closed():
    with pytest.raises(PromotePathError, match="forbidden"):
        assert_promote_scheme_allowed("random_kfold", for_promote=True)


def test_walk_forward_scheme_allowed_for_promote():
    assert_promote_scheme_allowed("walk_forward_purged", for_promote=True)


def test_research_may_mention_other_schemes_without_promote_flag():
    # Non-promote exploratory use does not raise.
    assert_promote_scheme_allowed("random_kfold", for_promote=False)


def test_purged_folds_do_not_overlap_and_respect_embargo(table):
    folds = purged_walk_forward(table, n_folds=4, embargo_bars=150, horizon_bars=8)
    assert len(folds) >= 2
    for fold in folds:
        assert fold.train_idx.max() < fold.test_idx.min()
        # Embargo + purge: last train index must be before test_start - horizon
        assert fold.train_end <= fold.test_start - 8
        assert fold.embargo_rows == 150


def test_later_fold_has_more_or_equal_train_rows(table):
    folds = purged_walk_forward(table, n_folds=4, embargo_bars=150, horizon_bars=8)
    train_sizes = [len(f.train_idx) for f in folds]
    assert train_sizes[-1] >= train_sizes[0]


def test_score_fold_and_report_marks_plumbing_not_promotable():
    y = np.array([0.01, -0.01, 0.02, -0.02, 0.0])
    pred = np.array([0.02, -0.02, 0.01, -0.01, 0.0])
    fold = score_fold(
        fold_id=0,
        y_true=y,
        y_pred=pred,
        train_rows=100,
        test_rows=5,
        purged_rows=8,
    )
    report = build_report(source="synthetic", model_kind="ridge_alpha", folds=[fold])
    assert report.promote_eligible is False
    assert "plumbing" in report.notes[0]
