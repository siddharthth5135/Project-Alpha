"""Build a supervised table from the promoted MarketStore.

The same FeatureSet and LabelDefinition the paper path uses. Output lands
where ``train_models.py --source truedata`` already looks.

    python scripts/train_from_store.py --contract-id NSE:FUT:NIFTY:CONT-vendor_spliced
    python scripts/train_from_store.py --then-train
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import load_risk_config, load_settings, load_strategy_spec  # noqa: E402
from alpha_desk.data.store import MarketStore  # noqa: E402
from alpha_desk.features.catalogue import build_feature_set_v0  # noqa: E402
from alpha_desk.ingest.ml_bridge import build_training_table  # noqa: E402
from alpha_desk.labels.definitions import label_definition_from_config  # noqa: E402
from alpha_desk.marketdata.contracts import BarInterval, contract_from_id  # noqa: E402
from alpha_desk.models.artifacts import PLUMBING_SOURCES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="truedata")
    parser.add_argument("--contract-id", default="NSE:FUT:NIFTY:CONT-vendor_spliced")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument(
        "--then-train",
        action="store_true",
        help="run train_models.py on the table just written",
    )
    args = parser.parse_args()

    settings = load_settings()
    if settings.live_enabled:
        print("REFUSING: live mode is on. Training is paper/research only.")
        return 2
    if args.source in PLUMBING_SOURCES:
        print(
            f"REFUSING: {args.source!r} is a plumbing source. "
            "This script reads promoted vendor data only."
        )
        return 2

    spec = load_strategy_spec()
    feature_set = build_feature_set_v0()
    label_def = label_definition_from_config(load_risk_config(), spec)
    contract = contract_from_id(args.contract_id)
    interval = BarInterval(args.interval)
    store = MarketStore(args.data_root or settings.data_root)

    print("\nProject Alpha — supervised table from promoted store")
    print("=" * 70)
    print(f"source        : {args.source}")
    print(f"contract      : {contract.contract_id}")
    print(f"interval      : {interval.value}")
    print(f"feature set   : {feature_set.short_hash}")
    print(f"label def     : {label_def.label_def_id}")

    bundle = build_training_table(
        store,
        source=args.source,
        contract=contract,
        interval=interval,
        feature_set=feature_set,
        label_definition=label_def,
    )
    print(
        f"rows          : {bundle.report.rows} "
        f"(from {bundle.bars_in} bars, warmup drop {bundle.report.dropped_warmup})"
    )
    print(f"window        : {bundle.report.first_ts} -> {bundle.report.last_ts}")

    out_dir = settings.data_root / "datasets" / label_def.label_def_id / feature_set.hash[:12]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{contract.storage_key}.parquet"
    bundle.frame.to_parquet(path, index=False)
    (out_dir / f"{contract.storage_key}.build_report.json").write_text(
        json.dumps(bundle.as_dict(), indent=2, default=str), encoding="utf-8"
    )
    print(f"wrote         : {path}")

    if not args.then_train:
        print(f"\nnext          : python scripts/train_models.py --source {args.source}")
        return 0

    cmd = [
        sys.executable,
        str(REPO / "scripts" / "train_models.py"),
        "--source",
        args.source,
    ]
    print("\n" + " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
