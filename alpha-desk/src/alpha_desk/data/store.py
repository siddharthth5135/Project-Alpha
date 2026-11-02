"""Market-data store.

Three properties are non-negotiable for anything a model will later be trained
on, and they shape this whole module:

* **Raw is immutable.** A write never edits an existing file. Re-ingesting a
  period produces a new numbered version alongside the old one, so a vendor
  restatement is visible in history instead of overwriting it. Reads merge
  versions with the newest winning, which keeps the read path deterministic.
* **Nothing enters silently.** Every write appends to a hash-stamped ingest log
  and updates a partition manifest with row counts, the covered window, the
  quality verdict and the content digest of the file.
* **Bad data is kept, not dropped.** Frames that fail quality gates land in a
  quarantine tree with the reason attached, where they can be inspected without
  polluting training reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

import pandas as pd

from alpha_desk.marketdata.contracts import SCHEMA_VERSION, Contract, Segment
from alpha_desk.marketdata.quality import QualityReport, Verdict

_VERSION_RE = re.compile(r"^(?P<stem>.+)__v(?P<version>\d+)\.parquet$")
_SAFE = re.compile(r"[^A-Za-z0-9_.=\-]+")


class Dataset(str, Enum):
    BARS = "bars"
    TICKS = "ticks"
    OPTION_CHAIN = "option_chain"
    GREEKS = "greeks"
    MASTERS = "masters"
    CORPORATE_ACTIONS = "corporate_actions"
    BHAVCOPY = "bhavcopy"
    LIVE_EVENTS = "live_events"


@dataclass(frozen=True)
class WriteReceipt:
    dataset: Dataset
    path: Path
    rows: int
    version: int
    verdict: Verdict
    content_sha256: str
    first_ts: str | None = None
    last_ts: str | None = None
    quarantined: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset.value,
            "path": str(self.path),
            "rows": self.rows,
            "version": self.version,
            "verdict": self.verdict.value,
            "content_sha256": self.content_sha256,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "quarantined": self.quarantined,
        }


class MarketStore:
    """Partitioned parquet store with versioned, append-only semantics."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ------------------------------------------------------------- #

    @property
    def ingest_log_path(self) -> Path:
        return self.root / "_ingest_log.jsonl"

    def partition_dir(self, dataset: Dataset, *parts: str) -> Path:
        path = self.root / dataset.value
        for part in parts:
            path = path / _safe(part)
        return path

    def quarantine_dir(self, dataset: Dataset, *parts: str) -> Path:
        path = self.root / "quarantine" / dataset.value
        for part in parts:
            path = path / _safe(part)
        return path

    # -- writes ------------------------------------------------------------ #

    def write_bars(
        self,
        frame: pd.DataFrame,
        *,
        source: str,
        contract: Contract,
        interval: str,
        report: QualityReport | None = None,
    ) -> list[WriteReceipt]:
        """Write bars partitioned by calendar month.

        Monthly partitions keep files large enough for efficient columnar reads
        while letting a backfill of one month be re-run without rewriting years.
        """
        return self._write_partitioned(
            frame,
            dataset=Dataset.BARS,
            parts=(source, contract.storage_key, interval),
            partition_by=_month_key,
            report=report,
            key_columns=["ts"],
        )

    def write_ticks(
        self,
        frame: pd.DataFrame,
        *,
        source: str,
        contract: Contract,
        report: QualityReport | None = None,
    ) -> list[WriteReceipt]:
        return self._write_partitioned(
            frame,
            dataset=Dataset.TICKS,
            parts=(source, contract.storage_key),
            partition_by=_day_key,
            report=report,
            key_columns=["ts", "sequence"],
        )

    def write_option_chain(
        self,
        frame: pd.DataFrame,
        *,
        source: str,
        underlying: str,
        expiry: date,
        report: QualityReport | None = None,
    ) -> list[WriteReceipt]:
        return self._write_partitioned(
            frame,
            dataset=Dataset.OPTION_CHAIN,
            parts=(source, underlying.upper(), expiry.isoformat()),
            partition_by=_day_key,
            report=report,
            key_columns=["ts", "strike", "right"],
        )

    def write_masters(
        self, frame: pd.DataFrame, *, source: str, segment: Segment
    ) -> list[WriteReceipt]:
        return self._write_partitioned(
            frame,
            dataset=Dataset.MASTERS,
            parts=(source, segment.value),
            partition_by=lambda f: _day_key(f, column="as_of"),
            report=None,
            key_columns=["contract_id"],
            timestamp_column="as_of",
        )

    def write_table(
        self,
        frame: pd.DataFrame,
        *,
        dataset: Dataset,
        parts: Sequence[str],
        stem: str,
        report: QualityReport | None = None,
    ) -> WriteReceipt:
        """Escape hatch for reference tables that need no time partitioning."""
        directory = self.partition_dir(dataset, *parts)
        return self._write_version(frame, directory, stem, dataset=dataset, report=report)

    def quarantine(
        self,
        frame: pd.DataFrame,
        *,
        dataset: Dataset,
        parts: Sequence[str],
        reason: str,
        report: QualityReport | None = None,
    ) -> WriteReceipt | None:
        if frame.empty:
            return None
        directory = self.quarantine_dir(dataset, *parts)
        stamped = frame.copy()
        stamped["quarantine_reason"] = reason
        stamped["quarantined_at"] = datetime.now(tz=timezone.utc)
        stem = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        return self._write_version(
            stamped,
            directory,
            stem,
            dataset=dataset,
            report=report,
            quarantined=True,
            reason=reason,
        )

    # -- reads ------------------------------------------------------------- #

    def read_bars(
        self,
        *,
        source: str,
        contract: Contract | str,
        interval: str,
        start: pd.Timestamp | str | None = None,
        end: pd.Timestamp | str | None = None,
    ) -> pd.DataFrame:
        key = contract if isinstance(contract, str) else contract.storage_key
        directory = self.partition_dir(Dataset.BARS, source, key, interval)
        frame = self._read_dir(directory, key_columns=["ts"])
        return _slice(frame, "ts", start, end)

    def read_ticks(
        self,
        *,
        source: str,
        contract: Contract | str,
        start: pd.Timestamp | str | None = None,
        end: pd.Timestamp | str | None = None,
    ) -> pd.DataFrame:
        key = contract if isinstance(contract, str) else contract.storage_key
        directory = self.partition_dir(Dataset.TICKS, source, key)
        frame = self._read_dir(directory, key_columns=["ts", "sequence"])
        return _slice(frame, "ts", start, end)

    def read_option_chain(
        self,
        *,
        source: str,
        underlying: str,
        expiry: date,
        start: pd.Timestamp | str | None = None,
        end: pd.Timestamp | str | None = None,
    ) -> pd.DataFrame:
        directory = self.partition_dir(
            Dataset.OPTION_CHAIN, source, underlying.upper(), expiry.isoformat()
        )
        frame = self._read_dir(directory, key_columns=["ts", "strike", "right"])
        return _slice(frame, "ts", start, end)

    def read_masters(self, *, source: str, segment: Segment) -> pd.DataFrame:
        directory = self.partition_dir(Dataset.MASTERS, source, segment.value)
        return self._read_dir(directory, key_columns=["contract_id"])

    def latest_ts(
        self, *, dataset: Dataset, parts: Sequence[str], column: str = "ts"
    ) -> pd.Timestamp | None:
        """Newest stored timestamp, for resuming an incremental backfill."""
        directory = self.partition_dir(dataset, *parts)
        manifest = _read_manifest(directory)
        stamps = [entry.get("last_ts") for entry in manifest.get("files", []) if entry.get("last_ts")]
        if stamps:
            return max(pd.Timestamp(s) for s in stamps)
        frame = self._read_dir(directory, key_columns=[column])
        if frame.empty or column not in frame.columns:
            return None
        return pd.Timestamp(frame[column].max())

    def coverage(self, dataset: Dataset) -> pd.DataFrame:
        """One row per partition: rows, window, verdict. The ingest dashboard."""
        rows: list[dict[str, object]] = []
        base = self.root / dataset.value
        if not base.exists():
            return pd.DataFrame(
                columns=["partition", "files", "rows", "first_ts", "last_ts", "verdicts"]
            )
        for manifest_path in sorted(base.rglob("_manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest.get("files", [])
            stamps = [f.get("first_ts") for f in files if f.get("first_ts")]
            ends = [f.get("last_ts") for f in files if f.get("last_ts")]
            rows.append(
                {
                    "partition": str(manifest_path.parent.relative_to(base)),
                    "files": len(files),
                    "rows": sum(int(f.get("rows", 0)) for f in files),
                    "first_ts": min(stamps) if stamps else None,
                    "last_ts": max(ends) if ends else None,
                    "verdicts": sorted({str(f.get("verdict")) for f in files}),
                }
            )
        return pd.DataFrame(rows)

    def ingest_log(self) -> list[dict[str, object]]:
        if not self.ingest_log_path.exists():
            return []
        with self.ingest_log_path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    # -- internals --------------------------------------------------------- #

    def _write_partitioned(
        self,
        frame: pd.DataFrame,
        *,
        dataset: Dataset,
        parts: Sequence[str],
        partition_by,
        report: QualityReport | None,
        key_columns: Sequence[str],
        timestamp_column: str = "ts",
    ) -> list[WriteReceipt]:
        if frame.empty:
            return []

        verdict = report.verdict if report else Verdict.ACCEPT
        if verdict is Verdict.QUARANTINE:
            receipt = self.quarantine(
                frame,
                dataset=dataset,
                parts=parts,
                reason="; ".join(report.errors) if report else "quality gate failed",
                report=report,
            )
            return [receipt] if receipt else []

        directory = self.partition_dir(dataset, *parts)
        receipts: list[WriteReceipt] = []
        for stem, chunk in partition_by(frame):
            receipts.append(
                self._write_version(
                    chunk.sort_values(list(key_columns)).reset_index(drop=True),
                    directory,
                    stem,
                    dataset=dataset,
                    report=report,
                    timestamp_column=timestamp_column,
                )
            )
        return receipts

    def _write_version(
        self,
        frame: pd.DataFrame,
        directory: Path,
        stem: str,
        *,
        dataset: Dataset,
        report: QualityReport | None,
        quarantined: bool = False,
        reason: str = "",
        timestamp_column: str = "ts",
    ) -> WriteReceipt:
        directory.mkdir(parents=True, exist_ok=True)
        version = self._next_version(directory, stem)
        path = directory / f"{stem}__v{version}.parquet"

        tmp = path.with_suffix(".parquet.tmp")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)

        digest = _sha256(path)
        first_ts, last_ts = _window(frame, timestamp_column)
        receipt = WriteReceipt(
            dataset=dataset,
            path=path,
            rows=len(frame),
            version=version,
            verdict=report.verdict if report else Verdict.ACCEPT,
            content_sha256=digest,
            first_ts=first_ts,
            last_ts=last_ts,
            quarantined=quarantined,
        )
        _update_manifest(directory, receipt, report=report, reason=reason)
        self._append_ingest_log(receipt, report=report, reason=reason)
        return receipt

    def _next_version(self, directory: Path, stem: str) -> int:
        highest = 0
        for existing in directory.glob(f"{stem}__v*.parquet"):
            match = _VERSION_RE.match(existing.name)
            if match and match["stem"] == stem:
                highest = max(highest, int(match["version"]))
        return highest + 1

    def _read_dir(self, directory: Path, *, key_columns: Sequence[str]) -> pd.DataFrame:
        if not directory.exists():
            return pd.DataFrame()
        files = sorted(directory.glob("*.parquet"), key=_version_sort_key)
        if not files:
            return pd.DataFrame()
        frames = [pd.read_parquet(path) for path in files]
        frame = pd.concat(frames, ignore_index=True)
        usable_keys = [c for c in key_columns if c in frame.columns]
        if usable_keys:
            frame = frame.drop_duplicates(subset=usable_keys, keep="last")
            frame = frame.sort_values(usable_keys)
        return frame.reset_index(drop=True)

    def _append_ingest_log(
        self, receipt: WriteReceipt, *, report: QualityReport | None, reason: str
    ) -> None:
        record = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "schema_version": SCHEMA_VERSION,
            **receipt.as_dict(),
            "reason": reason,
            "quality": report.as_dict() if report else None,
        }
        with self.ingest_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _month_key(frame: pd.DataFrame):
    stamps = pd.to_datetime(frame["ts"])
    for key, chunk in frame.groupby(stamps.dt.strftime("%Y-%m"), sort=True):
        yield str(key), chunk


def _day_key(frame: pd.DataFrame, column: str = "ts"):
    stamps = pd.to_datetime(frame[column])
    for key, chunk in frame.groupby(stamps.dt.strftime("%Y-%m-%d"), sort=True):
        yield str(key), chunk


def _version_sort_key(path: Path) -> tuple[str, int]:
    match = _VERSION_RE.match(path.name)
    if match:
        return match["stem"], int(match["version"])
    return path.stem, 0


def _read_manifest(directory: Path) -> dict[str, object]:
    path = directory / "_manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _update_manifest(
    directory: Path, receipt: WriteReceipt, *, report: QualityReport | None, reason: str
) -> None:
    path = directory / "_manifest.json"
    manifest = _read_manifest(directory)
    files = list(manifest.get("files", []))
    files.append(
        {
            "file": receipt.path.name,
            "rows": receipt.rows,
            "version": receipt.version,
            "verdict": receipt.verdict.value,
            "content_sha256": receipt.content_sha256,
            "first_ts": receipt.first_ts,
            "last_ts": receipt.last_ts,
            "written_at": datetime.now(tz=timezone.utc).isoformat(),
            "reason": reason,
            "quality": report.as_dict() if report else None,
        }
    )
    payload = {
        "dataset": receipt.dataset.value,
        "schema_version": SCHEMA_VERSION,
        "files": files,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, default=str, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _window(frame: pd.DataFrame, column: str) -> tuple[str | None, str | None]:
    if column not in frame.columns or frame.empty:
        return None, None
    stamps = pd.to_datetime(frame[column], errors="coerce").dropna()
    if stamps.empty:
        return None, None
    return str(stamps.min()), str(stamps.max())


def _slice(
    frame: pd.DataFrame,
    column: str,
    start: pd.Timestamp | str | None,
    end: pd.Timestamp | str | None,
) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return frame
    stamps = pd.to_datetime(frame[column])
    if start is not None:
        frame = frame[stamps >= _align(pd.Timestamp(start), stamps)]
        stamps = pd.to_datetime(frame[column])
    if end is not None:
        frame = frame[stamps <= _align(pd.Timestamp(end), stamps)]
    return frame.reset_index(drop=True)


def _align(stamp: pd.Timestamp, series: pd.Series) -> pd.Timestamp:
    tz = getattr(series.dt, "tz", None)
    if tz is None:
        return stamp.tz_localize(None) if stamp.tzinfo else stamp
    return stamp.tz_localize(tz) if stamp.tzinfo is None else stamp.tz_convert(tz)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe(part: str) -> str:
    return _SAFE.sub("-", str(part)).strip("-") or "unknown"
