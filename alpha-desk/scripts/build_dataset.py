"""Build the supervised training table from warehouse bars.

    python scripts/build_dataset.py --source synthetic
    python scripts/build_dataset.py --source yahoo --freeze

Output: <data_root>/datasets/<label_def_id>/<feature_hash>/<symbol>.parquet
plus a JSON build report beside it.

`--freeze` writes the feature lock file into configs/feature_locks/. Freeze once
per feature-set version, commit it, and never edit it afterwards: the lock is
what makes "the schema was frozen before out-of-sample evaluation" a checkable
claim rather than a promise.
"""

from __future__ import annotations

import argparse
import json
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
from alpha_desk.datasets.builder import build_supervised_table  # noqa: E402
from alpha_desk.features.catalogue import build_feature_set_v0  # noqa: E402
from alpha_desk.features.pit import assert_point_in_time, shift_future_attack  # noqa: E402
from alpha_desk.labels.definitions import label_definition_from_config  # noqa: E402
from alpha_desk.data.warehouse import ParquetWarehouse  # noqa: E402
from alpha_desk.registry.trial_registry import TrialRegistry  # noqa: E402

LOCK_DIR = CONFIG_DIR / "feature_locks"
PLUMBING_SOURCES = frozenset({"yahoo", "synthetic"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the supervised dataset")
    parser.add_argument("--source", default="synthetic", help="warehouse source partition")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--freeze", action="store_true", help="write the feature lock file")
    parser.add_argument("--skip-pit", action="store_true", help="skip leakage checks (not advised)")
    parser.add_argument(
        "--promote-grade",
        action="store_true",
        help="refuse yahoo/synthetic; mark the build as eligible for validation evidence",
    )
    args = parser.parse_args()

    if args.promote_grade and args.source in PLUMBING_SOURCES:
        print(
            f"\nREFUSING: --promote-grade cannot use source={args.source!r}.\n"
            "Yahoo/synthetic are plumbing only. See docs/DATA_VENDORS.md.\n"
            "Ingest a futures vendor (or angelone history) into its own warehouse "
            "partition, then rebuild with that --source.\n"
        )
        return 2

    settings = load_settings()
    spec = load_strategy_spec()
    timeframe = args.timeframe or spec.bars.primary_timeframe
    symbols = args.symbols or list(spec.universe.underlyings)

    feature_set = build_feature_set_v0()
    label_definition = label_definition_from_config(load_risk_config(), spec)

    print(f"\nfeature set   : {feature_set.name}/{feature_set.version} {feature_set.short_hash}")
    print(f"features      : {len(feature_set.feature_names)}, warmup {feature_set.max_lookback} bars")
    print(f"label def     : {label_definition.label_def_id}")
    print(f"embargo needed: >= {feature_set.max_lookback} bars (spec has {spec.validation.embargo_bars})")

    if spec.validation.embargo_bars < feature_set.max_lookback:
        print("\nREFUSING: the walk-forward embargo is shorter than the feature warmup.")
        print("Raise validation.embargo_bars in configs/strategy_spec_001.yaml.\n")
        return 1

    if args.freeze:
        path = feature_set.freeze(LOCK_DIR)
        print(f"froze         : {path.relative_to(REPO)}")
    feature_set.verify_freeze(LOCK_DIR)
    print("lock verified : code matches the frozen schema")

    warehouse = ParquetWarehouse(settings.data_root)
    out_root = settings.data_root / "datasets" / label_definition.label_def_id / feature_set.short_hash
    out_root.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    reports = []
    for symbol in symbols:
        bars = warehouse.read_bars(args.source, symbol, timeframe)
        if bars.empty:
            print(f"\n  {symbol}: no bars in the warehouse for source={args.source}")
            exit_code = 1
            continue

        if not args.skip_pit:
            assert_point_in_time(feature_set, bars, samples=4)
            shift_future_attack(feature_set, bars, samples=4)

        table, report = build_supervised_table(
            bars,
            symbol=symbol,
            source=args.source,
            feature_set=feature_set,
            label_definition=label_definition,
        )
        if table.empty:
            print(f"\n  {symbol}: no rows survived warmup and horizon trimming")
            exit_code = 1
            continue

        table.to_parquet(out_root / f"{symbol}.parquet", index=False)
        reports.append(report.as_dict())
        print(
            f"\n  {symbol}: {report.rows} rows from {report.input_bars} bars"
            f"  (warmup -{report.dropped_warmup}, horizon -{report.dropped_horizon},"
            f" incomplete -{report.dropped_incomplete})"
        )
        print(f"    window      : {report.first_ts} .. {report.last_ts}")
        print(f"    stop-hit    : long {report.label_balance['y_hit_stop_long']:.3f}"
              f"  short {report.label_balance['y_hit_stop_short']:.3f}")
        print(f"    unresolved  : long {report.label_balance['y_outcome_long_unresolved']:.3f}")

    if reports:
        (out_root / "build_report.json").write_text(
            json.dumps(
                {
                    "source": args.source,
                    "timeframe": timeframe,
                    "feature_schema": feature_set.schema(),
                    "label_definition": label_definition.as_dict(),
                    "symbols": reports,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        trial_id = _register(settings, spec, feature_set, label_definition, args.source, reports)
        print(f"\nwrote {out_root}")
        print(f"trial         : {trial_id} (PROPOSED, dataset stage recorded)")
        if args.promote_grade:
            print("grade         : PROMOTE-ELIGIBLE (non-plumbing source)")
        elif args.source in PLUMBING_SOURCES:
            print("NOTE: this dataset is research plumbing, not promotion evidence.")
            print("      Use --promote-grade with a futures vendor source when ready.")
    print()
    return exit_code


def _register(settings, spec, feature_set, label_definition, source, reports) -> str:
    """Open a trial for this dataset so a later model can be traced to its data."""
    with TrialRegistry(settings.data_root / "registry" / "trials.sqlite") as registry:
        trial_id = registry.create_trial(
            strategy_id=spec.strategy_id,
            hypothesis=(
                f"lean/{feature_set.version} features on {source} bars, labelled "
                f"{label_definition.label_def_id}, can separate stop-outs from targets "
                "well enough to clear costs. Untested at this point."
            ),
            feature_set_hash=feature_set.hash,
            label_def_id=label_definition.label_def_id,
            config_snapshot={
                "source": source,
                "timeframe": spec.bars.primary_timeframe,
                "embargo_bars": spec.validation.embargo_bars,
                "feature_warmup_bars": feature_set.max_lookback,
                "label_definition": label_definition.as_dict(),
            },
        )
        registry.record_metrics(
            trial_id,
            "dataset",
            {"symbols": reports},
            costs_verified=False,
        )
        return trial_id


if __name__ == "__main__":
    raise SystemExit(main())
