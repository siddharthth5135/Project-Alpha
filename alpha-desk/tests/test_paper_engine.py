"""Paper decision engine: features → score → rules → OrderPath (P1.4.3)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from alpha_desk.data.providers.synthetic import SyntheticProvider
from alpha_desk.features.catalogue import build_feature_set_v0
from alpha_desk.pipeline.order_path import PathStatus
from alpha_desk.pipeline.paper_engine import PaperDecisionEngine
from alpha_desk.strategy.rules import RulesConfig, SignalAction, StrategyRules

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def synth_bars() -> pd.DataFrame:
    return SyntheticProvider(seed=7).fetch("NIFTY", "15m", "2025-11-01", "2026-01-31")


def test_flat_score_never_reaches_broker(order_path, state, instruments, paper_broker, synth_bars):
    rules = StrategyRules(
        RulesConfig(strategy_id="TEST", alpha_threshold=0.05, require_regime_ok=False, require_liquidity_ok=False)
    )
    engine = PaperDecisionEngine(
        feature_set=build_feature_set_v0(),
        rules=rules,
        order_path=order_path,
        score_fn=lambda _frame: 0.0,
        instruments=instruments,
    )
    # Enough history for warmup
    window = synth_bars.iloc[:200]
    decision = engine.on_bar(window, symbol="NIFTY", state=state)
    assert decision.signal == SignalAction.FLAT.value
    assert decision.path_status is None
    assert paper_broker.fills == []


def test_strong_score_can_reach_paper_fill(order_path, state, instruments, paper_broker, synth_bars):
    rules = StrategyRules(
        RulesConfig(
            strategy_id="TEST",
            alpha_threshold=0.0,
            require_regime_ok=False,
            require_liquidity_ok=False,
            stop_atr_multiple=1.0,
            target_rr=4.0,  # headroom so cost drag does not trip R017 on synthetic ATR
        )
    )
    engine = PaperDecisionEngine(
        feature_set=build_feature_set_v0(),
        rules=rules,
        order_path=order_path,
        score_fn=lambda _frame: 1.5,
        instruments=instruments,
    )
    window = synth_bars.iloc[:200]
    # Align clock with session fixture expectations
    now = datetime(2026, 7, 24, 11, 0, tzinfo=IST)
    state.heartbeat(now)
    decision = engine.on_bar(window, symbol="NIFTY", state=state, now=now)
    assert decision.signal == SignalAction.ENTER.value
    assert decision.path_status == PathStatus.FILLED.value
    assert decision.fill_price is not None
    assert len(paper_broker.fills) == 1


def test_kill_switch_blocks_paper_engine_path(
    order_path, kill_switch, state, instruments, paper_broker, synth_bars
):
    rules = StrategyRules(
        RulesConfig(
            strategy_id="TEST",
            alpha_threshold=0.0,
            require_regime_ok=False,
            require_liquidity_ok=False,
        )
    )
    engine = PaperDecisionEngine(
        feature_set=build_feature_set_v0(),
        rules=rules,
        order_path=order_path,
        score_fn=lambda _frame: 1.5,
        instruments=instruments,
    )
    kill_switch.engage("paper engine drill", engaged_by="test")
    now = datetime(2026, 7, 24, 11, 0, tzinfo=IST)
    state.heartbeat(now)
    decision = engine.on_bar(synth_bars.iloc[:200], symbol="NIFTY", state=state, now=now)
    assert decision.signal == SignalAction.ENTER.value
    assert decision.path_status == PathStatus.REJECTED_BY_RISK.value
    assert paper_broker.fills == []
