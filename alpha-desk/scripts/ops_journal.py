"""Append a desk journal line (called from ops_daily / jobs).

    python scripts/ops_journal.py --phase preopen --level info --message "Pre-market checks starting"
    python scripts/ops_journal.py --phase live --level error --message "capture failed" --fix you
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from ops_desk_state import append_journal  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", required=True)
    p.add_argument("--level", default="info", choices=("info", "ok", "warn", "error"))
    p.add_argument("--message", required=True)
    p.add_argument("--fix", default="none", choices=("none", "auto", "you"))
    p.add_argument("--extra-json", default="")
    args = p.parse_args()
    extra = json.loads(args.extra_json) if args.extra_json.strip() else None
    row = append_journal(args.phase, args.level, args.message, fix=args.fix, extra=extra)
    print(json.dumps(row, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
