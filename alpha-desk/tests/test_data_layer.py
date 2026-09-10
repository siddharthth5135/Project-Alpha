"""Data schema, quality checks and warehouse round-trips."""

from __future__ import annotations

import pandas as pd
import pytest

from alpha_desk.data.providers.synthetic import SyntheticProvider
from alpha_desk.data.schema import BarSchemaError, normalize_bars, validate_bars
from alpha_desk.data.warehouse import ParquetWarehouse


@pytest.fixture
def bars():
    return SyntheticProvider(seed=7).fetch("NIFTY", "15m", "2026-06-01", "2026-06-15")


def test_synthetic_provider_is_deterministic():
    a = SyntheticProvider(seed=7).fetch("NIFTY", "15m", "2026-06-01", "2026-06-05")
    b = SyntheticProvider(seed=7).fetch("NIFTY", "15m", "2026-06-01", "2026-06-05")
    pd.testing.assert_frame_equal(a, b)


def test_bars_are_ist_and_sorted(bars):
    assert str(bars["ts"].dt.tz) == "Asia/Kolkata"
    assert bars["ts"].is_monotonic_increasing


def test_synthetic_bars_pass_quality_checks(bars):
    report = validate_bars(bars, symbol="NIFTY", timeframe="15m")
    assert report.is_clean, report.issues


def test_missing_columns_raise():
    with pytest.raises(BarSchemaError):
        normalize_bars(pd.DataFrame({"ts": [], "close": []}))


def test_duplicate_timestamps_are_collapsed():
    frame = pd.DataFrame(
        {
            "ts": ["2026-06-01 09:15", "2026-06-01 09:15", "2026-06-01 09:30"],
            "open": [1.0, 1.0, 2.0],
            "high": [1.0, 1.0, 2.0],
            "low": [1.0, 1.0, 2.0],
            "close": [1.0, 1.5, 2.0],
            "volume": [10, 20, 30],
        }
    )
    out = normalize_bars(frame)
    assert len(out) == 2
    assert out.iloc[0]["close"] == 1.5  # last write wins


def test_inconsistent_ohlc_is_reported():
    frame = normalize_bars(
        pd.DataFrame(
            {
                "ts": ["2026-06-01 09:15"],
                "open": [100.0],
                "high": [90.0],
                "low": [95.0],
                "close": [99.0],
                "volume": [10],
            }
        )
    )
    report = validate_bars(frame, symbol="X", timeframe="15m")
    assert not report.is_clean
    assert report.inverted_high_low == 1


def test_warehouse_round_trip(tmp_path, bars):
    wh = ParquetWarehouse(tmp_path / "data")
    report = wh.write_bars("synthetic", "NIFTY", "15m", bars)
    assert report.is_clean

    back = wh.read_bars("synthetic", "NIFTY", "15m")
    assert len(back) == len(bars)
    pd.testing.assert_series_equal(back["close"], bars["close"])


def test_warehouse_writes_are_idempotent(tmp_path, bars):
    wh = ParquetWarehouse(tmp_path / "data")
    wh.write_bars("synthetic", "NIFTY", "15m", bars)
    wh.write_bars("synthetic", "NIFTY", "15m", bars)
    assert len(wh.read_bars("synthetic", "NIFTY", "15m")) == len(bars)


def test_warehouse_merges_new_bars(tmp_path):
    wh = ParquetWarehouse(tmp_path / "data")
    june = SyntheticProvider(seed=1).fetch("NIFTY", "15m", "2026-06-01", "2026-06-10")
    july = SyntheticProvider(seed=1).fetch("NIFTY", "15m", "2026-07-01", "2026-07-10")
    wh.write_bars("synthetic", "NIFTY", "15m", june)
    wh.write_bars("synthetic", "NIFTY", "15m", july)
    assert len(wh.read_bars("synthetic", "NIFTY", "15m")) == len(june) + len(july)
    assert wh.available() == {"synthetic": {"NIFTY": ["15m"]}}


def test_sources_never_mix(tmp_path, bars):
    """Fabricated bars must not merge into a real-data series."""
    wh = ParquetWarehouse(tmp_path / "data")
    wh.write_bars("synthetic", "NIFTY", "15m", bars)
    wh.write_bars("yahoo", "NIFTY", "15m", bars.head(10))
    assert len(wh.read_bars("synthetic", "NIFTY", "15m")) == len(bars)
    assert len(wh.read_bars("yahoo", "NIFTY", "15m")) == 10


def test_reading_an_unknown_symbol_returns_empty(tmp_path):
    wh = ParquetWarehouse(tmp_path / "data")
    assert wh.read_bars("synthetic", "UNKNOWN", "15m").empty


def test_yahoo_provider_declares_its_limits():
    from alpha_desk.data.providers.yahoo import YahooProvider

    provider = YahooProvider({"NIFTY": "^NSEI"})
    assert provider.research_only is True
    assert provider.ticker_for("NIFTY") == "^NSEI"
    assert provider.max_lookback_days("15m") == 60
    with pytest.raises(ValueError):
        provider.max_lookback_days("3m")
