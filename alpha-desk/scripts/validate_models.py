"""P1.3 purged walk-forward validation + cost-aware report.

    python scripts/validate_models.py --source synthetic

Paper/research only. Plumbing sources cannot promote.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import (  # noqa: E402
    load_risk_config,
    load_settings,
    load_strategy_spec,
)
from alpha_desk.features.catalogue import build_feature_set_v0  # noqa: E402
from alpha_desk.labels.definitions import label_definition_from_config  # noqa: E402
from alpha_desk.models.artifacts import PLUMBING_SOURCES  # noqa: E402
from alpha_desk.models.columns import feature_columns  # noqa: E402
from alpha_desk.registry.trial_registry import TrialRegistry, TrialStatus  # noqa: E402
from alpha_desk.validation.report import build_report, score_fold  # noqa: E402
from alpha_desk.validation.walkforward import (  # noqa: E402
    PromotePathError,
    assert_promote_scheme_allowed,
    purged_walk_forward,
)


def _load_table(data_root: Path, source: str, label_def_id: str, feature_hash: str) -> pd.DataFrame:
    base = data_root / "datasets" / label_def_id
    if not base.exists():
        raise FileNotFoundError(f"missing datasets at {base}")
    preferred = base / feature_hash[:12]
    roots = [preferred] if preferred.exists() else sorted(p for p in base.iterdir() if p.is_dir())
    frames: list[pd.DataFrame] = []
    for root in roots:
        for path in sorted(root.glob("*.parquet")):
            part = pd.read_parquet(path)
            if "source" in part.columns:
                part = part[part["source"] == source]
            if not part.empty:
                frames.append(part)
        if frames:
            break
    if not frames:
        raise FileNotFoundError(f"no rows for source={source} under {base}")
    return pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)


def _fit_predict_ridge(train: pd.DataFrame, test: pd.DataFrame, features: list[str]):
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    pipe = Pipeline([("scale", StandardScaler()), ("ridge", Ridge(alpha=1.0))])
    pipe.fit(train[features], train["y_fwd_ret_H"].to_numpy(float))
    return pipe.predict(test[features])


def _fit_predict_lgbm(train: pd.DataFrame, test: pd.DataFrame, features: list[str]):
    import lightgbm as lgb

    model = lgb.LGBMRegressor(
        n_estimators=150,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=0,
        verbosity=-1,
    )
    model.fit(train[features], train["y_fwd_ret_H"].to_numpy(float))
    return model.predict(test[features])


def main() -> int:
    parser = argparse.ArgumentParser(description="P1.3 walk-forward validation")
    parser.add_argument("--source", default="synthetic")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--model", choices=["ridge", "lgbm", "both"], default="both")
    parser.add_argument(
        "--for-promote",
        action="store_true",
        help="fail closed unless scheme/data allow promotion",
    )
    args = parser.parse_args()

    settings = load_settings()
    if settings.live_enabled:
        print("REFUSING: live mode on — validation is paper/research only")
        return 2

    spec = load_strategy_spec()
    risk = load_risk_config()
    feature_set = build_feature_set_v0()
    label_def = label_definition_from_config(risk, spec)

    print("\nProject Alpha — P1.3 purged walk-forward")
    print("=" * 70)
    print(f"mode       : {settings.mode.value}")
    print(f"source     : {args.source}")
    print(f"scheme     : {spec.validation.scheme}")
    print(f"embargo    : {spec.validation.embargo_bars}")
    print(f"horizon    : {label_def.horizon_bars}")
    print(f"for_promote: {args.for_promote}")

    try:
        assert_promote_scheme_allowed(spec.validation.scheme, for_promote=args.for_promote)
    except PromotePathError as exc:
        print(f"\nREFUSING promote path: {exc}\n")
        return 3

    if args.for_promote and args.source in PLUMBING_SOURCES:
        print(
            f"\nREFUSING: --for-promote cannot use plumbing source={args.source!r}. "
            "Wait for TrueData API / futures vendor.\n"
        )
        return 3

    table = _load_table(
        settings.data_root, args.source, label_def.label_def_id, feature_set.hash
    )
    features = feature_columns(table)
    print(f"rows       : {len(table)} features={len(features)}")

    folds = purged_walk_forward(
        table,
        n_folds=args.folds,
        embargo_bars=spec.validation.embargo_bars,
        horizon_bars=label_def.horizon_bars,
    )
    print(f"folds      : {len(folds)}")

    models = []
    if args.model in {"ridge", "both"}:
        models.append(("ridge_alpha", _fit_predict_ridge))
    if args.model in {"lgbm", "both"}:
        models.append(("lgbm_alpha", _fit_predict_lgbm))

    all_reports = {}
    out_dir = settings.artifact_root / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    for kind, fit_predict in models:
        fold_reports = []
        for fold in folds:
            train = table.loc[fold.train_idx]
            test = table.loc[fold.test_idx]
            pred = fit_predict(train, test, features)
            fold_reports.append(
                score_fold(
                    fold_id=fold.fold_id,
                    y_true=test["y_fwd_ret_H"].to_numpy(float),
                    y_pred=pred,
                    train_rows=len(train),
                    test_rows=len(test),
                    purged_rows=fold.purged_rows,
                )
            )
        report = build_report(source=args.source, model_kind=kind, folds=fold_reports)
        all_reports[kind] = report.as_dict()
        path = out_dir / f"{kind}_{args.source}_wf.json"
        path.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        agg = report.aggregate
        print(f"\n{kind}")
        print(
            f"  mean IC={agg.get('mean_ic', 0):.4f}  "
            f"Sharpe={agg.get('mean_sharpe', 0):.3f}  "
            f"hit={agg.get('mean_hit_rate', 0):.3f}  "
            f"maxDD={agg.get('mean_max_drawdown', 0):.3f}  "
            f"promote_eligible={report.promote_eligible}"
        )
        print(f"  wrote {path}")

    summary_path = out_dir / f"summary_{args.source}.json"
    summary_path.write_text(json.dumps(all_reports, indent=2, sort_keys=True), encoding="utf-8")

    with TrialRegistry(settings.data_root / "registry" / "trials.sqlite") as registry:
        tid = registry.create_trial(
            strategy_id=spec.strategy_id,
            hypothesis=f"P1.3 walk-forward on source={args.source} ({args.model})",
            feature_set_hash=feature_set.hash,
            label_def_id=label_def.label_def_id,
            config_snapshot={"source": args.source, "folds": args.folds},
        )
        registry.record_metrics(tid, "p1_3_walkforward", all_reports, costs_verified=False)
        registry.decide(
            tid,
            status=TrialStatus.EVAL,
            decided_by="validate_models.py",
            rationale="walk-forward complete; human must PROMOTE_TO_PAPER or REJECT",
        )
        print(f"\ntrial      : {tid} status=EVAL")
        print("next       : python scripts/decide_trial.py --trial-id", tid, "--decision REJECT|PROMOTE_TO_PAPER --by YOU --reason '...'")

    print("=" * 70)
    print("RESULT: P1.3 harness ran. Plumbing results are not promote evidence.")
    print("Gate G3 still needs your written promote/reject on a real-data trial.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
