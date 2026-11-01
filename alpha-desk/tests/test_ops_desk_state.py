"""Ops desk state / progress (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from ops_desk_state import (  # noqa: E402
    append_journal,
    load_progress,
    progress_path,
    record_paper_day,
    status_path,
)


def test_journal_and_paper_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("ops_desk_state.OPS", tmp_path)
    append_journal("live", "info", "capturing started")
    append_journal("live", "error", "boom", fix="you")
    p = record_paper_day(ok=True, score=0.42, detail="ok", signal="FLAT")
    assert p["paper_days_ok"] == 1
    assert p["est_days_to_live"] == 19
    assert progress_path().exists()
    assert status_path().exists()
    assert load_progress()["last_paper"]["score"] == 0.42
