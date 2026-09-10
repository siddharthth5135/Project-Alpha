"""Human promote / reject decision for a trial (P1.3.3).

    python scripts/decide_trial.py --trial-id Txxx --decision REJECT --by "Name" --reason "weak IC"
    python scripts/decide_trial.py --trial-id Txxx --decision PROMOTE_TO_PAPER --by "Name" --reason "..."

Rejected trials stay forever. Promotion to paper is blocked for plumbing sources
unless --force-plumbing (still not live).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.models.artifacts import PLUMBING_SOURCES  # noqa: E402
from alpha_desk.registry.trial_registry import TrialRegistry, TrialStatus  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Record promote/reject on a trial")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument(
        "--decision",
        required=True,
        choices=["REJECT", "REJECTED", "PROMOTE_TO_PAPER", "PROMOTED_TO_PAPER", "RETIRED"],
    )
    parser.add_argument("--by", required=True, help="your name")
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--force-plumbing",
        action="store_true",
        help="allow PROMOTE_TO_PAPER on synthetic/yahoo (still not live; research only)",
    )
    args = parser.parse_args()

    settings = load_settings()
    if settings.live_enabled:
        print("REFUSING: live mode is on")
        return 2

    decision_map = {
        "REJECT": TrialStatus.REJECTED,
        "REJECTED": TrialStatus.REJECTED,
        "PROMOTE_TO_PAPER": TrialStatus.PROMOTED_TO_PAPER,
        "PROMOTED_TO_PAPER": TrialStatus.PROMOTED_TO_PAPER,
        "RETIRED": TrialStatus.RETIRED,
    }
    status = decision_map[args.decision]
    with TrialRegistry(settings.data_root / "registry" / "trials.sqlite") as registry:
        trial = registry.get(args.trial_id)
        snap = registry.config_snapshot(args.trial_id)
        source = str(snap.get("source", ""))
        if not source:
            for row in registry.metrics_for(args.trial_id):
                metrics = row["metrics"]
                if isinstance(metrics, dict) and metrics.get("source"):
                    source = str(metrics["source"])
                    break
                if isinstance(metrics, dict):
                    for value in metrics.values():
                        if isinstance(value, dict) and value.get("source"):
                            source = str(value["source"])
                            break
                if source:
                    break

        if (
            status is TrialStatus.PROMOTED_TO_PAPER
            and source in PLUMBING_SOURCES
            and not args.force_plumbing
        ):
            print(
                f"REFUSING PROMOTE_TO_PAPER: trial source={source!r} is plumbing. "
                "Wait for TrueData futures data, or pass --force-plumbing for a "
                "research-only paper dry-run (still not live)."
            )
            return 3

        registry.decide(
            args.trial_id,
            status=status,
            decided_by=args.by,
            rationale=args.reason,
        )
        updated = registry.get(args.trial_id)
        print(
            json.dumps(
                {
                    "trial_id": updated.trial_id,
                    "was": trial.status.value,
                    "status": updated.status.value,
                    "source": source,
                    "decided_by": args.by,
                    "reason": args.reason,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
