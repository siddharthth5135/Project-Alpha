"""End-to-end ingestion: mock vendor -> quality gate -> store -> training table.

This is the rehearsal for credential day. If these pass, supplying real
credentials changes one config value and nothing else.
"""

from __future__ import annotations

import pandas as pd
import pytest

from alpha_desk.audit.logger import AuditLog
from alpha_desk.data.store import Dataset, MarketStore
from alpha_desk.features.catalogue import build_feature_set_v0
from alpha_desk.ingest.ml_bridge import build_training_table, load_promoted_bars, training_readiness
from alpha_desk.ingest.service import IngestionService, default_window, summarise
from alpha_desk.labels.definitions import default_label_definition
from alpha_desk.marketdata.contracts import BarInterval, Segment, contract_from_id
from alpha_desk.marketdata.errors import DataQualityError
from alpha_desk.marketdata.quality import Verdict
from alpha_desk.vendors.truedata.mock import (
    MOCK_EXPIRIES,
    FakeWsTransport,
    bar_frame,
    build_mock_provider,
    heartbeat_frame,
    touchline_frame,
    trade_frame,
)

IST = "Asia/Kolkata"
START = pd.Timestamp("2025-05-15 09:15", tz=IST).to_pydatetime()
END = pd.Timestamp("2025-08-14 15:30", tz=IST).to_pydatetime()


@pytest.fixture
def service(tmp_path):
    provider = build_mock_provider()
    store = MarketStore(tmp_path / "warehouse")
    audit = AuditLog(tmp_path / "audit", run_id="test")
    return IngestionService(provider, store, audit=audit), provider, store, audit


def test_backfill_persists_promoted_bars_with_an_audit_trail(service):
    svc, provider, store, audit = service
    contract = provider.continuous_future("NIFTY")

    result = svc.backfill_bars(contract, BarInterval.M15, START, END)

    assert result.ok
    assert result.report.verdict in (Verdict.ACCEPT, Verdict.ACCEPT_WITH_WARNINGS)
    assert result.rows > 1_000
    assert not result.quarantined

    stored = store.read_bars(source="truedata", contract=contract, interval="15m")
    assert len(stored) == result.rows
    assert set(stored["source"]) == {"truedata"}

    ok, message = audit.verify_chain()
    assert ok, message
    events = [record["event"] for record in audit.read_all()]
    assert "MARKET_DATA" in events


def test_second_backfill_is_incremental(service):
    svc, provider, _store, _audit = service
    contract = provider.continuous_future("NIFTY")

    svc.backfill_bars(contract, BarInterval.M15, START, END)
    again = svc.backfill_bars(contract, BarInterval.M15, START, END)

    assert again.rows == 0
    assert any("resumed from stored data" in note for note in again.notes)
    assert again.requests <= 1


def test_backfill_can_be_forced_to_refetch(service):
    svc, provider, store, _audit = service
    contract = provider.continuous_future("NIFTY")

    first = svc.backfill_bars(contract, BarInterval.M15, START, END)
    forced = svc.backfill_bars(contract, BarInterval.M15, START, END, incremental=False)

    assert forced.rows == first.rows
    coverage = store.coverage(Dataset.BARS)
    assert coverage["files"].iloc[0] >= 2


def test_training_table_is_built_from_stored_bars(service):
    svc, provider, store, _audit = service
    contract = provider.continuous_future("NIFTY")
    svc.backfill_bars(contract, BarInterval.M15, START, END)

    bundle = build_training_table(
        store,
        source="truedata",
        contract=contract,
        interval=BarInterval.M15,
        feature_set=build_feature_set_v0(),
        label_definition=default_label_definition(),
    )

    assert bundle.report.rows > 500
    assert bundle.frame["symbol"].iloc[0] == contract.contract_id
    assert bundle.frame["feature_set_hash"].nunique() == 1
    assert bundle.provenance["source"] == ["truedata"]
    assert bundle.provenance["bar_convention"] == ["interval_start"]
    assert not bundle.frame.isna().any().any()


def test_training_refuses_a_source_it_was_not_asked_for(service):
    svc, provider, store, _audit = service
    contract = provider.continuous_future("NIFTY")
    svc.backfill_bars(contract, BarInterval.M15, START, END)

    with pytest.raises(DataQualityError, match="no promoted bars"):
        load_promoted_bars(
            store,
            source="angelone",
            contract=contract,
            interval=BarInterval.M15,
        )


def test_training_refuses_mixed_bar_conventions(service):
    svc, provider, store, _audit = service
    contract = provider.continuous_future("NIFTY")
    svc.backfill_bars(contract, BarInterval.M15, START, END)

    stored = store.read_bars(source="truedata", contract=contract, interval="15m")
    tampered = stored.copy()
    tampered.loc[tampered.index[:10], "bar_convention"] = "interval_end"
    store.write_bars(tampered, source="truedata", contract=contract, interval="15m")

    with pytest.raises(DataQualityError, match="timestamp conventions"):
        load_promoted_bars(
            store, source="truedata", contract=contract, interval=BarInterval.M15
        )


def test_readiness_view_flags_thin_partitions(service):
    svc, provider, store, _audit = service
    contract = provider.continuous_future("NIFTY")
    svc.backfill_bars(contract, BarInterval.M15, START, END)

    readiness = training_readiness(store, source="truedata", minimum_bars=100_000)
    assert len(readiness) == 1
    assert not bool(readiness["ready"].iloc[0])
    assert not bool(readiness["blocking"].iloc[0])


def test_option_chain_snapshot_is_stored_and_scored(service):
    svc, _provider, store, _audit = service
    expiry = MOCK_EXPIRIES[0]

    result = svc.snapshot_option_chain("NIFTY", expiry, with_greeks=True)

    assert result.ok
    assert result.rows == 22
    stored = store.read_option_chain(source="truedata", underlying="NIFTY", expiry=expiry)
    assert stored["iv"].gt(0).all()
    assert result.report.warnings == [] or "asymmetric" not in " ".join(result.report.warnings)


def test_master_sync_writes_every_segment(service):
    svc, _provider, store, _audit = service
    counts = svc.sync_masters([Segment.FUTURES, Segment.OPTIONS, Segment.EQUITY])

    assert counts["FUT"] > 0
    assert counts["OPT"] > 0
    futures = store.read_masters(source="truedata", segment=Segment.FUTURES)
    assert set(futures["lot_size"].unique()) <= {30, 75}


def test_live_capture_persists_ticks_and_reports_stats(tmp_path):
    contracts_symbols: list[str] = []

    def on_subscribe(symbols: list[str]) -> list[str]:
        contracts_symbols.extend(symbols)
        frames = [touchline_frame(symbols)]
        for index in range(1, 7):
            ts = f"2025-08-14T09:{15 + index:02d}:00"
            frames.append(trade_frame("1001", ts, 24_000 + index, sequence=index))
            frames.append(bar_frame("1001", ts, 24_000 + index))
        frames.append(heartbeat_frame())
        return frames

    ws = FakeWsTransport(on_subscribe=on_subscribe)
    provider = build_mock_provider(ws=ws)
    store = MarketStore(tmp_path / "warehouse")
    svc = IngestionService(provider, store)

    spot = provider.spot_contract("NIFTY")
    session = svc.record_live([spot], max_events=15, flush_every=3)

    assert contracts_symbols == ["NIFTY 50"]
    assert session.ticks >= 3
    assert session.bars >= 3
    assert session.heartbeats >= 0
    assert session.receipts

    ticks = store.read_ticks(source="truedata", contract=spot)
    assert len(ticks) == session.ticks
    assert ticks["ltp"].gt(0).all()
    assert ticks["contract_id"].eq(spot.contract_id).all()

    live_bars = store.read_bars(source="truedata_live", contract=spot, interval="1m")
    assert len(live_bars) == session.bars
    assert session.stream_stats["connects"] == 1


def test_summary_rolls_up_a_backfill_run(service):
    svc, provider, _store, _audit = service
    contracts = [provider.continuous_future("NIFTY"), provider.spot_contract("NIFTY")]
    results = svc.backfill_many(contracts, BarInterval.M15, START, END)

    summary = summarise(results)
    assert summary["contracts"] == 2
    assert summary["rows"] > 2_000
    assert summary["errors"] == []


def test_default_window_spans_requested_days():
    start, end = default_window(30)
    assert (pd.Timestamp(end) - pd.Timestamp(start)).days == 30


def test_contract_from_id_round_trips_continuous_and_option():
    continuous = contract_from_id("NSE:FUT:NIFTY:CONT-vendor_spliced")
    assert continuous.is_continuous and continuous.contract_id.endswith("CONT-vendor_spliced")
    option = contract_from_id("NSE:OPT:NIFTY:2025-08-28:24000:CE")
    assert option.strike == 24000.0 and option.right.value == "CE"
    assert option.contract_id == "NSE:OPT:NIFTY:2025-08-28:24000:CE"
