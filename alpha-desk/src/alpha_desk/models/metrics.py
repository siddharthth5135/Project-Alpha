"""Training metrics. Accuracy alone is never a promote gate."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    log_loss,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mse = float(mean_squared_error(y_true, y_pred))
    return {
        "rmse": float(np.sqrt(mse)),
        "mae": float(np.mean(np.abs(y_true - y_pred))),
        "r2": float(r2_score(y_true, y_pred)) if len(y_true) > 1 else 0.0,
        "ic": float(np.corrcoef(y_true, y_pred)[0, 1]) if len(y_true) > 2 else 0.0,
        "sign_accuracy": float(np.mean(np.sign(y_true) == np.sign(y_pred))),
    }


def classification_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=int)
    y_prob = np.asarray(y_prob, dtype=float)
    y_hat = (y_prob >= 0.5).astype(int)
    out: dict[str, float] = {
        "accuracy": float(accuracy_score(y_true, y_hat)),
        "brier": float(brier_score_loss(y_true, y_prob)),
        "log_loss": float(log_loss(y_true, np.clip(y_prob, 1e-6, 1 - 1e-6))),
        "base_rate": float(y_true.mean()) if len(y_true) else 0.0,
    }
    if len(np.unique(y_true)) > 1:
        out["auc"] = float(roc_auc_score(y_true, y_prob))
    else:
        out["auc"] = float("nan")
    # Reliability: mean predicted vs mean actual in 5 buckets
    edges = np.linspace(0, 1, 6)
    calib = []
    for i in range(5):
        mask = (y_prob >= edges[i]) & (y_prob < edges[i + 1] if i < 4 else y_prob <= edges[i + 1])
        if mask.sum() == 0:
            continue
        calib.append(
            {
                "bucket": f"{edges[i]:.1f}-{edges[i + 1]:.1f}",
                "n": int(mask.sum()),
                "pred_mean": float(y_prob[mask].mean()),
                "actual_mean": float(y_true[mask].mean()),
            }
        )
    out["calibration_buckets"] = calib  # type: ignore[assignment]
    return out
