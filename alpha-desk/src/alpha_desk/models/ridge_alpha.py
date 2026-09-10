"""Ridge baseline for forward return (alpha)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from alpha_desk.models.artifacts import ArtifactMeta, ArtifactStore
from alpha_desk.models.columns import feature_columns, matrix
from alpha_desk.models.metrics import regression_metrics
from alpha_desk.models.split import time_holdout


@dataclass
class TrainResult:
    model_id: str
    folder: Any
    meta: ArtifactMeta
    metrics_test: dict[str, float]


def train_ridge_alpha(
    table: pd.DataFrame,
    *,
    artifact_root: Any,
    embargo_bars: int,
    source: str,
    model_id: str | None = None,
) -> TrainResult:
    features = feature_columns(table)
    if not features:
        raise ValueError("no feature columns found")
    split = time_holdout(table, embargo_bars=embargo_bars)
    train = table.loc[split.train_idx]
    test = table.loc[split.test_idx]

    pipe = Pipeline(
        [
            ("scale", StandardScaler()),
            ("ridge", Ridge(alpha=1.0)),
        ]
    )
    x_train, y_train = matrix(train, features), train["y_fwd_ret_H"].to_numpy(float)
    x_test, y_test = matrix(test, features), test["y_fwd_ret_H"].to_numpy(float)
    pipe.fit(x_train, y_train)
    pred = pipe.predict(x_test)
    metrics = regression_metrics(y_test, pred)

    mid = model_id or f"ridge_alpha_{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M%S')}"
    meta = ArtifactMeta(
        model_id=mid,
        model_kind="ridge_alpha",
        version="v0",
        feature_names=features,
        feature_set_hash=str(table["feature_set_hash"].iloc[0]),
        label_def_id=str(table["label_def_id"].iloc[0]),
        target_column="y_fwd_ret_H",
        source=source,
        symbols=sorted(table["symbol"].unique().tolist()),
        metrics={"test": metrics, "embargo_bars": embargo_bars},
        train_rows=len(train),
        test_rows=len(test),
        notes="baseline; weak metrics are acceptable at P1.2.1",
    )
    folder = ArtifactStore(artifact_root).save(pipe, meta)
    return TrainResult(model_id=mid, folder=folder, meta=meta, metrics_test=metrics)


def predict_ridge(model: Pipeline, frame: pd.DataFrame, feature_names: list[str]) -> np.ndarray:
    return model.predict(matrix(frame, feature_names))
