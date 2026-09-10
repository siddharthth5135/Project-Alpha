"""Run the full paper decision path on warehouse bars (P1.4.3 plumbing).

    python scripts/run_paper_path.py --source synthetic --symbol NIFTY

Never places Angel live orders. Uses PaperBroker via build_desk().

If a trained ridge_alpha artifact exists it is used for scores; otherwise a
fixed plumbing score drives the path so the wire can be exercised without
models. Promote-grade paper (P1.5) requires a real vendor feed + promoted trial.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.desk import build_desk  # noqa: E402
from alpha_desk.features.catalogue import build_feature_set_v0  # noqa: E402
from alpha_desk.models.artifacts import ArtifactStore  # noqa: E402
from alpha_desk.pipeline.paper_engine import PaperDecisionEngine, score_from_artifact  # noqa: E402
from alpha_desk.strategy.rules import StrategyRules, rules_from_strategy_spec  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def main() -> int:
    parser = argparse.ArgumentParser(description="Wire features→rules→OrderPath on paper")
    parser.add_argument("--source", default="synthetic")
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--bars", type=int, default=250, help="history window ending at last bar")
    parser.add_argument(
        "--score",
        type=float,
        default=None,
        help="override model with a fixed alpha score (plumbing)",
    )
    parser.add_argument("--model-kind", default="ridge_alpha")
    args = parser.parse_args()

    desk = build_desk()
    for warning in desk.preflight():
        print(f"  warn: {warning}")

    if desk.settings.mode.value == "live":
        print("REFUSING: ALPHA_MODE=live — this script is paper-path only.")
        return 2

    timeframe = args.timeframe or desk.strategy_spec.bars.primary_timeframe
    bars = desk.warehouse.read_bars(args.source, args.symbol, timeframe)
    if bars.empty:
        print(f"No bars for {args.source}/{args.symbol}/{timeframe}. Run fetch_data.py first.")
        return 1
    if len(bars) < args.bars:
        print(f"Need >= {args.bars} bars, have {len(bars)}")
        return 1

    feature_set = build_feature_set_v0()
    rules_cfg = rules_from_strategy_spec(
        desk.strategy_spec, feature_set_hash=feature_set.short_hash
    )
    rules = StrategyRules(rules_cfg)

    if args.score is not None:
        score_fn = lambda _frame, s=args.score: float(s)  # noqa: E731
        score_label = f"fixed:{args.score}"
    else:
        store = ArtifactStore(desk.settings.artifact_root / "models")
        latest = store.latest(args.model_kind)
        if latest is None:
            print(f"No {args.model_kind} artifact; using plumbing score=0.5")
            score_fn = lambda _frame: 0.5  # noqa: E731
            score_label = "plumbing:0.5"
        else:
            model, meta = store.load(args.model_kind, latest[1].model_id)
            score_fn = score_from_artifact(model, meta.feature_names)
            score_label = f"{args.model_kind}:{meta.model_id}"

    engine = PaperDecisionEngine(
        feature_set=feature_set,
        rules=rules,
        order_path=desk.order_path,
        score_fn=score_fn,
        instruments=desk.instruments,
    )

    window = bars.iloc[-args.bars :].copy()
    # Use a mid-session clock so session gates do not veto the wire check.
    now = datetime(2026, 7, 24, 11, 0, tzinfo=IST)
    desk.state.heartbeat(now)

    print(f"\nPaper path wire check")
    print(f"  source/symbol : {args.source}/{args.symbol} ({timeframe})")
    print(f"  score         : {score_label}")
    print(f"  threshold T   : {rules_cfg.alpha_threshold}")
    print(f"  strategy      : {rules_cfg.strategy_id}")

    decision = engine.on_bar(window, symbol=args.symbol, state=desk.state, now=now)
    print(f"\n  signal        : {decision.signal}")
    print(f"  alpha_score   : {decision.alpha_score:.6f}")
    print(f"  reason        : {decision.reason}")
    print(f"  path_status   : {decision.path_status}")
    print(f"  detail        : {decision.detail}")
    print(f"  fill_price    : {decision.fill_price}")
    print(f"  open positions: {desk.state.open_position_count}")
    print(f"  audit dir     : {desk.audit.path.parent}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
