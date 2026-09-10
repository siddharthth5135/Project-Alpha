"""Trial registry - the desk's memory of every hypothesis, including failures.

Rejected trials are the health metric: a registry containing only winners
means someone is quietly deleting evidence. SQLite triggers enforce that
nothing is ever deleted and that a terminal decision cannot be rewritten.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    trial_id          TEXT PRIMARY KEY,
    strategy_id       TEXT NOT NULL,
    hypothesis        TEXT NOT NULL,
    status            TEXT NOT NULL,
    feature_set_hash  TEXT NOT NULL DEFAULT '',
    label_def_id      TEXT NOT NULL DEFAULT '',
    config_snapshot   TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trial_metrics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id   TEXT NOT NULL REFERENCES trials(trial_id),
    stage      TEXT NOT NULL,
    metrics    TEXT NOT NULL,
    costs_verified INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trial_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id    TEXT NOT NULL REFERENCES trials(trial_id),
    decision    TEXT NOT NULL,
    decided_by  TEXT NOT NULL,
    rationale   TEXT NOT NULL,
    decided_at  TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trials_no_delete
BEFORE DELETE ON trials
BEGIN
    SELECT RAISE(ABORT, 'trials are append-only: rejected trials are evidence');
END;

CREATE TRIGGER IF NOT EXISTS metrics_no_delete
BEFORE DELETE ON trial_metrics
BEGIN
    SELECT RAISE(ABORT, 'trial metrics are append-only');
END;

CREATE TRIGGER IF NOT EXISTS decisions_no_delete
BEFORE DELETE ON trial_decisions
BEGIN
    SELECT RAISE(ABORT, 'trial decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trials_no_resurrect
BEFORE UPDATE OF status ON trials
WHEN OLD.status IN ('REJECTED', 'RETIRED') AND NEW.status NOT IN ('REJECTED', 'RETIRED')
BEGIN
    SELECT RAISE(ABORT, 'a rejected trial cannot be revived: open a new trial_id');
END;
"""


class TrialStatus(str, Enum):
    PROPOSED = "PROPOSED"
    EVAL = "EVAL"
    REJECTED = "REJECTED"
    PROMOTED_TO_PAPER = "PROMOTED_TO_PAPER"
    PROMOTED_TO_LIVE = "PROMOTED_TO_LIVE"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class Trial:
    trial_id: str
    strategy_id: str
    hypothesis: str
    status: TrialStatus
    feature_set_hash: str
    label_def_id: str
    created_at: str
    updated_at: str


class TrialRegistry:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TrialRegistry":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #

    def create_trial(
        self,
        *,
        strategy_id: str,
        hypothesis: str,
        feature_set_hash: str = "",
        label_def_id: str = "",
        config_snapshot: dict[str, Any] | None = None,
        trial_id: str | None = None,
    ) -> str:
        tid = trial_id or f"T{uuid.uuid4().hex[:10]}"
        now = _now()
        self._conn.execute(
            "INSERT INTO trials (trial_id, strategy_id, hypothesis, status, feature_set_hash,"
            " label_def_id, config_snapshot, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                tid,
                strategy_id,
                hypothesis,
                TrialStatus.PROPOSED.value,
                feature_set_hash,
                label_def_id,
                json.dumps(config_snapshot or {}, sort_keys=True),
                now,
                now,
            ),
        )
        self._conn.commit()
        return tid

    def record_metrics(
        self,
        trial_id: str,
        stage: str,
        metrics: dict[str, Any],
        *,
        costs_verified: bool = False,
    ) -> None:
        self._require_trial(trial_id)
        self._conn.execute(
            "INSERT INTO trial_metrics (trial_id, stage, metrics, costs_verified, recorded_at)"
            " VALUES (?,?,?,?,?)",
            (trial_id, stage, json.dumps(metrics, sort_keys=True), int(costs_verified), _now()),
        )
        self._conn.commit()

    def decide(
        self,
        trial_id: str,
        *,
        status: TrialStatus,
        decided_by: str,
        rationale: str,
    ) -> None:
        """Record a decision. Promotions require a named human and a rationale."""
        self._require_trial(trial_id)
        promoting = status in (TrialStatus.PROMOTED_TO_PAPER, TrialStatus.PROMOTED_TO_LIVE)
        if promoting and (not decided_by.strip() or not rationale.strip()):
            raise ValueError("promotion requires decided_by and a written rationale")

        self._conn.execute(
            "INSERT INTO trial_decisions (trial_id, decision, decided_by, rationale, decided_at)"
            " VALUES (?,?,?,?,?)",
            (trial_id, status.value, decided_by, rationale, _now()),
        )
        self._conn.execute(
            "UPDATE trials SET status = ?, updated_at = ? WHERE trial_id = ?",
            (status.value, _now(), trial_id),
        )
        self._conn.commit()

    def get(self, trial_id: str) -> Trial:
        row = self._conn.execute(
            "SELECT * FROM trials WHERE trial_id = ?", (trial_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown trial_id {trial_id!r}")
        return Trial(
            trial_id=row["trial_id"],
            strategy_id=row["strategy_id"],
            hypothesis=row["hypothesis"],
            status=TrialStatus(row["status"]),
            feature_set_hash=row["feature_set_hash"],
            label_def_id=row["label_def_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_trials(self, status: TrialStatus | None = None) -> list[Trial]:
        if status is None:
            rows = self._conn.execute("SELECT trial_id FROM trials ORDER BY created_at").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT trial_id FROM trials WHERE status = ? ORDER BY created_at",
                (status.value,),
            ).fetchall()
        return [self.get(r["trial_id"]) for r in rows]

    def config_snapshot(self, trial_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT config_snapshot FROM trials WHERE trial_id = ?", (trial_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown trial_id {trial_id!r}")
        return json.loads(row["config_snapshot"] or "{}")

    def metrics_for(self, trial_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT stage, metrics, costs_verified, recorded_at FROM trial_metrics"
            " WHERE trial_id = ? ORDER BY id",
            (trial_id,),
        ).fetchall()
        return [
            {
                "stage": r["stage"],
                "metrics": json.loads(r["metrics"]),
                "costs_verified": bool(r["costs_verified"]),
                "recorded_at": r["recorded_at"],
            }
            for r in rows
        ]

    def rejection_rate(self) -> float:
        total = self._conn.execute("SELECT COUNT(*) c FROM trials").fetchone()["c"]
        if total == 0:
            return 0.0
        rejected = self._conn.execute(
            "SELECT COUNT(*) c FROM trials WHERE status = ?", (TrialStatus.REJECTED.value,)
        ).fetchone()["c"]
        return rejected / total

    def _require_trial(self, trial_id: str) -> None:
        self.get(trial_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
