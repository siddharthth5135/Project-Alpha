"""Train P1.2 model artifacts (paper / research path only).

    python scripts/train_models.py --source synthetic

Never places broker orders. Plumbing sources are stamped not promote-eligible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import load_settings, load_strategy_spec  # noqa: E402
from alpha_desk.features.catalogue import build_feature_set_v0  # noqa: E402
from alpha_desk.labels.definitions import label_definition_from_config  # noqa: E402
from alpha_desk.config.settings import load_risk_config  # noqa: E402
from alpha_desk.models.artifacts import PLUMBING_SOURCES  # noqa: E402
from alpha_desk.models.lgbm_alpha import train_lgbm_alpha  # noqa: E402
from alpha_desk.models.loss_model import train_loss_model  # noqa: E402
from alpha_desk.models.ridge_alpha import train_ridge_alpha  # noqa: E402
from alpha_desk.registry.trial_registry import TrialRegistry, TrialStatus  # noqa: E402


def _load_table(data_root: Path, source: str, label_def_id: str, feature_hash: str) -> pd.DataFrame:
    # Prefer exact hash folder; fall back to any hash under the label def.
    base = data_root / "datasets" / label_def_id
    if not base.exists():
        raise FileNotFoundError(f"no datasets under {base}; run build_dataset.py first")
    preferred = base / feature_hash[:12]
    search_roots = [preferred] if preferred.exists() else sorted(base.iterdir())
    frames = []
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.parquet")):
            if path.name == "build_report.json":
                continue
            part = pd.read_parquet(path)
            if "source" in part.columns:
                part = part[part["source"] == source]
            if not part.empty:
                frames.append(part)
        if frames:
            break
    if not frames:
        raise FileNotFoundError(
            f"no parquet rows for source={source} under {base}. "
            f"Run: python scripts/build_dataset.py --source {source}"
        )
    table = pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description="Train P1.2 model artifacts")
    parser.add_argument("--source", default="synthetic")
    parser.add_argument("--skip-loss", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    if settings.live_enabled:
        print("REFUSING: live mode is on. P1.2 training is paper/research only.")
        return 2

    spec = load_strategy_spec()
    risk = load_risk_config()
    feature_set = build_feature_set_v0()
    label_def = label_definition_from_config(risk, spec)

    print("\nProject Alpha — P1.2 model training")
    print("=" * 70)
    print(f"mode          : {settings.mode.value} (live_enabled={settings.live_enabled})")
    print(f"source        : {args.source}")
    print(f"feature set   : lean/{feature_set.version} {feature_set.short_hash}")
    print(f"label def     : {label_def.label_def_id}")
    print(f"embargo       : {spec.validation.embargo_bars} bars")
    if args.source in PLUMBING_SOURCES:
        print("NOTE          : plumbing source — artifacts will NOT be promote-eligible")

    table = _load_table(
        settings.data_root, args.source, label_def.label_def_id, feature_set.hash
    )
    print(f"rows          : {len(table)} across {sorted(table['symbol'].unique())}")

    art_root = settings.artifact_root / "models"
    results = {}

    ridge = train_ridge_alpha(
        table,
        artifact_root=art_root,
        embargo_bars=spec.validation.embargo_bars,
        source=args.source,
    )
    results["ridge_alpha"] = {
        "model_id": ridge.model_id,
        "metrics": ridge.metrics_test,
        "promote_eligible": ridge.meta.promote_eligible,
        "path": str(ridge.folder),
    }
    print(f"\nridge_alpha   : {ridge.model_id}")
    print(f"  test IC={ridge.metrics_test.get('ic', float('nan')):.4f} "
          f"rmse={ridge.metrics_test.get('rmse', float('nan')):.6f} "
          f"promote={ridge.meta.promote_eligible}")

    lgbm = train_lgbm_alpha(
        table,
        artifact_root=art_root,
        embargo_bars=spec.validation.embargo_bars,
        source=args.source,
    )
    results["lgbm_alpha"] = {
        "model_id": lgbm.model_id,
        "metrics": lgbm.metrics_test,
        "promote_eligible": lgbm.meta.promote_eligible,
        "path": str(lgbm.folder),
        "top_features": list(lgbm.feature_importance.items())[:5],
    }
    print(f"lgbm_alpha    : {lgbm.model_id}")
    print(f"  test IC={lgbm.metrics_test.get('ic', float('nan')):.4f} "
          f"rmse={lgbm.metrics_test.get('rmse', float('nan')):.6f} "
          f"promote={lgbm.meta.promote_eligible}")

    if not args.skip_loss:
        loss = train_loss_model(
            table,
            artifact_root=art_root,
            embargo_bars=spec.validation.embargo_bars,
            source=args.source,
        )
        results["loss_hit_stop"] = {
            "model_id": loss.model_id,
            "metrics": {
                k: v for k, v in loss.metrics_test.items() if k != "calibration_buckets"
            },
            "calibration_buckets": loss.metrics_test.get("calibration_buckets"),
            "promote_eligible": loss.meta.promote_eligible,
            "path": str(loss.folder),
        }
        print(f"loss_hit_stop : {loss.model_id}")
        print(
            f"  test auc={loss.metrics_test.get('auc', float('nan')):.4f} "
            f"brier={loss.metrics_test.get('brier', float('nan')):.4f} "
            f"promote={loss.meta.promote_eligible}"
        )

    report_path = art_root / "train_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, indent=2, sort_keys=True, default=str), encoding="utf-8")

    with TrialRegistry(settings.data_root / "registry" / "trials.sqlite") as registry:
        tid = registry.create_trial(
            strategy_id=spec.strategy_id,
            hypothesis=(
                f"P1.2 models on source={args.source}: ridge + lgbm alpha + loss gate. "
                "Scaffolding only until promote-grade futures data exists."
            ),
            feature_set_hash=feature_set.hash,
            label_def_id=label_def.label_def_id,
            config_snapshot={"source": args.source, "artifacts": results},
        )
        registry.record_metrics(tid, "p1_2_train", results, costs_verified=False)
        registry.decide(
            tid,
            status=TrialStatus.EVAL,
            decided_by="train_models.py",
            rationale="artifacts saved; not a promote decision",
        )
        print(f"\ntrial         : {tid} status=EVAL")

    print(f"report        : {report_path}")
    print("=" * 70)
    print("RESULT: P1.2 artifacts written. Gate G2 = versioned models exist.")
    print("NOT a promote. Paper trading only. TrueData API still required for Fully GO.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
