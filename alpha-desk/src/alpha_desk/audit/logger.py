"""Append-only audit log with a hash chain.

Every signal, draft, loss advisory, risk verdict, order and fill lands here as
one JSON line. Each record carries the SHA-256 of the previous one, so a
deleted or edited line breaks verification. The point is not cryptographic
security against a determined attacker; it is that "the log says we never sent
that order" becomes a checkable claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

GENESIS_HASH = "0" * 64


class AuditEvent(str, Enum):
    RUN_START = "RUN_START"
    HEARTBEAT = "HEARTBEAT"
    SIGNAL = "SIGNAL"
    DRAFT = "DRAFT"
    LOSS_ADVICE = "LOSS_ADVICE"
    RISK_DECISION = "RISK_DECISION"
    ORDER_SENT = "ORDER_SENT"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    HALT = "HALT"
    KILL_SWITCH = "KILL_SWITCH"
    FLATTEN = "FLATTEN"
    CONFIG_LOADED = "CONFIG_LOADED"
    TRIAL = "TRIAL"
    MARKET_DATA = "MARKET_DATA"
    FEED_STATE = "FEED_STATE"
    ERROR = "ERROR"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: str
    run_id: str
    event: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


class AuditLog:
    def __init__(self, log_dir: Path, run_id: str | None = None, session: date | None = None):
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        day = (session or datetime.now(timezone.utc).date()).isoformat()
        self._path = self._dir / f"audit-{day}.jsonl"
        self._run_id = run_id or uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self._seq, self._last_hash = self._resume()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def run_id(self) -> str:
        return self._run_id

    def write(self, event: AuditEvent, **payload: Any) -> AuditRecord:
        with self._lock:
            seq = self._seq + 1
            body = {
                "seq": seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "run_id": self._run_id,
                "event": event.value,
                "payload": payload,
                "prev_hash": self._last_hash,
            }
            digest = self._digest(body)
            record = {**body, "hash": digest}
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=_json_default, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._seq, self._last_hash = seq, digest
            return AuditRecord(**record)

    def read_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        with self._path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def verify_chain(self) -> tuple[bool, str]:
        """Return (ok, message). Any gap or edit breaks the chain."""
        prev = GENESIS_HASH
        for i, record in enumerate(self.read_all(), start=1):
            if record["seq"] != i:
                return False, f"sequence gap at record {i}: found seq={record['seq']}"
            if record["prev_hash"] != prev:
                return False, f"prev_hash mismatch at seq {i}"
            body = {k: v for k, v in record.items() if k != "hash"}
            if self._digest(body) != record["hash"]:
                return False, f"content hash mismatch at seq {i}"
            prev = record["hash"]
        return True, "chain intact"

    def _resume(self) -> tuple[int, str]:
        records = self.read_all()
        if not records:
            return 0, GENESIS_HASH
        last = records[-1]
        return last["seq"], last["hash"]

    @staticmethod
    def _digest(body: dict[str, Any]) -> str:
        blob = json.dumps(body, default=_json_default, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()
