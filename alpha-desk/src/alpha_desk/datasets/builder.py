"""Assemble the supervised table.

One row per decision bar:

    ts | symbol | source | <features...> | <labels...> | feature_set_hash | label_def_id

Rows are dropped at both ends: the head loses the feature warmup, the tail loses
the label horizon. Nothing is forward-filled or interpolated across those edges,
because a fabricated row at the boundary is a fabricated training example.

Every row carries the feature set hash and label definition id, so a model
artifact can be matched to the exact data recipe that produced it, and a
mismatch at inference time fails closed instead of silently mis-scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from alpha_desk.features.registry import FeatureSet
from alpha_desk.labels.definitions import LABEL_COLUMNS, LabelDefinition, build_labels


@dataclass
class DatasetBuildReport:
    symbol: str
    source: str
    input_bars: int
    rows: int
    dropped_warmup: int
    dropped_horizon: int
    dropped_incomplete: int
    feature_set_hash: str
    label_def_id: str
    first_ts: str | None = None
    last_ts: str | None = None
    label_balance: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "input_bars": self.input_bars,
            "rows": self.rows,
            "dropped_warmup": self.dropped_warmup,
            "dropped_horizon": self.dropped_horizon,
            "dropped_incomplete": self.dropped_incomplete,
            "feature_set_hash": self.feature_set_hash,
            "label_def_id": self.label_def_id,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "label_balance": self.label_balance,
        }


def build_supervised_table(
    bars: pd.DataFrame,
    *,
    symbol: str,
    source: str,
    feature_set: FeatureSet,
    label_definition: LabelDefinition,
) -> tuple[pd.DataFrame, DatasetBuildReport]:
    input_bars = len(bars)
    frame = bars.sort_values("ts").reset_index(drop=True)

    features = feature_set.build(frame, drop_warmup=True)
    labels = build_labels(frame, label_definition)

    merged = features.merge(labels, on="ts", how="inner")
    after_warmup = len(merged)

    # Tail rows have no resolved outcome; they are removed rather than imputed.
    merged = merged.dropna(subset=["y_fwd_ret_H", "y_outcome_long", "y_outcome_short"])
    after_horizon = len(merged)

    before_feature_drop = after_horizon
    merged = merged.dropna(subset=list(feature_set.feature_names))
    rows = len(merged)

    merged = merged.reset_index(drop=True)
    merged.insert(1, "symbol", symbol)
    merged.insert(2, "source", source)
    merged["feature_set_hash"] = feature_set.hash
    merged["label_def_id"] = label_definition.label_def_id

    balance: dict[str, float] = {}
    if rows:
        for column in ("y_hit_stop_long", "y_hit_stop_short"):
            balance[column] = round(float(merged[column].mean()), 4)
        for column in ("y_outcome_long", "y_outcome_short"):
            balance[f"{column}_unresolved"] = round(float((merged[column] == 0).mean()), 4)

    report = DatasetBuildReport(
        symbol=symbol,
        source=source,
        input_bars=input_bars,
        rows=rows,
        dropped_warmup=input_bars - after_warmup,
        dropped_horizon=after_warmup - after_horizon,
        dropped_incomplete=before_feature_drop - rows,
        feature_set_hash=feature_set.hash,
        label_def_id=label_definition.label_def_id,
        first_ts=str(merged["ts"].iloc[0]) if rows else None,
        last_ts=str(merged["ts"].iloc[-1]) if rows else None,
        label_balance=balance,
    )
    return merged, report


def dataset_columns(feature_set: FeatureSet) -> list[str]:
    return [
        "ts",
        "symbol",
        "source",
        *feature_set.feature_names,
        *LABEL_COLUMNS,
        "feature_set_hash",
        "label_def_id",
    ]
