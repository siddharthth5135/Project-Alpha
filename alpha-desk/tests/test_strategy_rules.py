"""Strategy rules unit tests (playbook step P1.4.1 exit gate).

Threshold edges, regime/liquidity filters, and the invariant that flat never
produces an Intent.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from alpha_desk.config.settings import load_strategy_spec
from alpha_desk.strategy.rules import (
    RulesConfig,
    SignalAction,
    StrategyRules,
    rules_from_strategy_spec,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def rules() -> StrategyRules:
    return StrategyRules(
        RulesConfig(
            strategy_id="TEST",
            alpha_threshold=0.05,
            require_regime_ok=True,
            allowed_regime_buckets=(1.0, 2.0),
            require_liquidity_ok=True,
            min_volume_zscore=-1.0,
            stop_atr_multiple=1.5,
            target_rr=2.0,
            feature_set_hash="abc",
        )
    )


def _feats(**overrides: float) -> dict[str, float]:
    base = {"vol_regime_bucket": 1.0, "volume_zscore_20": 0.0}
    base.update(overrides)
    return base


def test_zero_score_is_always_flat(rules, nifty):
    decision = rules.decide(
        ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
        instrument=nifty,
        ref_price=24_000.0,
        atr_points=40.0,
        alpha_score=0.0,
        features=_feats(),
    )
    assert decision.action is SignalAction.FLAT
    assert decision.intent is None


def test_score_below_threshold_is_flat(rules, nifty):
    decision = rules.decide(
        ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
        instrument=nifty,
        ref_price=24_000.0,
        atr_points=40.0,
        alpha_score=0.049,
        features=_feats(),
    )
    assert decision.is_flat
    assert decision.intent is None


def test_score_at_threshold_enters_long(rules, nifty):
    decision = rules.decide(
        ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
        instrument=nifty,
        ref_price=24_000.0,
        atr_points=40.0,
        alpha_score=0.05,
        features=_feats(),
    )
    assert decision.action is SignalAction.ENTER
    assert decision.intent is not None
    assert decision.intent.side.value == "BUY"
    assert decision.intent.stop_price == 23_940.0  # 24000 - 1.5*40
    assert decision.intent.target_price == 24_120.0  # + 2 * stop_dist


def test_negative_score_enters_short(rules, nifty):
    decision = rules.decide(
        ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
        instrument=nifty,
        ref_price=24_000.0,
        atr_points=40.0,
        alpha_score=-0.2,
        features=_feats(),
    )
    assert decision.intent is not None
    assert decision.intent.side.value == "SELL"
    assert decision.intent.stop_price == 24_060.0
    assert decision.intent.target_price == 23_880.0


def test_regime_filter_blocks_entry(rules, nifty):
    decision = rules.decide(
        ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
        instrument=nifty,
        ref_price=24_000.0,
        atr_points=40.0,
        alpha_score=1.0,
        features=_feats(vol_regime_bucket=0.0),  # low vol not in allowed
    )
    assert decision.is_flat
    assert "regime" in decision.reason


def test_liquidity_filter_blocks_entry(rules, nifty):
    decision = rules.decide(
        ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
        instrument=nifty,
        ref_price=24_000.0,
        atr_points=40.0,
        alpha_score=1.0,
        features=_feats(volume_zscore_20=-2.0),
    )
    assert decision.is_flat
    assert "liquidity" in decision.reason


def test_invalid_atr_is_flat(rules, nifty):
    decision = rules.decide(
        ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
        instrument=nifty,
        ref_price=24_000.0,
        atr_points=0.0,
        alpha_score=1.0,
        features=_feats(),
    )
    assert decision.is_flat
    assert decision.intent is None


def test_rules_from_strategy_spec_reads_numeric_thresholds():
    spec = load_strategy_spec()
    cfg = rules_from_strategy_spec(spec, feature_set_hash="deadbeef")
    assert cfg.strategy_id == spec.strategy_id
    assert cfg.alpha_threshold == 0.0
    assert cfg.stop_atr_multiple == 1.5
    assert cfg.target_rr == 2.0
    assert cfg.feature_set_hash == "deadbeef"
