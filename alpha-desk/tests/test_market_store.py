"""Store semantics: immutability, versioning, manifests, quarantine."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from alpha_desk.data.store import Dataset, MarketStore
from alpha_desk.marketdata.contracts import (
    ContinuationStyle,
    Contract,
    OptionRight,
    Provenance,
    Segment,
)
from alpha_desk.marketdata.normalize import normalize_bars, normalize_option_chain
from alpha_desk.marketdata.contracts import BarInterval
from alpha_desk.marketdata.quality import QualityReport, Verdict

IST = "Asia/Kolkata"
CONTRACT = Contract(
    symbol="NIFTY", segment=Segment.FUTURES, continuation=ContinuationStyle.VENDOR_SPLICED
)
PROVENANCE = Provenance(source="truedata", vendor_symbol="NIFTY-I")


@pytest.fixture
def store(tmp_path) -> MarketStore:
    return MarketStore(tmp_path / "warehouse")


def _bars(start: str, periods: int, close: float = 100.0) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="15min", tz=IST)
    raw = pd.DataFrame(
        {
            "ts": index,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close + 0.5,
            "volume": 1_000,
            "oi": 5_000,
        }
    )
    return normalize_bars(
        raw, contract=CONTRACT, interval=BarInterval.M15, provenance=PROVENANCE
    ).rows


def test_rewriting_a_period_adds_a_version_and_keeps_the_original(store):
    first = store.write_bars(
        _bars("2025-08-14 09:15", 4, close=100.0),
        source="truedata",
        contract=CONTRACT,
        interval="15m",
    )
    second = store.write_bars(
        _bars("2025-08-14 09:15", 4, close=200.0),
        source="truedata",
        contract=CONTRACT,
        interval="15m",
    )

    assert [r.version for r in first] == [1]
    assert [r.version for r in second] == [2]
    assert first[0].path.exists() and second[0].path.exists()
    assert first[0].content_sha256 != second[0].content_sha256

    merged = store.read_bars(source="truedata", contract=CONTRACT, interval="15m")
    assert len(merged) == 4
    assert merged["close"].eq(200.5).all()  # newest version wins


def test_bars_are_partitioned_by_month(store):
    receipts = store.write_bars(
        _bars("2025-07-31 23:00", 8),
        source="truedata",
        contract=CONTRACT,
        interval="15m",
    )
    assert sorted(r.path.stem.split("__")[0] for r in receipts) == ["2025-07", "2025-08"]


def test_manifest_and_ingest_log_record_every_write(store):
    report = QualityReport(
        dataset="bars", contract_id=CONTRACT.contract_id, interval="15m", rows=4
    )
    receipts = store.write_bars(
        _bars("2025-08-14 09:15", 4),
        source="truedata",
        contract=CONTRACT,
        interval="15m",
        report=report,
    )
    manifest = receipts[0].path.parent / "_manifest.json"
    assert manifest.exists()

    log = store.ingest_log()
    assert len(log) == 1
    assert log[0]["rows"] == 4
    assert log[0]["verdict"] == Verdict.ACCEPT.value
    assert log[0]["content_sha256"] == receipts[0].content_sha256
    assert log[0]["quality"]["contract_id"] == CONTRACT.contract_id


def test_quarantine_verdict_never_reaches_the_read_path(store):
    report = QualityReport(
        dataset="bars",
        contract_id=CONTRACT.contract_id,
        interval="15m",
        rows=4,
        verdict=Verdict.QUARANTINE,
        errors=["whole sessions missing: ['2025-08-13']"],
    )
    receipts = store.write_bars(
        _bars("2025-08-14 09:15", 4),
        source="truedata",
        contract=CONTRACT,
        interval="15m",
        report=report,
    )

    assert receipts[0].quarantined
    assert "quarantine" in receipts[0].path.parts
    assert store.read_bars(source="truedata", contract=CONTRACT, interval="15m").empty

    quarantined = pd.read_parquet(receipts[0].path)
    assert "2025-08-13" in quarantined["quarantine_reason"].iloc[0]


def test_rejected_rows_can_be_quarantined_separately(store):
    rejected = pd.DataFrame({"ts": ["bad"], "close": [1.0], "reject_reason": ["bad ts"]})
    receipt = store.quarantine(
        rejected,
        dataset=Dataset.BARS,
        parts=("truedata", CONTRACT.storage_key, "15m"),
        reason="rejected during normalisation",
    )
    assert receipt is not None
    assert receipt.rows == 1
    assert store.quarantine(pd.DataFrame(), dataset=Dataset.BARS, parts=("x",), reason="") is None


def test_latest_ts_supports_incremental_resume(store):
    store.write_bars(
        _bars("2025-08-14 09:15", 4), source="truedata", contract=CONTRACT, interval="15m"
    )
    latest = store.latest_ts(
        dataset=Dataset.BARS, parts=("truedata", CONTRACT.storage_key, "15m")
    )
    assert latest == pd.Timestamp("2025-08-14 10:00", tz=IST)
    assert (
        store.latest_ts(dataset=Dataset.BARS, parts=("truedata", "UNKNOWN", "15m")) is None
    )


def test_read_window_is_inclusive_and_timezone_aware(store):
    store.write_bars(
        _bars("2025-08-14 09:15", 8), source="truedata", contract=CONTRACT, interval="15m"
    )
    sliced = store.read_bars(
        source="truedata",
        contract=CONTRACT,
        interval="15m",
        start="2025-08-14 09:45",
        end="2025-08-14 10:15",
    )
    assert len(sliced) == 3
    assert str(sliced["ts"].iloc[0].time()) == "09:45:00"


def test_option_chain_snapshots_append_within_a_day(store):
    expiry = date(2025, 8, 28)
    raw = pd.DataFrame(
        {
            "ts": ["2025-08-14T11:00:00", "2025-08-14T11:00:00"],
            "underlying": ["NIFTY", "NIFTY"],
            "expiry": [expiry, expiry],
            "strike": [24000.0, 24000.0],
            "right": [OptionRight.CALL.value, OptionRight.PUT.value],
            "ltp": [120.0, 95.0],
            "oi": [1000, 1200],
        }
    )
    frame = normalize_option_chain(raw, provenance=PROVENANCE).rows
    store.write_option_chain(frame, source="truedata", underlying="NIFTY", expiry=expiry)
    store.write_option_chain(frame, source="truedata", underlying="NIFTY", expiry=expiry)

    stored = store.read_option_chain(source="truedata", underlying="NIFTY", expiry=expiry)
    assert len(stored) == 2  # same snapshot key deduplicated across versions

    coverage = store.coverage(Dataset.OPTION_CHAIN)
    assert coverage["files"].iloc[0] == 2
    assert coverage["rows"].iloc[0] == 4


def test_masters_are_written_per_segment(store):
    frame = pd.DataFrame(
        {
            "as_of": [pd.Timestamp("2025-08-14", tz="UTC")],
            "contract_id": ["NSE:FUT:NIFTY:2025-08-28"],
            "symbol": ["NIFTY"],
            "segment": ["FUT"],
            "exchange": ["NSE"],
            "expiry": [date(2025, 8, 28)],
            "strike": [None],
            "right": [None],
            "lot_size": [75],
            "tick_size": [0.05],
            "vendor_symbol": ["NIFTY25AUG28FUT"],
            "vendor_token": ["4321"],
        }
    )
    store.write_masters(frame, source="truedata", segment=Segment.FUTURES)
    stored = store.read_masters(source="truedata", segment=Segment.FUTURES)
    assert stored["vendor_symbol"].iloc[0] == "NIFTY25AUG28FUT"


def test_storage_keys_are_filesystem_safe(store):
    option = Contract(
        symbol="NIFTY",
        segment=Segment.OPTIONS,
        expiry=date(2025, 8, 28),
        strike=24000.0,
        right=OptionRight.CALL,
    )
    directory = store.partition_dir(Dataset.BARS, "truedata", option.storage_key, "1m")
    relative = directory.relative_to(store.root)
    assert ":" not in str(relative)
    assert relative.parts[-2] == "NSE__OPT__NIFTY__2025-08-28__24000__CE"
