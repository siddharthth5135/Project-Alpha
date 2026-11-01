"""Append one ops heartbeat line (JSONL). Used by scheduled jobs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, help="job name, e.g. preopen|live|eod|train")
    parser.add_argument("--status", required=True, choices=("start", "ok", "fail"))
    parser.add_argument("--detail", default="")
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()

    root = REPO / "logs" / "ops"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "heartbeat.jsonl"
    row = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "job": args.job,
        "status": args.status,
        "exit_code": args.exit_code,
        "detail": args.detail[:500],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True) + "\n")

    # Mirror into developer journal (console history)
    py = REPO / ".venv" / "Scripts" / "python.exe"
    journal = REPO / "scripts" / "ops_journal.py"
    if py.exists() and journal.exists():
        level = {"start": "info", "ok": "ok", "fail": "error"}[args.status]
        msg = f"heartbeat {args.job}={args.status} exit={args.exit_code} {args.detail}"[:500]
        fix = "you" if args.status == "fail" else "none"
        subprocess.run(
            [
                str(py),
                str(journal),
                "--phase",
                args.job,
                "--level",
                level,
                "--message",
                msg,
                "--fix",
                fix,
            ],
            cwd=str(REPO),
            check=False,
            capture_output=True,
        )

    if args.status == "fail":
        fail = root / f"FAIL_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.job}.txt"
        fail.write_text(json.dumps(row, indent=2), encoding="utf-8")
        notify = REPO / "scripts" / "ops_notify.py"
        if py.exists() and notify.exists():
            subprocess.run(
                [
                    str(py),
                    str(notify),
                    "--level",
                    "error",
                    "--title",
                    f"AlphaDesk {args.job} FAIL",
                    "--body",
                    f"exit={args.exit_code} {args.detail}"[:500],
                    "--phase",
                    args.job,
                    "--fix",
                    "you",
                ],
                cwd=str(REPO),
                check=False,
                capture_output=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
