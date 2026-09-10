"""Store -> supervised table.

The bridge exists so training never reads parquet directly. It enforces the
guarantees a model card has to be able to claim: one source per dataset, one
schema version, one bar-timestamp convention, no quarantined rows, and the exact
feature-set hash and label definition recorded alongside the rows.

Feature construction is delegated to the same :class:`FeatureSet` the live path
uses. That is deliberate and load-bearing: there is no second implementation
that could drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from alpha_desk.data.store import Dataset, MarketStore
from alpha_desk.datasets.builder import DatasetBuildReport, build_supervised_table
from alpha_desk.features.registry import FeatureSet
from alpha_desk.labels.definitions import LabelDefinition
from alpha_desk.marketdata.contracts import (
    BAR_COLUMNS_EXT,
    SCHEMA_VERSION,
    BarInterval,
    Contract,
)
from alpha_desk.marketdata.errors import DataQualityError


@dataclass
class TrainingBundle:
    frame: pd.DataFrame
    report: DatasetBuildReport
    contract_id: str
    interval: str
    source: str
    bars_in: int
    provenance: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "interval": self.interval,
            "source": self.source,
            "bars_in": self.bars_in,
            "provenance": self.provenance,
            **self.report.as_dict(),
        }


def load_promoted_bars(
    store: MarketStore,
    *,
    source: str,
    contract: Contract,
    interval: BarInterval,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Read promoted bars and verify they are fit to train on."""
    frame = store.read_bars(
        source=source, contract=contract, interval=interval.value, start=start, end=end
    )
    if frame.empty:
        raise DataQualityError(
            f"no promoted bars for {contract.contract_id} {interval.value} from {source}; "
            "run the backfill first, or check the quarantine tree"
        )

    provenance = _verify_provenance(frame, contract=contract, interval=interval, source=source)

    stamps = pd.to_datetime(frame["ts"])
    if stamps.duplicated().any():
        raise DataQualityError(f"{contract.contract_id}: duplicate timestamps in stored bars")
    if not stamps.is_monotonic_increasing:
        raise DataQualityError(f"{contract.contract_id}: stored bars are not time-ordered")

    columns = [c for c in BAR_COLUMNS_EXT if c in frame.columns]
    return frame[columns].reset_index(drop=True), provenance


def build_training_table(
    store: MarketStore,
    *,
    source: str,
    contract: Contract,
    interval: BarInterval,
    feature_set: FeatureSet,
    label_definition: LabelDefinition,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
) -> TrainingBundle:
    bars, provenance = load_promoted_bars(
        store, source=source, contract=contract, interval=interval, start=start, end=end
    )
    frame, report = build_supervised_table(
        bars,
        symbol=contract.contract_id,
        source=source,
        feature_set=feature_set,
        label_definition=label_definition,
    )
    if frame.empty:
        raise DataQualityError(
            f"{contract.contract_id}: no rows survived feature warmup and label horizon "
            f"({len(bars)} bars in); widen the backfill window"
        )
    return TrainingBundle(
        frame=frame,
        report=report,
        contract_id=contract.contract_id,
        interval=interval.value,
        source=source,
        bars_in=len(bars),
        provenance=provenance,
    )


def training_readiness(
    store: MarketStore,
    *,
    source: str,
    minimum_bars: int = 5_000,
) -> pd.DataFrame:
    """Per-partition view of whether there is enough data to start training."""
    coverage = store.coverage(Dataset.BARS)
    if coverage.empty:
        return coverage
    coverage = coverage[coverage["partition"].str.startswith(source)].copy()
    coverage["ready"] = coverage["rows"] >= minimum_bars
    coverage["blocking"] = coverage["verdicts"].apply(
        lambda verdicts: "quarantine" in verdicts
    )
    return coverage.reset_index(drop=True)


def _verify_provenance(
    frame: pd.DataFrame, *, contract: Contract, interval: BarInterval, source: str
) -> dict[str, object]:
    provenance: dict[str, object] = {}

    for column, expected in (("source", source), ("schema_version", SCHEMA_VERSION)):
        if column not in frame.columns:
            continue
        values = set(frame[column].dropna().unique())
        provenance[column] = sorted(str(v) for v in values)
        if len(values) > 1:
            raise DataQualityError(
                f"{contract.contract_id}: stored bars mix {column} values {sorted(values)}; "
                "a dataset must come from one source and one schema"
            )
        if expected is not None and values and str(next(iter(values))) != str(expected):
            raise DataQualityError(
                f"{contract.contract_id}: expected {column}={expected!r}, "
                f"stored data says {next(iter(values))!r}"
            )

    if "bar_convention" in frame.columns:
        conventions = set(frame["bar_convention"].dropna().unique())
        provenance["bar_convention"] = sorted(str(c) for c in conventions)
        if len(conventions) > 1:
            raise DataQualityError(
                f"{contract.contract_id}: stored bars mix timestamp conventions {sorted(conventions)}; "
                "features would be shifted for part of the series"
            )

    if "interval" in frame.columns:
        intervals = set(frame["interval"].dropna().unique())
        if intervals and intervals != {interval.value}:
            raise DataQualityError(
                f"{contract.contract_id}: partition holds intervals {sorted(intervals)}, "
                f"expected {interval.value}"
            )

    provenance["rows"] = len(frame)
    return provenance
