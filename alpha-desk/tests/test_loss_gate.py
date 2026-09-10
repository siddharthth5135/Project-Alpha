"""Loss gate tests (playbook step P1.0.6)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from alpha_desk.core.types import LossAction
from alpha_desk.loss.interface import LossGate, breakeven_probability, expected_value_inr


class _FixedModel:
    model_id = "fixed_test_model"

    def __init__(self, p: float) -> None:
        self._p = p

    def p_hit_stop(self, draft, context):
        return self._p

    def expected_shortfall_inr(self, draft, context):
        return draft.risk_inr * 1.25


def _draft(sizer, intent, state):
    d = sizer.draft(intent, state)
    assert d is not None
    return d


def test_every_evaluation_attaches_advice(loss_gate, sizer, buy_intent, state):
    result = loss_gate.evaluate(_draft(sizer, buy_intent, state))
    assert result.loss_advice is not None
    assert result.loss_advice.model_id.endswith("STUB")


def test_low_adverse_probability_holds_size(risk_config, sizer, buy_intent, state):
    draft = _draft(sizer, buy_intent, state)
    gate = LossGate(_FixedModel(0.10), risk_config, resizer=sizer.resize)
    result = gate.evaluate(draft)
    assert result.loss_advice.action is LossAction.HOLD
    assert result.lots == draft.lots


def test_mid_band_cuts_size(risk_config, sizer, buy_intent, state, cost_model):
    draft = _draft(sizer, buy_intent, state)
    p_be = breakeven_probability(draft)
    hold_at, cut_at = risk_config.loss_gate.thresholds(p_be)
    mid = (hold_at + cut_at) / 2

    # Need at least two lots for a cut to be expressible.
    big = sizer.resize(draft, 2)
    gate = LossGate(_FixedModel(mid), risk_config, resizer=sizer.resize)
    result = gate.evaluate(big)
    assert result.loss_advice.action is LossAction.CUT
    assert result.lots == 1


def test_cut_below_one_lot_becomes_skip(risk_config, sizer, buy_intent, state):
    draft = _draft(sizer, buy_intent, state)
    assert draft.lots == 1
    p_be = breakeven_probability(draft)
    hold_at, cut_at = risk_config.loss_gate.thresholds(p_be)
    gate = LossGate(_FixedModel((hold_at + cut_at) / 2), risk_config, resizer=sizer.resize)
    result = gate.evaluate(draft)
    assert result.loss_advice.action is LossAction.SKIP


def test_high_adverse_probability_skips(risk_config, sizer, buy_intent, state):
    gate = LossGate(_FixedModel(0.99), risk_config, resizer=sizer.resize)
    result = gate.evaluate(_draft(sizer, buy_intent, state))
    assert result.loss_advice.action is LossAction.SKIP


def test_nan_probability_fails_closed(risk_config, sizer, buy_intent, state):
    gate = LossGate(_FixedModel(float("nan")), risk_config, resizer=sizer.resize)
    result = gate.evaluate(_draft(sizer, buy_intent, state))
    assert result.loss_advice.action is LossAction.SKIP
    assert result.loss_advice.p_hit_stop == 1.0


def test_thresholds_scale_with_reward_to_risk(risk_config, sizer, buy_intent, state):
    """A wider target tolerates a higher adverse probability - that is the whole
    reason the gate is relative rather than a fixed cutoff."""
    narrow = _draft(sizer, replace(buy_intent, target_price=24_100.0), state)
    wide = _draft(sizer, replace(buy_intent, target_price=24_400.0), state)
    _, cut_narrow = risk_config.loss_gate.thresholds(breakeven_probability(narrow))
    _, cut_wide = risk_config.loss_gate.thresholds(breakeven_probability(wide))
    assert cut_wide > cut_narrow


def test_cut_recomputes_costs_rather_than_reusing_them(sizer, buy_intent, state):
    two = sizer.resize(sizer.draft(buy_intent, state), 2)
    one = sizer.resize(two, 1)
    assert one.round_turn_cost_inr < two.round_turn_cost_inr
    # Flat brokerage: halving lots does not halve cost.
    assert one.round_turn_cost_inr > two.round_turn_cost_inr / 2


def test_expected_value_helper_matches_definition(sizer, buy_intent, state):
    draft = _draft(sizer, buy_intent, state)
    ev = expected_value_inr(draft, 0.4)
    assert ev == pytest.approx(0.6 * draft.reward_inr - 0.4 * draft.risk_inr)


def test_random_walk_stub_has_no_edge_without_alpha(sizer, buy_intent, state):
    """With alpha_score = 0 the stub reduces to the driftless baseline, whose
    expected value equals minus costs. It must not look profitable."""
    from alpha_desk.loss.heuristic import HeuristicLossModel

    flat = replace(buy_intent, alpha_score=0.0)
    draft = _draft(sizer, flat, state)
    p = HeuristicLossModel().p_hit_stop(draft, {})
    assert expected_value_inr(draft, p) < 0


def test_stub_model_is_flagged_as_a_stub():
    from alpha_desk.loss.heuristic import HeuristicLossModel

    assert HeuristicLossModel.is_stub is True
