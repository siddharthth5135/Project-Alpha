"""Gate G2/G3 checklist — models + validation honesty.

    python scripts/verify_g3.py --source synthetic
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from alpha_desk.config.settings import load_settings, load_strategy_spec  # noqa: E402
from alpha_desk.models.artifacts import ArtifactStore, PLUMBING_SOURCES  # noqa: E402
from alpha_desk.validation.walkforward import (  # noqa: E402
    PromotePathError,
    assert_promote_scheme_allowed,
)
from verify_foundation import Check  # noqa: E402


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="synthetic")
    args = parser.parse_args()

    print("\nProject Alpha — Gate G2/G3 model + validation checks")
    print("=" * 70)
    settings = load_settings()
    spec = load_strategy_spec()
    checks = Check()

    store = ArtifactStore(settings.artifact_root / "models")
    for kind in ("ridge_alpha", "lgbm_alpha", "loss_hit_stop"):
        latest = store.latest(kind)
        checks.add(
            f"artifact exists: {kind}",
            latest is not None,
            latest[1].model_id if latest else "missing — run train_models.py",
        )
        if latest:
            _, meta = latest
            checks.add(
                f"{kind} promote stamp honest",
                (not meta.promote_eligible) if args.source in PLUMBING_SOURCES else True,
                f"promote_eligible={meta.promote_eligible} source={meta.source}",
            )

    checks.add(
        "strategy scheme is walk_forward_purged",
        "walk_forward" in spec.validation.scheme,
        spec.validation.scheme,
    )
    try:
        assert_promote_scheme_allowed("random_kfold", for_promote=True)
        ok, detail = False, "random_kfold was allowed — FAIL"
    except PromotePathError as exc:
        ok, detail = True, str(exc)[:80]
    checks.add("random k-fold promote fails closed", ok, detail)

    report = settings.artifact_root / "validation" / f"summary_{args.source}.json"
    checks.add(
        "walk-forward report present",
        report.exists(),
        str(report) if report.exists() else "run validate_models.py",
        mandatory=False,
    )
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        checks.add("report is non-empty", bool(data), f"keys={list(data)}")

    checks.add(
        "live trading OFF",
        not settings.live_enabled,
        f"mode={settings.mode.value}",
    )

    print(checks.render())
    print("=" * 70)
    if checks.mandatory_failures:
        print(f"RESULT: {checks.mandatory_failures} mandatory check(s) FAILED.\n")
        return 1
    print("RESULT: G2/G3 code checks PASS (scaffolding).")
    if args.source in PLUMBING_SOURCES:
        print("CAVEAT: plumbing source — not research-honesty sign-off for promote.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
