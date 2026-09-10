"""LightGBM alpha model for forward return."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from alpha_desk.models.artifacts import ArtifactMeta, ArtifactStore
from alpha_desk.models.columns import feature_columns
from alpha_desk.models.metrics import regression_metrics
from alpha_desk.models.split import time_holdout


@dataclass
class TrainResult:
    model_id: str
    folder: Any
    meta: ArtifactMeta
    metrics_test: dict[str, float]
    feature_importance: dict[str, float]


def train_lgbm_alpha(
    table: pd.DataFrame,
    *,
    artifact_root: Any,
    embargo_bars: int,
    source: str,
    model_id: str | None = None,
) -> TrainResult:
    import lightgbm as lgb

    features = feature_columns(table)
    split = time_holdout(table, embargo_bars=embargo_bars)
    train = table.loc[split.train_idx]
    test = table.loc[split.test_idx]

    model = lgb.LGBMRegressor(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=0,
        verbosity=-1,
    )
    x_train = train[features]
    y_train = train["y_fwd_ret_H"].to_numpy(float)
    x_test = test[features]
    y_test = test["y_fwd_ret_H"].to_numpy(float)
    model.fit(x_train, y_train)
    pred = model.predict(x_test)
    metrics = regression_metrics(y_test, pred)
    importance = {
        name: float(val)
        for name, val in sorted(
            zip(features, model.feature_importances_), key=lambda kv: -kv[1]
        )
    }

    mid = model_id or f"lgbm_alpha_{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M%S')}"
    meta = ArtifactMeta(
        model_id=mid,
        model_kind="lgbm_alpha",
        version="v0",
        feature_names=features,
        feature_set_hash=str(table["feature_set_hash"].iloc[0]),
        label_def_id=str(table["label_def_id"].iloc[0]),
        target_column="y_fwd_ret_H",
        source=source,
        symbols=sorted(table["symbol"].unique().tolist()),
        metrics={
            "test": metrics,
            "embargo_bars": embargo_bars,
            "feature_importance": importance,
        },
        train_rows=len(train),
        test_rows=len(test),
    )
    folder = ArtifactStore(artifact_root).save(model, meta)
    return TrainResult(
        model_id=mid,
        folder=folder,
        meta=meta,
        metrics_test=metrics,
        feature_importance=importance,
    )


def predict_lgbm(model: Any, frame: pd.DataFrame, feature_names: list[str]) -> np.ndarray:
    return np.asarray(model.predict(frame[feature_names]), dtype=float)
