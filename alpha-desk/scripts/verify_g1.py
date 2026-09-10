"""Gate G1 verification: data and features.

The playbook's G1 exit condition is "no leakage tests pass". That phrase is easy
to satisfy dishonestly, so this script checks three things instead of one:

1. the point-in-time and shift-future tests pass on real warehouse bars,
2. the same attacks *fail* on deliberately leaky features, proving the tests bite,
3. the feature schema is frozen, committed, and matches the code.

    python scripts/verify_g1.py --source synthetic
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import (  # noqa: E402
    CONFIG_DIR,
    load_risk_config,
    load_settings,
    load_strategy_spec,
)
from alpha_desk.data.continuous import (  # noqa: E402
    assert_returns_are_roll_invariant,
    synthetic_contracts,
)
from alpha_desk.data.schema import validate_bars  # noqa: E402
from alpha_desk.data.warehouse import ParquetWarehouse  # noqa: E402
from alpha_desk.datasets.builder import build_supervised_table  # noqa: E402
from alpha_desk.features.catalogue import build_feature_set_v0  # noqa: E402
from alpha_desk.features.pit import (  # noqa: E402
    PointInTimeViolation,
    assert_labels_see_the_future,
    assert_point_in_time,
    shift_future_attack,
)
from alpha_desk.features.registry import FeatureSet, FeatureSpec  # noqa: E402
from alpha_desk.labels.definitions import (  # noqa: E402
    LABEL_COLUMNS,
    build_labels,
    label_definition_from_config,
)

sys.path.insert(0, str(REPO / "scripts"))
from verify_foundation import Check  # noqa: E402

LOCK_DIR = CONFIG_DIR / "feature_locks"


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate G1 verification")
    parser.add_argument("--source", default="synthetic")
    args = parser.parse_args()

    print("\nProject Alpha - Gate G1 data and feature verification")
    print("=" * 70)

    settings = load_settings()
    spec = load_strategy_spec()
    feature_set = build_feature_set_v0()
    label_definition = label_definition_from_config(load_risk_config(), spec)
    warehouse = ParquetWarehouse(settings.data_root)

    symbols = list(spec.universe.underlyings)
    bars = {s: warehouse.read_bars(args.source, s, spec.bars.primary_timeframe) for s in symbols}
    usable = {s: b for s, b in bars.items() if not b.empty}

    checks = Check()

    checks.add(
        "warehouse has bars",
        bool(usable),
        f"source={args.source}: " + ", ".join(f"{s} {len(b)}" for s, b in bars.items()),
    )
    if not usable:
        print(checks.render())
        print("\nRESULT: no data. Run scripts/fetch_data.py first.\n")
        return 1

    def _quality():
        problems = []
        for symbol, frame in usable.items():
            report = validate_bars(
                frame, symbol=symbol, timeframe=spec.bars.primary_timeframe
            )
            if not report.is_clean:
                problems.append(f"{symbol}: {'; '.join(report.issues)}")
        return not problems, "; ".join(problems) or "all symbols clean"

    def _pit():
        for symbol, frame in usable.items():
            assert_point_in_time(feature_set, frame, samples=6)
        return True, f"{len(feature_set.feature_names)} features, {len(usable)} symbols"

    def _attack():
        for symbol, frame in usable.items():
            shift_future_attack(feature_set, frame, samples=6)
        return True, "corrupting future bars moves nothing at time t"

    def _attack_bites():
        """The control experiment. If this passes, the attack is not testing anything."""
        frame = next(iter(usable.values()))
        leaky = FeatureSet(
            "control",
            "v0",
            [FeatureSpec("next_bar", 1, lambda f: f["close"].shift(-1), "deliberate leak")],
        )
        try:
            shift_future_attack(leaky, frame, samples=3)
        except PointInTimeViolation:
            return True, "a deliberately leaky feature is rejected, so the test bites"
        return False, "a leaky feature PASSED the attack - the leakage test is broken"

    def _labels_look_forward():
        frame = next(iter(usable.values()))
        assert_labels_see_the_future(
            lambda f: build_labels(f, label_definition), frame, LABEL_COLUMNS
        )
        return True, f"{label_definition.label_def_id} reacts to a changed future"

    def _frozen():
        feature_set.verify_freeze(LOCK_DIR)
        path = feature_set.lock_path(LOCK_DIR).relative_to(REPO)
        return True, f"{path} matches code, hash {feature_set.short_hash}"

    def _embargo():
        ok = spec.validation.embargo_bars >= feature_set.max_lookback
        return ok, (
            f"embargo {spec.validation.embargo_bars} bars vs feature warmup "
            f"{feature_set.max_lookback} bars"
        )

    def _roll_invariance():
        from datetime import date

        contracts = synthetic_contracts(
            start="2026-01-05 09:15",
            periods=900,
            expiries=[date(2026, 1, 29), date(2026, 2, 26), date(2026, 3, 26)],
        )
        assert_returns_are_roll_invariant(contracts)
        return True, "adding a later contract does not change earlier returns"

    def _dataset():
        details = []
        for symbol, frame in usable.items():
            table, report = build_supervised_table(
                frame,
                symbol=symbol,
                source=args.source,
                feature_set=feature_set,
                label_definition=label_definition,
            )
            if table.empty or table.isna().any().any():
                return False, f"{symbol}: empty or contains missing values"
            accounted = (
                report.rows
                + report.dropped_warmup
                + report.dropped_horizon
                + report.dropped_incomplete
            )
            if accounted != report.input_bars:
                return False, f"{symbol}: {accounted} rows accounted vs {report.input_bars} bars"
            details.append(f"{symbol} {report.rows} rows")
        return True, ", ".join(details)

    checks.run("bar quality report clean", _quality)
    checks.run("features are point-in-time", _pit)
    checks.run("features survive shift-future attack", _attack)
    checks.run("leakage test rejects a leaky control", _attack_bites)
    checks.run("labels do look forward", _labels_look_forward)
    checks.run("feature schema frozen and committed", _frozen)
    checks.run("embargo covers the feature warmup", _embargo)
    checks.run("futures roll has no look-ahead", _roll_invariance)
    checks.run("supervised table builds with no gaps", _dataset)

    print(checks.render())
    print("=" * 70)

    if checks.mandatory_failures:
        print(f"RESULT: {checks.mandatory_failures} mandatory check(s) FAILED. G1 not met.\n")
        return 1

    print("RESULT: G1 checks PASS.")
    if args.source in {"synthetic", "yahoo"}:
        print(
            f"CAVEAT: source={args.source} is research plumbing. G1 is only genuinely met "
            "on a vendor feed with real F&O bars and volume."
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
