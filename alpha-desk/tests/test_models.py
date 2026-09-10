"""P1.2 model artifact tests — paper/research path only."""

from __future__ import annotations

import pandas as pd
import pytest

from alpha_desk.data.providers.synthetic import SyntheticProvider
from alpha_desk.datasets.builder import build_supervised_table
from alpha_desk.features.catalogue import build_feature_set_v0
from alpha_desk.labels.definitions import default_label_definition
from alpha_desk.models.artifacts import ArtifactStore, PLUMBING_SOURCES
from alpha_desk.models.lgbm_alpha import train_lgbm_alpha
from alpha_desk.models.loss_model import ArtifactLossModel, train_loss_model
from alpha_desk.models.ridge_alpha import train_ridge_alpha
from alpha_desk.models.split import time_holdout


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    bars = SyntheticProvider(seed=21).fetch("NIFTY", "15m", "2025-01-01", "2026-06-30")
    feature_set = build_feature_set_v0()
    labels = default_label_definition()
    out, _ = build_supervised_table(
        bars,
        symbol="NIFTY",
        source="synthetic",
        feature_set=feature_set,
        label_definition=labels,
    )
    assert len(out) > 500
    return out


def test_holdout_respects_embargo(table):
    split = time_holdout(table, embargo_bars=150)
    assert split.train_idx.max() < split.test_idx.min()
    gap = int(split.test_idx.min() - split.train_idx.max() - 1)
    assert gap >= 150


def test_plumbing_source_cannot_be_promote_eligible(table, tmp_path):
    result = train_ridge_alpha(
        table, artifact_root=tmp_path, embargo_bars=150, source="synthetic"
    )
    assert result.meta.source in PLUMBING_SOURCES
    assert result.meta.promote_eligible is False


def test_ridge_artifact_round_trip(table, tmp_path):
    result = train_ridge_alpha(
        table, artifact_root=tmp_path, embargo_bars=150, source="synthetic"
    )
    model, meta = ArtifactStore(tmp_path).load("ridge_alpha", result.model_id)
    assert meta.feature_set_hash == result.meta.feature_set_hash
    assert meta.target_column == "y_fwd_ret_H"
    pred = model.predict(table[meta.feature_names].to_numpy(float)[:5])
    assert len(pred) == 5


def test_lgbm_writes_feature_importance(table, tmp_path):
    result = train_lgbm_alpha(
        table, artifact_root=tmp_path, embargo_bars=150, source="synthetic"
    )
    assert result.feature_importance
    assert "test" in result.meta.metrics


def test_loss_model_calibrated_and_scores(table, tmp_path, approved_draft):
    result = train_loss_model(
        table, artifact_root=tmp_path, embargo_bars=150, source="synthetic"
    )
    model, meta = ArtifactStore(tmp_path).load("loss_hit_stop", result.model_id)
    loss = ArtifactLossModel(model, meta)
    ctx = {name: float(table.iloc[-10][name]) for name in meta.feature_names}
    p = loss.p_hit_stop(approved_draft, ctx)
    assert 0.0 <= p <= 1.0
    assert loss.expected_shortfall_inr(approved_draft, ctx) == pytest.approx(
        approved_draft.risk_inr
    )


def test_missing_features_fail_closed_to_skip(table, tmp_path, approved_draft):
    result = train_loss_model(
        table, artifact_root=tmp_path, embargo_bars=150, source="synthetic"
    )
    model, meta = ArtifactStore(tmp_path).load("loss_hit_stop", result.model_id)
    loss = ArtifactLossModel(model, meta)
    assert loss.p_hit_stop(approved_draft, {}) == 1.0
