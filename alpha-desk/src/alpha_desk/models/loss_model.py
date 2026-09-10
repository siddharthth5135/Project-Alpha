"""Loss model: P(hit stop) before target (long side primary for v0)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV

from alpha_desk.core.types import DraftOrder
from alpha_desk.models.artifacts import ArtifactMeta, ArtifactStore
from alpha_desk.models.columns import feature_columns
from alpha_desk.models.metrics import classification_metrics
from alpha_desk.models.split import time_holdout


@dataclass
class TrainResult:
    model_id: str
    folder: Any
    meta: ArtifactMeta
    metrics_test: dict[str, float]


def train_loss_model(
    table: pd.DataFrame,
    *,
    artifact_root: Any,
    embargo_bars: int,
    source: str,
    target_column: str = "y_hit_stop_long",
    model_id: str | None = None,
) -> TrainResult:
    import lightgbm as lgb

    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.frozen import FrozenEstimator

    features = feature_columns(table)
    split = time_holdout(table, embargo_bars=embargo_bars)
    train = table.loc[split.train_idx].dropna(subset=[target_column])
    test = table.loc[split.test_idx].dropna(subset=[target_column])

    y_train = train[target_column].to_numpy(int)
    y_test = test[target_column].to_numpy(int)
    if len(np.unique(y_train)) < 2:
        raise ValueError(f"{target_column} has a single class in the train window")

    base = lgb.LGBMClassifier(
        n_estimators=150,
        learning_rate=0.05,
        num_leaves=23,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=0,
        verbosity=-1,
    )
    cal_cut = int(len(train) * 0.8)
    x_fit = train.iloc[:cal_cut][features]
    y_fit = y_train[:cal_cut]
    x_cal = train.iloc[cal_cut:][features]
    y_cal = y_train[cal_cut:]
    base.fit(x_fit, y_fit)
    calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
    calibrated.fit(x_cal, y_cal)

    model = calibrated
    x_test = test[features]
    proba = model.predict_proba(x_test)[:, 1]
    metrics = classification_metrics(y_test, proba)

    mid = model_id or f"loss_hit_stop_{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M%S')}"
    meta = ArtifactMeta(
        model_id=mid,
        model_kind="loss_hit_stop",
        version="v0",
        feature_names=features,
        feature_set_hash=str(table["feature_set_hash"].iloc[0]),
        label_def_id=str(table["label_def_id"].iloc[0]),
        target_column=target_column,
        source=source,
        symbols=sorted(table["symbol"].unique().tolist()),
        metrics={"test": metrics, "embargo_bars": embargo_bars},
        train_rows=len(train),
        test_rows=len(test),
        notes="calibrated P(hit stop) for the loss gate; long-side v0",
    )
    folder = ArtifactStore(artifact_root).save(model, meta)
    return TrainResult(model_id=mid, folder=folder, meta=meta, metrics_test=metrics)


class ArtifactLossModel:
    """LossModel protocol implementation backed by a saved artifact."""

    def __init__(self, model: Any, meta: ArtifactMeta) -> None:
        self._model = model
        self._meta = meta
        self.model_id = meta.model_id

    def p_hit_stop(self, draft: DraftOrder, context: dict[str, float]) -> float:
        # Context must supply the same feature names; missing → refuse with 1.0
        # (skip) rather than inventing zeros.
        missing = [n for n in self._meta.feature_names if n not in context]
        if missing:
            return 1.0
        import pandas as pd

        row = pd.DataFrame([{n: context[n] for n in self._meta.feature_names}])
        proba = self._model.predict_proba(row)[0, 1]
        # v0 trained on long-stop labels; shorts use the same score until a
        # dedicated short artifact exists (conservative for paper scaffolding).
        _ = draft.intent.side  # reserved for side-specific models
        return float(np.clip(proba, 0.0, 1.0))

    def expected_shortfall_inr(self, draft: DraftOrder, context: dict[str, float]) -> float:
        # Without a severity model, ES is the full stop risk (pessimistic).
        return float(draft.risk_inr)
