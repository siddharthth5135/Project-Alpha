"""Shared ops desk state: journal, status snapshot, paper-progress gates.

Files under ``logs/ops/`` (gitignored):
  journal.jsonl       - append-only event log (console history)
  desk_status.json    - latest snapshot for the console header
  progress.json       - paper days / gates / go-live countdown
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
REPO = Path(__file__).resolve().parents[1]
OPS = REPO / "logs" / "ops"

# MVP go-live sample (matches prior plan): 20 good paper days before tiny live.
TARGET_PAPER_DAYS = 20
PAPER_START_KEY = "paper_campaign_start"


def ops_dir() -> Path:
    OPS.mkdir(parents=True, exist_ok=True)
    return OPS


def journal_path() -> Path:
    return ops_dir() / "journal.jsonl"


def status_path() -> Path:
    return ops_dir() / "desk_status.json"


def progress_path() -> Path:
    return ops_dir() / "progress.json"


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def append_journal(
    phase: str,
    level: str,
    message: str,
    *,
    fix: str = "none",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one console-visible event.

    ``fix`` is ``auto`` | ``you`` | ``none`` — tells the developer who acts.
    ``level`` is ``info`` | ``ok`` | ``warn`` | ``error``.
    """
    row: dict[str, Any] = {
        "ts": now_ist().isoformat(),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "level": level.lower(),
        "message": message[:800],
        "fix": fix,
    }
    if extra:
        row["extra"] = extra
    path = journal_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    _update_status_from_event(row)
    return row


def _update_status_from_event(row: dict[str, Any]) -> None:
    status = load_status()
    status["updated_at"] = row["ts"]
    status["last_phase"] = row["phase"]
    status["last_level"] = row["level"]
    status["last_message"] = row["message"]
    status["last_fix"] = row.get("fix", "none")
    if row["level"] == "error":
        status["open_errors"] = int(status.get("open_errors", 0)) + 1
        status["needs_attention"] = True
    elif row["level"] == "ok" and row["phase"] in {"day_close", "train", "paper"}:
        # Do not clear all errors automatically; day_close report can.
        pass
    save_status(status)


def load_status() -> dict[str, Any]:
    path = status_path()
    if not path.exists():
        return {
            "updated_at": None,
            "last_phase": "idle",
            "last_level": "info",
            "last_message": "desk idle - open console and wait for scheduled jobs",
            "last_fix": "none",
            "open_errors": 0,
            "needs_attention": False,
            "angel_allow_marketdata": _angel_allow(),
            "mode": os.environ.get("ALPHA_MODE", "paper"),
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"updated_at": None, "last_phase": "corrupt_status", "last_level": "error"}


def save_status(status: dict[str, Any]) -> None:
    status["angel_allow_marketdata"] = _angel_allow()
    status_path().write_text(json.dumps(status, indent=2), encoding="utf-8")


def load_progress() -> dict[str, Any]:
    path = progress_path()
    if not path.exists():
        today = date.today().isoformat()
        return {
            PAPER_START_KEY: today,
            "target_paper_days": TARGET_PAPER_DAYS,
            "paper_days_ok": 0,
            "paper_days_fail": 0,
            "paper_day_log": [],
            "last_train": None,
            "last_paper_score": None,
            "gates": {
                "G1_feed": False,
                "G2_ops_stable": False,
                "G3_edge_after_costs": False,
                "G4_size_1lot": False,
                "G5_live_go": False,
            },
            "go_live_ready": False,
            "est_days_to_live": TARGET_PAPER_DAYS,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_progress(progress: dict[str, Any]) -> None:
    progress_path().write_text(json.dumps(progress, indent=2), encoding="utf-8")


def record_paper_day(
    *,
    ok: bool,
    score: float | None,
    detail: str,
    signal: str | None = None,
) -> dict[str, Any]:
    progress = load_progress()
    day = date.today().isoformat()
    entry = {
        "date": day,
        "ok": ok,
        "score": score,
        "signal": signal,
        "detail": detail[:400],
    }
    log = [e for e in progress.get("paper_day_log", []) if e.get("date") != day]
    log.append(entry)
    progress["paper_day_log"] = log[-120:]
    progress["paper_days_ok"] = sum(1 for e in progress["paper_day_log"] if e.get("ok"))
    progress["paper_days_fail"] = sum(1 for e in progress["paper_day_log"] if not e.get("ok"))
    progress["last_paper_score"] = score
    progress["last_paper"] = entry

    ok_days = progress["paper_days_ok"]
    target = int(progress.get("target_paper_days", TARGET_PAPER_DAYS))
    progress["est_days_to_live"] = max(0, target - ok_days)

    gates = progress.setdefault("gates", {})
    gates["G1_feed"] = _angel_allow() and ok_days >= 1
    gates["G2_ops_stable"] = ok_days >= min(5, target)
    # G3 stays false until human reviews edge — never auto-green.
    gates["G4_size_1lot"] = False  # set only at live enable
    gates["G5_live_go"] = False
    progress["go_live_ready"] = (
        ok_days >= target and gates.get("G1_feed") and gates.get("G2_ops_stable")
    )
    # Still need human G3 review + G5 signature — ready means "ask HoQR", not auto-live.
    save_progress(progress)
    return progress


def record_train(detail: str, *, ok: bool) -> dict[str, Any]:
    progress = load_progress()
    progress["last_train"] = {
        "ts": now_ist().isoformat(),
        "ok": ok,
        "detail": detail[:400],
    }
    save_progress(progress)
    return progress


def read_journal(limit: int = 200) -> list[dict[str, Any]]:
    path = journal_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def clear_attention() -> None:
    status = load_status()
    status["needs_attention"] = False
    status["open_errors"] = 0
    save_status(status)


def _angel_allow() -> bool:
    env_path = REPO / ".env"
    if not env_path.exists():
        return False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("ANGELONE_ALLOW_MARKETDATA"):
            val = line.split("=", 1)[-1].strip().strip('"').lower()
            return val in {"1", "true", "yes", "on"}
    return False


def progress_summary_lines() -> list[str]:
    p = load_progress()
    target = int(p.get("target_paper_days", TARGET_PAPER_DAYS))
    ok = int(p.get("paper_days_ok", 0))
    fail = int(p.get("paper_days_fail", 0))
    left = int(p.get("est_days_to_live", target))
    gates = p.get("gates", {})
    lines = [
        f"Paper campaign start : {p.get(PAPER_START_KEY)}",
        f"Good paper days      : {ok}/{target}  (fail days: {fail})",
        f"Est. days to ask live: {left}  (then 1-lot only; still needs your GO)",
        f"Gates G1 feed        : {'PASS' if gates.get('G1_feed') else 'WAIT'}",
        f"Gates G2 ops stable  : {'PASS' if gates.get('G2_ops_stable') else 'WAIT'}",
        f"Gates G3 edge review : {'PASS' if gates.get('G3_edge_after_costs') else 'YOU must review'}",
        f"Gates G5 live GO     : {'PASS' if gates.get('G5_live_go') else 'BLOCKED (correct)'}",
        f"Ask-live checklist   : {'YES — ping HoQR' if p.get('go_live_ready') else 'NOT YET'}",
    ]
    last = p.get("last_paper")
    if last:
        lines.append(
            f"Last paper           : {last.get('date')} ok={last.get('ok')} "
            f"score={last.get('score')} signal={last.get('signal')}"
        )
    train = p.get("last_train")
    if train:
        lines.append(f"Last train           : {train.get('ts')} ok={train.get('ok')}")
    return lines
