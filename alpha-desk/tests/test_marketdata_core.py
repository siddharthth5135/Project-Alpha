"""Canonical market-data layer: contracts, calendar, normalisation, quality."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from alpha_desk.marketdata.calendar import load_market_calendar
from alpha_desk.marketdata.contracts import (
    PROVENANCE_COLUMNS,
    BarInterval,
    ContinuationStyle,
    Contract,
    OptionRight,
    Provenance,
    Segment,
)
from alpha_desk.marketdata.normalize import normalize_bars, normalize_option_chain, normalize_ticks
from alpha_desk.marketdata.provider import Capability, ProviderDescriptor
from alpha_desk.marketdata.quality import QualityThresholds, Verdict, assess_bars
from alpha_desk.marketdata.errors import NotEntitledError

IST = "Asia/Kolkata"
PROVENANCE = Provenance(source="truedata", vendor_symbol="NIFTY-I")


@pytest.fixture(scope="module")
def calendar():
    return load_market_calendar()


def _contract(**kwargs) -> Contract:
    base = {"symbol": "NIFTY", "segment": Segment.FUTURES, "continuation": ContinuationStyle.VENDOR_SPLICED}
    base.update(kwargs)
    return Contract(**base)


# --------------------------------------------------------------------------- #
# contracts
# --------------------------------------------------------------------------- #


def test_option_requires_strike_and_right():
    with pytest.raises(ValueError, match="strike"):
        Contract(symbol="NIFTY", segment=Segment.OPTIONS, expiry=date(2025, 8, 28))


def test_future_requires_expiry_unless_continuous():
    with pytest.raises(ValueError, match="expiry"):
        Contract(symbol="NIFTY", segment=Segment.FUTURES)
    assert _contract().is_continuous


def test_contract_id_is_stable_and_storage_safe():
    option = Contract(
        symbol="NIFTY",
        segment=Segment.OPTIONS,
        expiry=date(2025, 8, 28),
        strike=24000.0,
        right=OptionRight.CALL,
    )
    assert option.contract_id == "NSE:OPT:NIFTY:2025-08-28:24000:CE"
    assert ":" not in option.storage_key
    assert option.is_expired_style


def test_descriptor_require_reports_missing_capability():
    descriptor = ProviderDescriptor(
        name="thin",
        capabilities=frozenset({Capability.HISTORICAL_BARS}),
        segments=frozenset({Segment.FUTURES}),
    )
    descriptor.require(Capability.HISTORICAL_BARS)
    with pytest.raises(NotEntitledError, match="live_greeks"):
        descriptor.require(Capability.LIVE_GREEKS)


# --------------------------------------------------------------------------- #
# calendar
# --------------------------------------------------------------------------- #


def test_nse_session_has_375_one_minute_bars(calendar):
    index = calendar.expected_bar_index(
        Segment.FUTURES,
        BarInterval.M1,
        pd.Timestamp("2025-08-14 09:15", tz=IST),
        pd.Timestamp("2025-08-14 15:30", tz=IST),
    )
    assert len(index) == 375
    assert str(index[0].time()) == "09:15:00"
    assert str(index[-1].time()) == "15:29:00"


def test_holidays_and_weekends_are_not_expected(calendar):
    assert not calendar.is_trading_day(date(2025, 8, 15))
    assert not calendar.is_trading_day(date(2025, 8, 16))
    assert calendar.is_trading_day(date(2025, 8, 14))
    index = calendar.expected_bar_index(
        Segment.FUTURES,
        BarInterval.M15,
        pd.Timestamp("2025-08-14 09:15", tz=IST),
        pd.Timestamp("2025-08-18 15:30", tz=IST),
    )
    days = {ts.date() for ts in index}
    assert days == {date(2025, 8, 14), date(2025, 8, 18)}


def test_special_session_overrides_holiday_list(calendar):
    muhurat = date(2025, 10, 21)
    assert calendar.is_trading_day(muhurat)
    window = calendar.window(Segment.EQUITY, muhurat)
    assert (window.open.hour, window.close.hour) == (13, 14)


def test_observed_days_override_configured_holidays(calendar):
    learned = calendar.with_observed_days(frozenset({date(2025, 8, 15)}))
    assert learned.is_trading_day(date(2025, 8, 15))
    assert not learned.is_trading_day(date(2025, 8, 14))
    assert learned.is_verified(date(2030, 1, 1))


def test_unverified_dates_are_flagged(calendar):
    assert calendar.is_verified(date(2025, 6, 2))
    assert calendar.is_verified(date(2026, 6, 2))
    assert not calendar.is_verified(date(2027, 6, 2))


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #


def _raw_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [
                "2025-08-14T09:15:00",
                "2025-08-14T09:16:00",
                "2025-08-14T09:17:00",
                "2025-08-14T09:17:00",
                "2025-08-14T09:18:00",
                "not-a-timestamp",
            ],
            "open": [100, 101, 102, 102, 103, 104],
            "high": [101, 102, 103, 103, 102, 105],
            "low": [99, 100, 101, 101, 104, 103],
            "close": [100.5, 101.5, 102.5, 102.7, 103.5, 104.5],
            "volume": [10, 20, 30, 31, 40, 50],
            "oi": [1000, 1010, 1020, 1021, 1030, 1040],
        }
    )


def test_normalize_bars_localises_dedupes_and_keeps_rejects():
    result = normalize_bars(
        _raw_bars(), contract=_contract(), interval=BarInterval.M1, provenance=PROVENANCE
    )

    assert result.accepted == 3
    assert str(result.rows["ts"].dt.tz) == IST
    assert result.rows["ts"].is_monotonic_increasing
    assert result.rows["close"].iloc[-1] == 102.7  # duplicate resolved last-wins

    reasons = set(result.rejected["reject_reason"])
    assert reasons == {"unparseable timestamp", "inconsistent ohlc"}
    assert all(column in result.rows.columns for column in PROVENANCE_COLUMNS)
    assert set(result.rows["source"]) == {"truedata"}


def test_normalize_bars_raises_when_everything_is_rejected():
    broken = pd.DataFrame(
        {
            "ts": ["2025-08-14T09:15:00"],
            "open": [0],
            "high": [0],
            "low": [0],
            "close": [0],
            "volume": [1],
        }
    )
    result = normalize_bars(
        broken, contract=_contract(), interval=BarInterval.M1, provenance=PROVENANCE
    )
    with pytest.raises(Exception, match="every row rejected"):
        result.raise_if_all_rejected("NIFTY-I 1m")


def test_normalize_ticks_rejects_crossed_book():
    raw = pd.DataFrame(
        {
            "ts": ["2025-08-14T09:15:01", "2025-08-14T09:15:02"],
            "ltp": [100.0, 101.0],
            "volume": [5, 6],
            "bid": [100.5, 100.0],
            "ask": [100.0, 100.5],
            "sequence": [1, 2],
        }
    )
    result = normalize_ticks(raw, contract=_contract(), provenance=PROVENANCE)
    assert result.accepted == 1
    assert list(result.rejected["reject_reason"]) == ["crossed book"]


def test_normalize_option_chain_rejects_impossible_greeks():
    raw = pd.DataFrame(
        {
            "ts": ["2025-08-14T11:00:00"] * 3,
            "underlying": ["NIFTY"] * 3,
            "expiry": ["2025-08-28"] * 3,
            "strike": [24000, 24050, 24100],
            "right": ["CE", "XX", "PE"],
            "ltp": [120.0, 80.0, 90.0],
            "oi": [1000, 1000, 1000],
            "iv": [13.0, 12.0, 12.0],
            "delta": [0.55, 0.5, -3.0],
        }
    )
    result = normalize_option_chain(raw, provenance=PROVENANCE)
    assert result.accepted == 1
    assert set(result.rejected["reject_reason"]) == {"unknown option right", "implausible delta"}


# --------------------------------------------------------------------------- #
# quality
# --------------------------------------------------------------------------- #


def _clean_bars(calendar, interval=BarInterval.M15) -> pd.DataFrame:
    index = calendar.expected_bar_index(
        Segment.FUTURES,
        interval,
        pd.Timestamp("2025-08-14 09:15", tz=IST),
        pd.Timestamp("2025-08-14 15:30", tz=IST),
    )
    return pd.DataFrame(
        {
            "ts": index,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000,
            "oi": 5000,
        }
    )


def test_clean_series_is_accepted(calendar):
    report = assess_bars(
        _clean_bars(calendar),
        contract=_contract(),
        interval=BarInterval.M15,
        calendar=calendar,
    )
    assert report.verdict is Verdict.ACCEPT
    assert report.missing_rows == 0
    assert report.expected_rows == 25


def test_long_gap_quarantines(calendar):
    frame = _clean_bars(calendar)
    holed = pd.concat([frame.iloc[:2], frame.iloc[20:]], ignore_index=True)
    report = assess_bars(
        holed,
        contract=_contract(),
        interval=BarInterval.M15,
        calendar=calendar,
        thresholds=QualityThresholds(max_gap_run_bars=5),
    )
    assert report.verdict is Verdict.QUARANTINE
    assert report.longest_gap_run == 18
    assert any("contiguous gap" in error for error in report.errors)


def test_missing_session_is_reported(calendar):
    day_one = _clean_bars(calendar)
    frame = pd.concat(
        [day_one, day_one.assign(ts=day_one["ts"] + pd.Timedelta(days=4))], ignore_index=True
    )
    report = assess_bars(
        frame,
        contract=_contract(),
        interval=BarInterval.M15,
        calendar=calendar,
        expected_start=pd.Timestamp("2025-08-14 09:15", tz=IST),
        expected_end=pd.Timestamp("2025-08-19 15:30", tz=IST),
    )
    assert report.verdict is Verdict.QUARANTINE
    assert "2025-08-19" in report.missing_sessions


def test_empty_frame_is_quarantined(calendar):
    report = assess_bars(
        pd.DataFrame(),
        contract=_contract(),
        interval=BarInterval.M15,
        calendar=calendar,
    )
    assert report.verdict is Verdict.QUARANTINE
    assert not report.ok
