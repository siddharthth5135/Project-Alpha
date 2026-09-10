"""Label pipeline tests (playbook step P1.1.4).

Exit gate: the future is used for the target column only, asserted automatically.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_desk.data.providers.synthetic import SyntheticProvider
from alpha_desk.datasets.builder import build_supervised_table
from alpha_desk.features.catalogue import build_feature_set_v0
from alpha_desk.features.pit import PointInTimeViolation, assert_labels_see_the_future
from alpha_desk.labels.definitions import (
    LABEL_COLUMNS,
    LabelDefinition,
    build_labels,
    default_label_definition,
)


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return SyntheticProvider(seed=9).fetch("NIFTY", "15m", "2025-10-01", "2026-03-31")


@pytest.fixture
def definition() -> LabelDefinition:
    return default_label_definition()


def _bars_from(prices: list[float], atr_points: float = 10.0) -> pd.DataFrame:
    """Hand-built bars where high/low equal close, so barriers are unambiguous."""
    stamps = pd.date_range("2026-01-01 09:15", periods=len(prices), freq="15min", tz="Asia/Kolkata")
    return pd.DataFrame(
        {
            "ts": stamps,
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1_000] * len(prices),
        }
    )


# --------------------------------------------------------------------------- #
# forward looking, on purpose
# --------------------------------------------------------------------------- #


def test_labels_react_to_a_corrupted_future(bars, definition):
    assert_labels_see_the_future(
        lambda frame: build_labels(frame, definition), bars, LABEL_COLUMNS
    )


def test_a_backward_looking_label_is_caught(bars):
    """Mirror of the feature test: prove this check is not vacuous."""

    def past_only(frame: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"ts": frame["ts"], "y_fwd_ret_H": frame["close"].pct_change()})

    with pytest.raises(PointInTimeViolation, match="not forward-looking"):
        assert_labels_see_the_future(past_only, bars, ["y_fwd_ret_H"])


def test_forward_return_matches_its_definition(definition):
    prices = [100.0 + i for i in range(40)]
    frame = _bars_from(prices)
    labels = build_labels(frame, definition, atr=pd.Series([5.0] * len(frame)))
    h = definition.horizon_bars
    drag = 2 * definition.cost_drag_per_side_bps / 10_000.0
    expected = prices[0 + h] / prices[0] - 1.0 - drag
    assert labels["y_fwd_ret_H"].iloc[0] == pytest.approx(expected)


def test_tail_rows_have_no_labels(bars, definition):
    labels = build_labels(bars, definition)
    tail = labels.tail(definition.horizon_bars)
    assert tail["y_fwd_ret_H"].isna().all()
    assert tail["y_outcome_long"].isna().all()


# --------------------------------------------------------------------------- #
# barrier resolution
# --------------------------------------------------------------------------- #


def test_long_target_hit_first(definition):
    # Stop 15 below, target 30 above (1.5 ATR of 10, R:R 2).
    frame = _bars_from([100.0, 105.0, 120.0, 131.0, 131.0, 131.0, 131.0, 131.0, 131.0, 131.0])
    labels = build_labels(frame, definition, atr=pd.Series([10.0] * len(frame)))
    assert labels["y_outcome_long"].iloc[0] == 1.0
    assert labels["y_hit_stop_long"].iloc[0] == 0.0


def test_long_stop_hit_first(definition):
    frame = _bars_from([100.0, 95.0, 84.0, 131.0, 131.0, 131.0, 131.0, 131.0, 131.0, 131.0])
    labels = build_labels(frame, definition, atr=pd.Series([10.0] * len(frame)))
    assert labels["y_outcome_long"].iloc[0] == -1.0
    assert labels["y_hit_stop_long"].iloc[0] == 1.0


def test_neither_barrier_touched_is_zero_not_a_win(definition):
    frame = _bars_from([100.0] * 12)
    labels = build_labels(frame, definition, atr=pd.Series([10.0] * len(frame)))
    assert labels["y_outcome_long"].iloc[0] == 0.0
    # Unresolved is not a stop-out, but it is also not a win.
    assert labels["y_hit_stop_long"].iloc[0] == 0.0


def test_same_bar_ambiguity_resolves_against_us(definition):
    """One bar spanning both barriers records a stop-out: without ticks we cannot
    know the order, and assuming the win is how backtests flatter themselves."""
    stamps = pd.date_range("2026-01-01 09:15", periods=12, freq="15min", tz="Asia/Kolkata")
    frame = pd.DataFrame(
        {
            "ts": stamps,
            "open": [100.0] * 12,
            "high": [100.0, 140.0] + [100.0] * 10,
            "low": [100.0, 80.0] + [100.0] * 10,
            "close": [100.0] * 12,
            "volume": [1_000] * 12,
        }
    )
    labels = build_labels(frame, definition, atr=pd.Series([10.0] * 12))
    assert labels["y_outcome_long"].iloc[0] == -1.0
    assert labels["y_outcome_short"].iloc[0] == -1.0


def test_short_side_is_mirrored(definition):
    frame = _bars_from([100.0, 95.0, 69.0] + [100.0] * 9)
    labels = build_labels(frame, definition, atr=pd.Series([10.0] * len(frame)))
    assert labels["y_outcome_short"].iloc[0] == 1.0
    assert labels["y_hit_stop_short"].iloc[0] == 0.0


def test_wider_stops_are_hit_less_often(bars):
    tight = build_labels(bars, LabelDefinition(8, 0.5, 2.0))
    wide = build_labels(bars, LabelDefinition(8, 3.0, 2.0))
    assert tight["y_hit_stop_long"].mean() > wide["y_hit_stop_long"].mean()


def test_label_ids_encode_the_policy():
    a = LabelDefinition(8, 1.5, 2.0, 2.0)
    b = LabelDefinition(8, 1.5, 6.0, 2.0)
    assert a.label_def_id != b.label_def_id
    assert "H8" in a.label_def_id and "rr2" in a.label_def_id


def test_invalid_definitions_are_refused():
    with pytest.raises(ValueError):
        LabelDefinition(0, 1.5, 2.0)
    with pytest.raises(ValueError):
        LabelDefinition(8, 0.0, 2.0)
    with pytest.raises(ValueError):
        LabelDefinition(8, 1.5, 0.0)


# --------------------------------------------------------------------------- #
# supervised table
# --------------------------------------------------------------------------- #


def test_supervised_table_is_complete_and_tagged(bars, definition):
    feature_set = build_feature_set_v0()
    table, report = build_supervised_table(
        bars,
        symbol="NIFTY",
        source="synthetic",
        feature_set=feature_set,
        label_definition=definition,
    )
    assert not table.isna().any().any()
    assert (table["feature_set_hash"] == feature_set.hash).all()
    assert (table["label_def_id"] == definition.label_def_id).all()
    assert report.rows == len(table)
    assert report.dropped_warmup >= feature_set.max_lookback
    assert report.dropped_horizon >= definition.horizon_bars


def test_supervised_table_row_count_accounts_for_every_drop(bars, definition):
    feature_set = build_feature_set_v0()
    _, report = build_supervised_table(
        bars,
        symbol="NIFTY",
        source="synthetic",
        feature_set=feature_set,
        label_definition=definition,
    )
    accounted = (
        report.rows + report.dropped_warmup + report.dropped_horizon + report.dropped_incomplete
    )
    assert accounted == report.input_bars


def test_supervised_table_reports_label_balance(bars, definition):
    _, report = build_supervised_table(
        bars,
        symbol="NIFTY",
        source="synthetic",
        feature_set=build_feature_set_v0(),
        label_definition=definition,
    )
    assert 0.0 < report.label_balance["y_hit_stop_long"] < 1.0


def test_stop_hit_rate_is_near_the_random_walk_prior(bars):
    """Sanity check on synthetic data: with R:R 2 a driftless series should hit
    the stop roughly two thirds of the time among resolved trades. A number far
    from that means the barrier logic is wrong, not that we found an edge."""
    labels = build_labels(bars, LabelDefinition(48, 1.0, 2.0)).dropna(subset=["y_outcome_long"])
    resolved = labels[labels["y_outcome_long"] != 0]
    rate = (resolved["y_outcome_long"] == -1).mean()
    assert 0.5 < rate < 0.85, rate
