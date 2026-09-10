"""Paper decision path: features → model score → rules → OrderPath.

Never touches Angel live orders. Uses PaperBroker via build_desk().
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from alpha_desk.core.types import Instrument
from alpha_desk.features.registry import FeatureSet
from alpha_desk.pipeline.order_path import OrderPath, PathResult, PathStatus
from alpha_desk.risk.state import RiskState
from alpha_desk.strategy.rules import SignalAction, StrategyRules


ScoreFn = Callable[[pd.DataFrame], float]


@dataclass(frozen=True)
class BarDecision:
    ts: datetime
    symbol: str
    alpha_score: float
    signal: str
    reason: str
    path_status: str | None = None
    detail: str = ""
    fill_price: float | None = None


class PaperDecisionEngine:
    """One closed decision bar → optional paper order."""

    def __init__(
        self,
        *,
        feature_set: FeatureSet,
        rules: StrategyRules,
        order_path: OrderPath,
        score_fn: ScoreFn,
        instruments: dict[str, Instrument],
    ) -> None:
        self._features = feature_set
        self._rules = rules
        self._path = order_path
        self._score = score_fn
        self._instruments = instruments

    def on_bar(
        self,
        bars: pd.DataFrame,
        *,
        symbol: str,
        state: RiskState,
        now: datetime | None = None,
    ) -> BarDecision:
        """bars must be sorted and include history through the decision bar."""
        if bars.empty:
            raise ValueError("bars is empty")
        frame = bars.sort_values("ts").reset_index(drop=True)
        ts = pd.Timestamp(frame["ts"].iloc[-1]).to_pydatetime()
        decision_time = now or ts

        feat_frame = self._features.build(frame, drop_warmup=False)
        if len(feat_frame) == 0:
            return BarDecision(ts, symbol, 0.0, SignalAction.FLAT.value, "feature build empty")
        row = feat_frame.iloc[-1]
        if row.isna().any():
            return BarDecision(ts, symbol, 0.0, SignalAction.FLAT.value, "warmup incomplete")

        feature_map = {
            name: float(row[name])
            for name in self._features.feature_names
            if name in row.index and pd.notna(row[name])
        }
        score = float(self._score(feat_frame.tail(1)))
        instrument = self._instruments[symbol]
        ref = float(frame["close"].iloc[-1])
        atr = float(feature_map.get("atr_14", 0.0))

        signal = self._rules.decide(
            ts=ts,
            instrument=instrument,
            ref_price=ref,
            atr_points=atr,
            alpha_score=score,
            features=feature_map,
        )
        if signal.is_flat or signal.intent is None:
            return BarDecision(ts, symbol, score, signal.action.value, signal.reason)

        state.heartbeat(decision_time)
        result: PathResult = self._path.submit(
            signal.intent, state, decision_time, context=feature_map
        )
        return BarDecision(
            ts=ts,
            symbol=symbol,
            alpha_score=score,
            signal=signal.action.value,
            reason=signal.reason,
            path_status=result.status.value,
            detail=result.detail,
            fill_price=result.fill.price if result.fill else None,
        )


def score_from_artifact(model: Any, feature_names: list[str]) -> ScoreFn:
    """Build a score function from a loaded sklearn/lightgbm regressor."""

    def _score(feat_tail: pd.DataFrame) -> float:
        x = feat_tail[feature_names]
        pred = model.predict(x)
        return float(pred[0])

    return _score
