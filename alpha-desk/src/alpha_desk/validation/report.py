"""Cost-aware research report from OOS predictions.

This is not a full execution simulator. It answers: if we followed the model's
sign (or score threshold) on each decision bar, what did the *already
cost-dragged* forward label do? Promote decisions still require verified costs
and non-plumbing data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class FoldReport:
    fold_id: int
    train_rows: int
    test_rows: int
    purged_rows: int
    ic: float
    hit_rate: float
    mean_trade_ret: float
    total_ret: float
    sharpe: float
    max_drawdown: float
    n_trades: int
    turnover: float


@dataclass
class ValidationReport:
    source: str
    scheme: str
    model_kind: str
    promote_eligible: bool
    folds: list[FoldReport] = field(default_factory=list)
    aggregate: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "scheme": self.scheme,
            "model_kind": self.model_kind,
            "promote_eligible": self.promote_eligible,
            "folds": [asdict(f) for f in self.folds],
            "aggregate": self.aggregate,
            "notes": self.notes,
        }


def _sharpe(returns: np.ndarray, bars_per_year: float = 252 * 25) -> float:
    """15m bars ≈ 25/session * 252 sessions; rough annualisation for ranking only."""
    if len(returns) < 2:
        return 0.0
    mu, sigma = float(np.mean(returns)), float(np.std(returns, ddof=1))
    if sigma <= 1e-12:
        return 0.0
    return (mu / sigma) * float(np.sqrt(bars_per_year))


def _max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    equity = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    return float(dd.min())


def score_fold(
    *,
    fold_id: int,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    train_rows: int,
    test_rows: int,
    purged_rows: int,
    score_threshold: float = 0.0,
) -> FoldReport:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    if len(y_true) != len(y_pred):
        raise ValueError("y_true/y_pred length mismatch")

    ic = float(np.corrcoef(y_true, y_pred)[0, 1]) if len(y_true) > 2 else 0.0
    # Long when pred > threshold, short when pred < -threshold, else flat.
    position = np.where(y_pred > score_threshold, 1.0, np.where(y_pred < -score_threshold, -1.0, 0.0))
    trade_rets = position * y_true
    active = position != 0
    n_trades = int(active.sum())
    hit_rate = float(((trade_rets > 0) & active).sum() / n_trades) if n_trades else 0.0
    mean_trade = float(trade_rets[active].mean()) if n_trades else 0.0
    # Turnover proxy: fraction of bars where position changes.
    turnover = float(np.mean(np.diff(position, prepend=0) != 0)) if len(position) else 0.0

    return FoldReport(
        fold_id=fold_id,
        train_rows=train_rows,
        test_rows=test_rows,
        purged_rows=purged_rows,
        ic=ic,
        hit_rate=hit_rate,
        mean_trade_ret=mean_trade,
        total_ret=float(trade_rets.sum()),
        sharpe=_sharpe(trade_rets),
        max_drawdown=_max_drawdown(trade_rets),
        n_trades=n_trades,
        turnover=turnover,
    )


def aggregate_folds(folds: list[FoldReport]) -> dict[str, float]:
    if not folds:
        return {}
    return {
        "n_folds": float(len(folds)),
        "mean_ic": float(np.mean([f.ic for f in folds])),
        "mean_sharpe": float(np.mean([f.sharpe for f in folds])),
        "mean_hit_rate": float(np.mean([f.hit_rate for f in folds])),
        "mean_max_drawdown": float(np.mean([f.max_drawdown for f in folds])),
        "total_ret_sum": float(np.sum([f.total_ret for f in folds])),
        "total_trades": float(np.sum([f.n_trades for f in folds])),
        "mean_turnover": float(np.mean([f.turnover for f in folds])),
    }


def build_report(
    *,
    source: str,
    model_kind: str,
    folds: list[FoldReport],
    scheme: str = "walk_forward_purged",
) -> ValidationReport:
    from alpha_desk.models.artifacts import PLUMBING_SOURCES

    notes = []
    promote = source not in PLUMBING_SOURCES
    if source in PLUMBING_SOURCES:
        notes.append(f"source={source} is plumbing; report is not promote evidence")
        promote = False
    notes.append("forward returns already include label cost drag; execution slippage not re-simulated")
    return ValidationReport(
        source=source,
        scheme=scheme,
        model_kind=model_kind,
        promote_eligible=promote,
        folds=folds,
        aggregate=aggregate_folds(folds),
        notes=notes,
    )
