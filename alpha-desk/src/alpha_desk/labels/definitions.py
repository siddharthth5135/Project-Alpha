"""Label definitions for the A1 index strategy.

Two targets, matching the two models the architecture calls for:

* `y_fwd_ret_H` — net forward return over H bars, for the alpha model.
* `y_hit_stop_long` / `y_hit_stop_short` — did the stop get hit before the
  target inside H bars, for the loss model.

The stop/target barriers use the same ATR multiple and R:R that the live
strategy will use, so the loss model is trained on the question the loss gate
actually asks. If the risk policy changes, the `label_def_id` changes with it
and old models stop matching.

Barrier resolution within a bar is pessimistic: when a single bar's range spans
both the stop and the target, the stop is recorded as hit. Without tick data we
cannot know the order, and assuming the good outcome is how backtests lie.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LABEL_COLUMNS = [
    "y_fwd_ret_H",
    "y_hit_stop_long",
    "y_hit_stop_short",
    "y_outcome_long",
    "y_outcome_short",
]


@dataclass(frozen=True)
class LabelDefinition:
    horizon_bars: int
    stop_atr_multiple: float
    target_rr: float
    cost_drag_per_side_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.horizon_bars < 1:
            raise ValueError("horizon must be at least one bar")
        if self.stop_atr_multiple <= 0:
            raise ValueError("stop_atr_multiple must be positive")
        if self.target_rr <= 0:
            raise ValueError("target_rr must be positive")

    @property
    def label_def_id(self) -> str:
        return (
            f"L_H{self.horizon_bars}"
            f"_stop{self.stop_atr_multiple:g}atr"
            f"_rr{self.target_rr:g}"
            f"_cost{self.cost_drag_per_side_bps:g}bps"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "label_def_id": self.label_def_id,
            "horizon_bars": self.horizon_bars,
            "stop_atr_multiple": self.stop_atr_multiple,
            "target_rr": self.target_rr,
            "cost_drag_per_side_bps": self.cost_drag_per_side_bps,
            "barrier_tie_break": "stop_wins",
        }


def default_label_definition(
    horizon_bars: int = 8,
    stop_atr_multiple: float = 1.5,
    target_rr: float = 2.0,
    cost_drag_per_side_bps: float = 2.0,
) -> LabelDefinition:
    return LabelDefinition(
        horizon_bars=horizon_bars,
        stop_atr_multiple=stop_atr_multiple,
        target_rr=target_rr,
        cost_drag_per_side_bps=cost_drag_per_side_bps,
    )


DEFAULT_STOP_ATR_MULTIPLE = 1.5
DEFAULT_COST_DRAG_BPS = 2.0


def label_definition_from_config(risk_config, strategy_spec) -> LabelDefinition:
    """Derive the label definition from config, so the barriers the loss model is
    trained on are the barriers the desk actually enforces.

    Both the dataset builder and the G1 verifier call this. Deriving it twice by
    hand is how a model quietly ends up trained on a different question from the
    one the loss gate asks.
    """
    return LabelDefinition(
        horizon_bars=strategy_spec.horizon.bars,
        stop_atr_multiple=DEFAULT_STOP_ATR_MULTIPLE,
        target_rr=risk_config.quality_gate.min_rr_after_costs,
        cost_drag_per_side_bps=DEFAULT_COST_DRAG_BPS,
    )


def build_labels(
    bars: pd.DataFrame,
    definition: LabelDefinition,
    *,
    atr: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute labels aligned to the decision bar.

    Row t answers "if I acted at the close of bar t, what happened over the next
    H bars". The final H rows have no answer and are NaN.
    """
    frame = bars.sort_values("ts").reset_index(drop=True)
    if atr is None:
        from alpha_desk.features.builders import atr_14

        atr = atr_14(frame)
    atr = pd.Series(np.asarray(atr, dtype=float), index=frame.index)

    h = definition.horizon_bars
    close = frame["close"].astype(float)

    drag = definition.cost_drag_per_side_bps / 10_000.0
    gross_fwd = close.shift(-h) / close - 1.0
    out = pd.DataFrame({"ts": frame["ts"], "y_fwd_ret_H": gross_fwd - 2 * drag})

    stop_distance = atr * definition.stop_atr_multiple
    target_distance = stop_distance * definition.target_rr

    highs, lows = frame["high"].to_numpy(float), frame["low"].to_numpy(float)
    entry = close.to_numpy(float)
    stop_pts, target_pts = stop_distance.to_numpy(float), target_distance.to_numpy(float)
    n = len(frame)

    long_outcome = np.full(n, np.nan)
    short_outcome = np.full(n, np.nan)

    for i in range(n):
        if i + h >= n or not np.isfinite(stop_pts[i]) or stop_pts[i] <= 0:
            continue
        window = slice(i + 1, i + 1 + h)
        future_high, future_low = highs[window], lows[window]

        long_outcome[i] = _resolve(
            future_high, future_low, entry[i] + target_pts[i], entry[i] - stop_pts[i], long=True
        )
        short_outcome[i] = _resolve(
            future_high, future_low, entry[i] - target_pts[i], entry[i] + stop_pts[i], long=False
        )

    out["y_outcome_long"] = long_outcome
    out["y_outcome_short"] = short_outcome
    # NaN, not 0, where the trade never resolved: "stop not hit" and "we do not
    # know" are different training signals.
    out["y_hit_stop_long"] = np.where(np.isnan(long_outcome), np.nan, long_outcome == -1).astype(
        float
    )
    out["y_hit_stop_short"] = np.where(
        np.isnan(short_outcome), np.nan, short_outcome == -1
    ).astype(float)
    out.loc[out["y_outcome_long"].isna(), "y_hit_stop_long"] = np.nan
    out.loc[out["y_outcome_short"].isna(), "y_hit_stop_short"] = np.nan
    return out


def _resolve(
    future_high: np.ndarray,
    future_low: np.ndarray,
    target: float,
    stop: float,
    *,
    long: bool,
) -> float:
    """+1 target first, -1 stop first, 0 neither. Ties go to the stop."""
    if long:
        target_hits = np.flatnonzero(future_high >= target)
        stop_hits = np.flatnonzero(future_low <= stop)
    else:
        target_hits = np.flatnonzero(future_low <= target)
        stop_hits = np.flatnonzero(future_high >= stop)

    first_target = target_hits[0] if target_hits.size else None
    first_stop = stop_hits[0] if stop_hits.size else None

    if first_stop is None and first_target is None:
        return 0.0
    if first_stop is None:
        return 1.0
    if first_target is None:
        return -1.0
    return -1.0 if first_stop <= first_target else 1.0
