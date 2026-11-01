"""End-of-day paper path + progress update (no live orders).

    python scripts/ao_paper_day.py
    python scripts/ao_paper_day.py --source angelone --symbol NIFTY
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

from ops_desk_state import append_journal, record_paper_day  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default="angelone")
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--bars", type=int, default=250)
    args = p.parse_args()

    append_journal("paper", "info", f"Paper session starting source={args.source} symbol={args.symbol}")

    cmd = [
        str(REPO / ".venv" / "Scripts" / "python.exe"),
        str(REPO / "scripts" / "run_paper_path.py"),
        "--source",
        args.source,
        "--symbol",
        args.symbol,
        "--bars",
        str(args.bars),
    ]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    # Parse a few fields if present
    score = None
    signal = None
    for line in out.splitlines():
        if "alpha_score" in line and ":" in line:
            try:
                score = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        if "signal" in line and ":" in line and "alpha" not in line:
            signal = line.split(":", 1)[1].strip()

    ok = proc.returncode == 0
    detail = out[-500:] if out else f"exit={proc.returncode}"
    progress = record_paper_day(ok=ok, score=score, detail=detail, signal=signal)

    if ok:
        append_journal(
            "paper",
            "ok",
            f"Paper done signal={signal} score={score} "
            f"good_days={progress['paper_days_ok']}/{progress['target_paper_days']} "
            f"days_to_ask_live≈{progress['est_days_to_live']}",
            fix="none",
            extra={"signal": signal, "score": score},
        )
    else:
        append_journal(
            "paper",
            "error",
            f"Paper FAILED exit={proc.returncode}. If no bars yet: enable Angel + capture first.",
            fix="you",
        )
        print(out[-2000:])
        return 1

    print(json.dumps({"ok": True, "progress": {
        "paper_days_ok": progress["paper_days_ok"],
        "est_days_to_live": progress["est_days_to_live"],
        "go_live_ready": progress["go_live_ready"],
    }}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
