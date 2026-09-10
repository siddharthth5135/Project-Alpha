"""Append one ops heartbeat line (JSONL). Used by scheduled jobs."""

from __future__ import annotations

import argparse
import json
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

    if args.status == "fail":
        fail = root / f"FAIL_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.job}.txt"
        fail.write_text(json.dumps(row, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
