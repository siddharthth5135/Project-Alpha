"""Ingestion orchestration.

This is the seam between a vendor and the desk: fetch through the port, score
with the quality gate, persist through the store, and record what happened in
the audit log. It knows nothing about TrueData — swapping providers changes the
object handed to the constructor and nothing else.

Backfills are resumable by default. Re-running one asks the store for the newest
stored bar and requests only what is missing, which matters on a seven-day trial
where a re-run must not spend quota re-downloading history we already hold.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from alpha_desk.audit.logger import AuditEvent, AuditLog
from alpha_desk.data.store import Dataset, MarketStore, WriteReceipt
from alpha_desk.marketdata.calendar import MarketCalendar, load_market_calendar
from alpha_desk.marketdata.contracts import IST, BarInterval, Contract, Segment
from alpha_desk.marketdata.errors import MarketDataError, NoDataError
from alpha_desk.marketdata.quality import (
    QualityReport,
    QualityThresholds,
    Verdict,
    assess_bars,
    assess_option_chain,
)


@dataclass
class IngestResult:
    contract_id: str
    dataset: Dataset
    interval: str
    rows: int
    receipts: list[WriteReceipt] = field(default_factory=list)
    report: QualityReport | None = None
    notes: list[str] = field(default_factory=list)
    requests: int = 0
    elapsed_s: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and (self.report is None or self.report.ok)

    @property
    def quarantined(self) -> bool:
        return any(r.quarantined for r in self.receipts)

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "dataset": self.dataset.value,
            "interval": self.interval,
            "rows": self.rows,
            "files": [r.path.name for r in self.receipts],
            "verdict": self.report.verdict.value if self.report else None,
            "notes": self.notes,
            "requests": self.requests,
            "elapsed_s": round(self.elapsed_s, 3),
            "error": self.error,
        }


@dataclass
class LiveRecording:
    """What a live capture session saw. The trial's stability evidence."""

    started_at: datetime
    ended_at: datetime | None = None
    ticks: int = 0
    bars: int = 0
    greeks: int = 0
    touchlines: int = 0
    heartbeats: int = 0
    disconnects: int = 0
    unrouted: int = 0
    receipts: list[WriteReceipt] = field(default_factory=list)
    stream_stats: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "ticks": self.ticks,
            "bars": self.bars,
            "greeks": self.greeks,
            "touchlines": self.touchlines,
            "heartbeats": self.heartbeats,
            "disconnects": self.disconnects,
            "unrouted": self.unrouted,
            "files": [str(r.path) for r in self.receipts],
            "stream": self.stream_stats,
        }


class IngestionService:
    def __init__(
        self,
        provider,
        store: MarketStore,
        *,
        source: str | None = None,
        calendar: MarketCalendar | None = None,
        thresholds: QualityThresholds | None = None,
        audit: AuditLog | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._provider = provider
        self._store = store
        self._source = source or getattr(provider.descriptor, "name", "unknown")
        self._calendar = calendar or getattr(provider, "calendar", None) or load_market_calendar()
        self._thresholds = thresholds or QualityThresholds()
        self._audit = audit
        self._clock = clock

    @property
    def store(self) -> MarketStore:
        return self._store

    @property
    def source(self) -> str:
        return self._source

    # -- reference --------------------------------------------------------- #

    def sync_masters(self, segments: Sequence[Segment] | None = None) -> dict[str, int]:
        wanted = segments or list(getattr(self._provider, "config").segments)
        written: dict[str, int] = {}
        for segment in wanted:
            try:
                frame = self._provider.fetch_symbol_master(segment)
            except MarketDataError as exc:
                self._log(AuditEvent.ERROR, stage="sync_masters", segment=segment.value, error=str(exc))
                continue
            if frame.empty:
                continue
            self._store.write_masters(frame, source=self._source, segment=segment)
            written[segment.value] = len(frame)
        self._log(AuditEvent.MARKET_DATA, stage="sync_masters", counts=written)
        return written

    # -- history ----------------------------------------------------------- #

    def backfill_bars(
        self,
        contract: Contract,
        interval: BarInterval,
        start: datetime,
        end: datetime,
        *,
        incremental: bool = True,
    ) -> IngestResult:
        began = self._clock()
        window_start = start
        notes: list[str] = []

        if incremental:
            latest = self._store.latest_ts(
                dataset=Dataset.BARS,
                parts=(self._source, contract.storage_key, interval.value),
            )
            if latest is not None:
                resume = (latest + _interval_delta(interval)).to_pydatetime()
                if resume > _as_ts(window_start).to_pydatetime():
                    window_start = resume
                    notes.append(f"resumed from stored data at {latest}")

        if _as_ts(window_start) > _as_ts(end):
            return IngestResult(
                contract_id=contract.contract_id,
                dataset=Dataset.BARS,
                interval=interval.value,
                rows=0,
                notes=[*notes, "already up to date"],
                elapsed_s=self._clock() - began,
            )

        try:
            outcome = self._provider.fetch_bars_detailed(contract, interval, window_start, end)
        except NoDataError as exc:
            return IngestResult(
                contract_id=contract.contract_id,
                dataset=Dataset.BARS,
                interval=interval.value,
                rows=0,
                notes=[*notes, str(exc)],
                elapsed_s=self._clock() - began,
            )
        except MarketDataError as exc:
            self._log(
                AuditEvent.ERROR,
                stage="backfill_bars",
                contract=contract.contract_id,
                error=str(exc),
            )
            return IngestResult(
                contract_id=contract.contract_id,
                dataset=Dataset.BARS,
                interval=interval.value,
                rows=0,
                notes=notes,
                error=str(exc),
                elapsed_s=self._clock() - began,
            )

        report = assess_bars(
            outcome.frame,
            contract=contract,
            interval=interval,
            calendar=self._calendar,
            rejected_rows=len(outcome.rejected),
            thresholds=self._thresholds,
            expected_start=_as_ts(window_start),
            expected_end=_as_ts(end),
        )

        receipts = self._store.write_bars(
            outcome.frame,
            source=self._source,
            contract=contract,
            interval=interval.value,
            report=report,
        )
        if not outcome.rejected.empty:
            self._store.quarantine(
                outcome.rejected,
                dataset=Dataset.BARS,
                parts=(self._source, contract.storage_key, interval.value),
                reason="rejected during normalisation",
            )

        result = IngestResult(
            contract_id=contract.contract_id,
            dataset=Dataset.BARS,
            interval=interval.value,
            rows=len(outcome.frame),
            receipts=receipts,
            report=report,
            notes=[*notes, *outcome.notes],
            requests=outcome.requests,
            elapsed_s=self._clock() - began,
        )
        self._log(AuditEvent.MARKET_DATA, stage="backfill_bars", **result.as_dict())
        return result

    def backfill_many(
        self,
        contracts: Sequence[Contract],
        interval: BarInterval,
        start: datetime,
        end: datetime,
        *,
        incremental: bool = True,
        stop_on_error: bool = False,
    ) -> list[IngestResult]:
        results: list[IngestResult] = []
        for contract in contracts:
            result = self.backfill_bars(
                contract, interval, start, end, incremental=incremental
            )
            results.append(result)
            if stop_on_error and result.error:
                break
        return results

    def backfill_ticks(
        self, contract: Contract, start: datetime, end: datetime
    ) -> IngestResult:
        began = self._clock()
        try:
            frame = self._provider.fetch_ticks(contract, start, end)
        except MarketDataError as exc:
            return IngestResult(
                contract_id=contract.contract_id,
                dataset=Dataset.TICKS,
                interval="tick",
                rows=0,
                error=str(exc),
                elapsed_s=self._clock() - began,
            )
        receipts = self._store.write_ticks(frame, source=self._source, contract=contract)
        result = IngestResult(
            contract_id=contract.contract_id,
            dataset=Dataset.TICKS,
            interval="tick",
            rows=len(frame),
            receipts=receipts,
            elapsed_s=self._clock() - began,
        )
        self._log(AuditEvent.MARKET_DATA, stage="backfill_ticks", **result.as_dict())
        return result

    # -- options ----------------------------------------------------------- #

    def snapshot_option_chain(
        self, underlying: str, expiry: date, *, with_greeks: bool = True
    ) -> IngestResult:
        began = self._clock()
        try:
            outcome = self._provider.fetch_option_chain_detailed(
                underlying, expiry, with_greeks=with_greeks
            )
        except MarketDataError as exc:
            return IngestResult(
                contract_id=f"{underlying}:{expiry.isoformat()}",
                dataset=Dataset.OPTION_CHAIN,
                interval="snapshot",
                rows=0,
                error=str(exc),
                elapsed_s=self._clock() - began,
            )

        report = assess_option_chain(
            outcome.frame,
            underlying=underlying,
            expiry=expiry,
            rejected_rows=len(outcome.rejected),
        )
        receipts = self._store.write_option_chain(
            outcome.frame,
            source=self._source,
            underlying=underlying,
            expiry=expiry,
            report=report,
        )
        result = IngestResult(
            contract_id=f"{underlying}:{expiry.isoformat()}",
            dataset=Dataset.OPTION_CHAIN,
            interval="snapshot",
            rows=len(outcome.frame),
            receipts=receipts,
            report=report,
            notes=outcome.notes,
            requests=1,
            elapsed_s=self._clock() - began,
        )
        self._log(AuditEvent.MARKET_DATA, stage="option_chain", **result.as_dict())
        return result

    # -- live -------------------------------------------------------------- #

    def record_live(
        self,
        contracts: Sequence[Contract],
        *,
        max_events: int | None = None,
        max_seconds: float | None = None,
        flush_every: int = 500,
    ) -> LiveRecording:
        """Subscribe, persist every event, and stop on a bound.

        Buffers are flushed on a count trigger and again on exit, including on
        interrupt, so a captured session is never lost because the operator hit
        Ctrl-C.
        """
        from alpha_desk.marketdata.events import EventKind

        session = LiveRecording(started_at=pd.Timestamp.now(tz=IST).to_pydatetime())
        by_id = {c.contract_id: c for c in contracts}
        ticks: list[dict[str, object]] = []
        bars: list[dict[str, object]] = []
        greeks: list[dict[str, object]] = []

        self._provider.subscribe(contracts)
        deadline = None if max_seconds is None else self._clock() + max_seconds
        seen = 0

        try:
            for event in self._provider.stream():
                seen += 1
                if event.kind is EventKind.TICK:
                    session.ticks += 1
                    ticks.append(_as_row(event.payload, event))
                elif event.kind is EventKind.BID_ASK:
                    session.ticks += 1
                    ticks.append(_as_row(event.payload, event))
                elif event.kind is EventKind.BAR:
                    session.bars += 1
                    bars.append(_as_row(event.payload, event))
                elif event.kind is EventKind.GREEKS:
                    session.greeks += 1
                    greeks.append(_as_row(event.payload, event))
                elif event.kind is EventKind.TOUCHLINE:
                    session.touchlines += 1
                elif event.kind is EventKind.HEARTBEAT:
                    session.heartbeats += 1
                elif event.kind is EventKind.DISCONNECTED:
                    session.disconnects += 1
                    self._log(AuditEvent.FEED_STATE, state="disconnected", reason=str(event.payload))
                elif event.kind is EventKind.UNROUTED:
                    session.unrouted += 1

                if len(ticks) >= flush_every:
                    session.receipts.extend(self._flush_live(ticks, by_id, Dataset.TICKS))
                if len(bars) >= flush_every:
                    session.receipts.extend(self._flush_live(bars, by_id, Dataset.BARS))
                if len(greeks) >= flush_every:
                    session.receipts.extend(self._flush_live(greeks, by_id, Dataset.GREEKS))

                if max_events is not None and seen >= max_events:
                    break
                if deadline is not None and self._clock() >= deadline:
                    break
        except KeyboardInterrupt:
            self._log(AuditEvent.FEED_STATE, state="interrupted")
        finally:
            session.receipts.extend(self._flush_live(ticks, by_id, Dataset.TICKS))
            session.receipts.extend(self._flush_live(bars, by_id, Dataset.BARS))
            session.receipts.extend(self._flush_live(greeks, by_id, Dataset.GREEKS))
            session.ended_at = pd.Timestamp.now(tz=IST).to_pydatetime()
            stream_client = getattr(self._provider, "stream_client", None)
            if stream_client is not None:
                session.stream_stats = stream_client.stats.as_dict()
            self._log(AuditEvent.MARKET_DATA, stage="record_live", **session.as_dict())

        return session

    def _flush_live(
        self,
        buffer: list[dict[str, object]],
        by_id: dict[str, Contract],
        dataset: Dataset,
    ) -> list[WriteReceipt]:
        if not buffer:
            return []
        frame = pd.DataFrame(buffer)
        buffer.clear()
        receipts: list[WriteReceipt] = []

        for contract_id, chunk in frame.groupby("contract_id"):
            contract = by_id.get(str(contract_id))
            if contract is None:
                self._store.quarantine(
                    chunk,
                    dataset=dataset,
                    parts=(self._source, "unresolved"),
                    reason=f"event for unsubscribed contract {contract_id}",
                )
                continue
            if dataset is Dataset.TICKS:
                receipts.extend(
                    self._store.write_ticks(chunk, source=self._source, contract=contract)
                )
            elif dataset is Dataset.BARS:
                interval = str(chunk["interval"].iloc[0]) if "interval" in chunk else "1m"
                receipts.extend(
                    self._store.write_bars(
                        chunk.drop(columns=["interval"], errors="ignore"),
                        source=f"{self._source}_live",
                        contract=contract,
                        interval=interval,
                    )
                )
            else:
                receipts.append(
                    self._store.write_table(
                        chunk,
                        dataset=dataset,
                        parts=(self._source, contract.storage_key),
                        stem=pd.Timestamp.now(tz=IST).strftime("%Y-%m-%d"),
                    )
                )
        return receipts

    # -- audit ------------------------------------------------------------- #

    def _log(self, event: AuditEvent, **payload: object) -> None:
        if self._audit is None:
            return
        self._audit.write(event, source=self._source, **payload)


def _as_row(payload: object, event) -> dict[str, object]:
    row = {k: v for k, v in vars(payload).items() if not k.startswith("_")}
    if hasattr(payload, "interval"):
        row["interval"] = getattr(payload.interval, "value", payload.interval)
    row["received_at"] = event.received_at
    row["latency_s"] = event.latency_s
    return row


def _interval_delta(interval: BarInterval) -> pd.Timedelta:
    if interval is BarInterval.EOD:
        return pd.Timedelta(days=1)
    if interval is BarInterval.TICK:
        return pd.Timedelta(seconds=1)
    return pd.Timedelta(minutes=interval.minutes)


def _as_ts(value: datetime | str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def summarise(results: Sequence[IngestResult]) -> dict[str, object]:
    """Compact roll-up for a backfill run, suitable for a console summary."""
    total_rows = sum(r.rows for r in results)
    return {
        "contracts": len(results),
        "rows": total_rows,
        "requests": sum(r.requests for r in results),
        "quarantined": sum(1 for r in results if r.quarantined),
        "errors": [
            {"contract": r.contract_id, "error": r.error} for r in results if r.error
        ],
        "warned": [
            {"contract": r.contract_id, "warnings": r.report.warnings}
            for r in results
            if r.report and r.report.verdict is Verdict.ACCEPT_WITH_WARNINGS
        ],
        "elapsed_s": round(sum(r.elapsed_s for r in results), 2),
    }


def default_window(days: int, *, end: datetime | None = None) -> tuple[datetime, datetime]:
    stop = _as_ts(end or pd.Timestamp.now(tz=IST))
    return (stop - timedelta(days=days)).to_pydatetime(), stop.to_pydatetime()
