"""Trial registry and audit log tests (playbook steps P1.0.7 and audit law)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from alpha_desk.audit.logger import AuditEvent, AuditLog
from alpha_desk.registry.trial_registry import TrialRegistry, TrialStatus


@pytest.fixture
def registry(tmp_path):
    with TrialRegistry(tmp_path / "trials.sqlite") as reg:
        yield reg


def _new_trial(registry) -> str:
    return registry.create_trial(
        strategy_id="A1_INDEX_REGIME_V0",
        hypothesis="15m regime-conditional mean reversion on index futures",
        feature_set_hash="abc123",
        label_def_id="y_fwd_ret_8",
    )


def test_trial_starts_as_proposed(registry):
    tid = _new_trial(registry)
    assert registry.get(tid).status is TrialStatus.PROPOSED


def test_metrics_are_recorded_with_cost_verification_flag(registry):
    tid = _new_trial(registry)
    registry.record_metrics(tid, "walk_forward", {"net_sharpe": 0.4}, costs_verified=False)
    rows = registry.metrics_for(tid)
    assert rows[0]["metrics"]["net_sharpe"] == 0.4
    assert rows[0]["costs_verified"] is False


def test_rejected_trials_cannot_be_deleted(registry, tmp_path):
    tid = _new_trial(registry)
    registry.decide(
        tid, status=TrialStatus.REJECTED, decided_by="HoQR", rationale="no OOS edge"
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        registry._conn.execute("DELETE FROM trials WHERE trial_id = ?", (tid,))


def test_rejected_trial_cannot_be_revived(registry):
    tid = _new_trial(registry)
    registry.decide(tid, status=TrialStatus.REJECTED, decided_by="HoQR", rationale="weak")
    with pytest.raises(sqlite3.IntegrityError, match="new trial_id"):
        registry.decide(
            tid,
            status=TrialStatus.PROMOTED_TO_PAPER,
            decided_by="HoQR",
            rationale="second thoughts",
        )


def test_promotion_requires_a_named_human_and_rationale(registry):
    tid = _new_trial(registry)
    with pytest.raises(ValueError, match="rationale"):
        registry.decide(tid, status=TrialStatus.PROMOTED_TO_PAPER, decided_by="", rationale="")


def test_rejection_rate_is_reported(registry):
    keep = _new_trial(registry)
    drop = _new_trial(registry)
    registry.decide(drop, status=TrialStatus.REJECTED, decided_by="HoQR", rationale="weak")
    registry.decide(
        keep, status=TrialStatus.PROMOTED_TO_PAPER, decided_by="HoQR", rationale="honest OOS"
    )
    assert registry.rejection_rate() == pytest.approx(0.5)


def test_unknown_trial_raises(registry):
    with pytest.raises(KeyError):
        registry.get("T-does-not-exist")


# --------------------------------------------------------------------------- #
# audit log
# --------------------------------------------------------------------------- #


def test_audit_chain_verifies(tmp_path):
    log = AuditLog(tmp_path / "audit")
    log.write(AuditEvent.RUN_START, note="one")
    log.write(AuditEvent.HEARTBEAT, note="two")
    ok, message = log.verify_chain()
    assert ok, message


def test_edited_audit_record_breaks_the_chain(tmp_path):
    log = AuditLog(tmp_path / "audit")
    log.write(AuditEvent.SIGNAL, symbol="NIFTY")
    log.write(AuditEvent.ORDER_SENT, symbol="NIFTY", lots=1)

    records = log.read_all()
    records[0]["payload"]["symbol"] = "BANKNIFTY"
    log.path.write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n", encoding="utf-8"
    )

    ok, message = log.verify_chain()
    assert not ok
    assert "hash mismatch" in message


def test_deleted_audit_record_breaks_the_chain(tmp_path):
    log = AuditLog(tmp_path / "audit")
    for i in range(3):
        log.write(AuditEvent.HEARTBEAT, i=i)

    records = log.read_all()
    del records[1]
    log.path.write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n", encoding="utf-8"
    )

    ok, _ = log.verify_chain()
    assert not ok


def test_audit_log_resumes_an_existing_file(tmp_path):
    first = AuditLog(tmp_path / "audit")
    first.write(AuditEvent.RUN_START)
    second = AuditLog(tmp_path / "audit")
    second.write(AuditEvent.RUN_START)
    ok, message = second.verify_chain()
    assert ok, message
    assert len(second.read_all()) == 2
