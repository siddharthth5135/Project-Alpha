"""Feature pipeline tests (playbook step P1.1.3).

Exit gate: point-in-time tests pass, the shift-future attack is caught, and the
feature hash is stable for the same definitions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_desk.data.providers.synthetic import SyntheticProvider
from alpha_desk.features import builders as b
from alpha_desk.features.catalogue import FEATURE_SET_V0, build_feature_set_v0
from alpha_desk.features.pit import (
    PointInTimeViolation,
    assert_point_in_time,
    shift_future_attack,
)
from alpha_desk.features.registry import FeatureFreezeError, FeatureSet, FeatureSpec


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return SyntheticProvider(seed=3).fetch("NIFTY", "15m", "2025-10-01", "2026-03-31")


@pytest.fixture
def feature_set() -> FeatureSet:
    return build_feature_set_v0()


# --------------------------------------------------------------------------- #
# leakage - the reason this step exists
# --------------------------------------------------------------------------- #


def test_features_are_point_in_time(feature_set, bars):
    assert_point_in_time(feature_set, bars, samples=8)


def test_features_survive_the_shift_future_attack(feature_set, bars):
    shift_future_attack(feature_set, bars, samples=8)


def test_the_attack_actually_catches_a_leaky_feature(bars):
    """Guard against a vacuous test: a deliberately leaky feature must fail."""

    def peeks_ahead(frame: pd.DataFrame) -> pd.Series:
        return frame["close"].shift(-1) / frame["close"] - 1.0

    leaky = FeatureSet("leaky", "v0", [FeatureSpec("tomorrow", 1, peeks_ahead, "leaks")])
    with pytest.raises(PointInTimeViolation):
        shift_future_attack(leaky, bars, samples=3)


def test_the_attack_catches_full_sample_normalisation(bars):
    """Whole-sample statistics survive truncation but not perturbation."""

    def whole_sample_zscore(frame: pd.DataFrame) -> pd.Series:
        r = frame["close"].pct_change()
        return (r - r.mean()) / r.std()

    leaky = FeatureSet(
        "leaky2", "v0", [FeatureSpec("z_all", 2, whole_sample_zscore, "full-sample z")]
    )
    with pytest.raises(PointInTimeViolation):
        shift_future_attack(leaky, bars, samples=3)


def test_vol_regime_bucket_uses_trailing_quantiles_only(feature_set, bars):
    """This is the feature most likely to leak, so it gets its own check."""
    single = FeatureSet(
        "regime_only",
        "v0",
        [
            FeatureSpec(
                "vol_regime_bucket",
                b.REGIME_WINDOW + b.VOL_WINDOW + 1,
                b.vol_regime_bucket,
                "trailing tercile",
            )
        ],
    )
    assert_point_in_time(single, bars, samples=5)
    shift_future_attack(single, bars, samples=5)


# --------------------------------------------------------------------------- #
# schema, hash, freeze
# --------------------------------------------------------------------------- #


def test_hash_is_stable_across_instances():
    assert build_feature_set_v0().hash == build_feature_set_v0().hash


def test_hash_changes_when_a_feature_is_added(feature_set):
    extended = FeatureSet(
        feature_set.name,
        feature_set.version,
        [*feature_set.specs, FeatureSpec("noise", 1, lambda f: f["close"] * 0, "junk")],
    )
    assert extended.hash != feature_set.hash


def test_hash_changes_when_an_implementation_changes(feature_set):
    def different_rsi(frame: pd.DataFrame) -> pd.Series:
        return frame["close"].pct_change().rolling(9).mean()

    swapped = FeatureSet(
        feature_set.name,
        feature_set.version,
        [
            FeatureSpec(s.name, s.lookback, different_rsi, s.description)
            if s.name == "rsi_14"
            else s
            for s in feature_set.specs
        ],
    )
    assert swapped.hash != feature_set.hash


def test_schema_lists_every_feature(feature_set):
    schema = feature_set.schema()
    assert {f["name"] for f in schema["features"]} == set(feature_set.feature_names)
    assert schema["max_lookback"] == feature_set.max_lookback


def test_duplicate_feature_names_are_refused():
    spec = FeatureSpec("dup", 1, lambda f: f["close"], "x")
    with pytest.raises(ValueError, match="duplicate"):
        FeatureSet("bad", "v0", [spec, spec])


def test_freeze_then_verify_passes(feature_set, tmp_path):
    feature_set.freeze(tmp_path)
    feature_set.verify_freeze(tmp_path)


def test_verify_without_a_lock_file_fails(feature_set, tmp_path):
    with pytest.raises(FeatureFreezeError, match="no lock file"):
        feature_set.verify_freeze(tmp_path)


def test_changing_a_frozen_set_fails_closed(feature_set, tmp_path):
    feature_set.freeze(tmp_path)
    mutated = FeatureSet(
        feature_set.name,
        feature_set.version,
        [*feature_set.specs, FeatureSpec("sneaky", 1, lambda f: f["close"] * 0, "added later")],
    )
    with pytest.raises(FeatureFreezeError, match="added=\\['sneaky'\\]"):
        mutated.verify_freeze(tmp_path)


def test_refreezing_a_changed_set_is_refused(feature_set, tmp_path):
    feature_set.freeze(tmp_path)
    mutated = FeatureSet(
        feature_set.name,
        feature_set.version,
        [*feature_set.specs, FeatureSpec("sneaky", 1, lambda f: f["close"] * 0, "added later")],
    )
    with pytest.raises(FeatureFreezeError, match="Bump the feature set version"):
        mutated.freeze(tmp_path)


def test_shipped_lock_file_matches_the_code():
    """The committed lock is the contract. If this fails, someone edited a
    feature without bumping the version."""
    from alpha_desk.config.settings import CONFIG_DIR

    FEATURE_SET_V0.verify_freeze(CONFIG_DIR / "feature_locks")


# --------------------------------------------------------------------------- #
# output shape and content
# --------------------------------------------------------------------------- #


def test_build_drops_the_warmup_window(feature_set, bars):
    out = feature_set.build(bars)
    assert len(out) == len(bars) - feature_set.max_lookback


def test_build_has_no_missing_values_after_warmup(feature_set, bars):
    out = feature_set.build(bars)
    assert not out.isna().any().any(), out.isna().sum()[lambda s: s > 0].to_dict()


def test_build_preserves_timestamps(feature_set, bars):
    out = feature_set.build(bars)
    assert out["ts"].is_monotonic_increasing
    assert out["ts"].iloc[0] == bars["ts"].iloc[feature_set.max_lookback]


def test_empty_input_yields_empty_frame(feature_set):
    out = feature_set.build(bars=pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"]))
    assert out.empty


def test_time_of_day_spans_the_session(feature_set, bars):
    tod = b.time_of_day(bars)
    assert tod.min() == pytest.approx(0.0)
    assert tod.max() == pytest.approx(1.0)


def test_day_of_week_is_weekdays_only(bars):
    assert set(b.day_of_week(bars).unique()) <= {0.0, 1.0, 2.0, 3.0, 4.0}


def test_rsi_stays_in_range(bars):
    rsi = b.rsi_14(bars).dropna()
    assert rsi.between(0, 100).all()


def test_vol_regime_bucket_only_takes_three_values(bars):
    buckets = b.vol_regime_bucket(bars).dropna()
    assert set(buckets.unique()) <= {0.0, 1.0, 2.0}


def test_atr_pct_is_scale_free():
    """The points-based ATR differs between instruments at the same volatility;
    the percentage version does not. That is why models use the percentage."""
    cheap = SyntheticProvider(seed=5, start_price=1_000.0).fetch(
        "X", "15m", "2026-01-01", "2026-02-01"
    )
    rich = cheap.copy()
    for column in ("open", "high", "low", "close"):
        rich[column] = rich[column] * 50

    assert b.atr_14(rich).iloc[-1] > b.atr_14(cheap).iloc[-1] * 10
    assert b.atr_14_pct(rich).iloc[-1] == pytest.approx(
        b.atr_14_pct(cheap).iloc[-1], rel=1e-6
    )


def test_a_feature_returning_the_wrong_length_is_rejected(bars):
    bad = FeatureSet("bad", "v0", [FeatureSpec("short", 1, lambda f: f["close"].head(3), "x")])
    with pytest.raises(Exception, match="rows"):
        bad.build(bars)


def test_zero_volume_falls_back_for_vwap(bars):
    """Yahoo reports zero volume on indices; VWAP must still produce numbers."""
    flat = bars.copy()
    flat["volume"] = 0
    out = b.vwap_distance(flat)
    assert np.isfinite(out.to_numpy()).all()
