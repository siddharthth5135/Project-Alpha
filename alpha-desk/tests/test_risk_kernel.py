"""Risk Kernel unit tests (playbook step P1.0.5, exit gate: >= 20 tests green).

Each test asserts one refusal, or the one case where everything is in order.
A rejection here is the product working.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from alpha_desk.config.settings import AppSettings, load_risk_config
from alpha_desk.core.types import LossAction, LossAdvice, Mode, Side
from alpha_desk.risk.kernel import (
    HALT_DAILY_LOSS,
    HALT_DRAWDOWN,
    HALT_FEED_LOSS,
    ReasonCode,
    RiskKernel,
)
from tests.conftest import IST


def codes(decision) -> set[str]:
    return set(decision.reason_codes)


# --------------------------------------------------------------------------- #
# the one happy path
# --------------------------------------------------------------------------- #


def test_clean_draft_is_approved(kernel, approved_draft, state, market_time):
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert decision.approved, decision.reasons
    assert decision.approved_lots == approved_draft.lots


# --------------------------------------------------------------------------- #
# global stops
# --------------------------------------------------------------------------- #


def test_kill_switch_blocks_everything(kernel, kill_switch, approved_draft, state, market_time):
    kill_switch.engage("drill", engaged_by="HoQR")
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert not decision.approved
    assert ReasonCode.KILL_SWITCH_ENGAGED in codes(decision)


def test_kill_switch_also_blocks_exits(kernel, kill_switch, approved_draft, state, market_time):
    kill_switch.engage("drill")
    exit_draft = replace(
        approved_draft, intent=replace(approved_draft.intent, reduce_only=True)
    )
    decision = kernel.evaluate(exit_draft, state, market_time)
    assert not decision.approved
    assert ReasonCode.KILL_SWITCH_ENGAGED in codes(decision)


def test_daily_loss_breach_halts_and_rejects(kernel, approved_draft, state, market_time):
    state.apply_pnl(-0.031 * state.start_of_day_equity)
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert not decision.approved
    assert ReasonCode.DAILY_LOSS_BREACH in codes(decision)
    assert HALT_DAILY_LOSS in state.halts


def test_daily_loss_just_inside_limit_does_not_halt(kernel, approved_draft, state, market_time):
    state.apply_pnl(-0.029 * state.start_of_day_equity)
    kernel.evaluate(approved_draft, state, market_time)
    assert HALT_DAILY_LOSS not in state.halts


def test_drawdown_breach_halts_and_rejects(kernel, approved_draft, state, market_time):
    state.peak_equity = 1_200_000  # 16.7% below peak, cap is 12%
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert not decision.approved
    assert ReasonCode.DRAWDOWN_BREACH in codes(decision)
    assert HALT_DRAWDOWN in state.halts


def test_stale_heartbeat_halts_trading(kernel, approved_draft, state, market_time):
    state.heartbeat(market_time - timedelta(seconds=600))
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert not decision.approved
    assert ReasonCode.DATA_FEED_STALE in codes(decision)
    assert HALT_FEED_LOSS in state.halts


def test_feed_halt_clears_when_heartbeat_returns(kernel, approved_draft, state, market_time):
    state.heartbeat(market_time - timedelta(seconds=600))
    kernel.evaluate(approved_draft, state, market_time)
    assert HALT_FEED_LOSS in state.halts

    state.heartbeat(market_time)
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert HALT_FEED_LOSS not in state.halts
    assert decision.approved, decision.reasons


def test_broker_disconnect_blocks_new_risk(kernel, approved_draft, state, market_time):
    state.broker_connected = False
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert ReasonCode.BROKER_DISCONNECTED in codes(decision)


def test_exits_are_allowed_while_halted(kernel, approved_draft, state, market_time):
    state.apply_pnl(-0.05 * state.start_of_day_equity)
    exit_draft = replace(
        approved_draft, intent=replace(approved_draft.intent, reduce_only=True)
    )
    decision = kernel.evaluate(exit_draft, state, market_time)
    assert decision.approved, decision.reasons


# --------------------------------------------------------------------------- #
# mode / signature
# --------------------------------------------------------------------------- #


def test_live_mode_cannot_construct_settings_paper_only_phase(tmp_path):
    with pytest.raises(ValueError, match="paper-only"):
        AppSettings(
            mode=Mode.LIVE,
            data_root=tmp_path,
            artifact_root=tmp_path,
            log_root=tmp_path,
            live_go_signature="GO-LIVE 2026-09-20 HoQR",
        )


def test_paper_settings_never_enable_live(tmp_path):
    settings = AppSettings(
        mode=Mode.PAPER,
        data_root=tmp_path,
        artifact_root=tmp_path,
        log_root=tmp_path,
        live_go_signature="GO-LIVE should be ignored",
    )
    assert settings.live_enabled is False


# --------------------------------------------------------------------------- #
# loss gate enforcement
# --------------------------------------------------------------------------- #


def test_draft_without_loss_advice_is_rejected(kernel, sizer, buy_intent, state, market_time):
    raw = sizer.draft(buy_intent, state)
    decision = kernel.evaluate(raw, state, market_time)
    assert not decision.approved
    assert ReasonCode.MISSING_LOSS_ADVICE in codes(decision)


def test_loss_gate_skip_is_honoured(kernel, approved_draft, state, market_time):
    skipped = approved_draft.with_loss_advice(
        LossAdvice(
            action=LossAction.SKIP,
            p_hit_stop=0.5,
            expected_shortfall_inr=1000.0,
            model_id="test",
        )
    )
    decision = kernel.evaluate(skipped, state, market_time)
    assert ReasonCode.LOSS_GATE_BLOCKED in codes(decision)


def test_absolute_p_hit_stop_backstop(kernel, approved_draft, state, market_time):
    doomed = approved_draft.with_loss_advice(
        LossAdvice(
            action=LossAction.HOLD,
            p_hit_stop=0.95,
            expected_shortfall_inr=1000.0,
            model_id="test",
        )
    )
    decision = kernel.evaluate(doomed, state, market_time)
    assert ReasonCode.LOSS_GATE_BLOCKED in codes(decision)


def test_negative_expected_value_is_rejected(kernel, approved_draft, state, market_time):
    # Below the absolute backstop, but still a losing bet at this R:R.
    marginal = approved_draft.with_loss_advice(
        LossAdvice(
            action=LossAction.HOLD,
            p_hit_stop=0.80,
            expected_shortfall_inr=1000.0,
            model_id="test",
        )
    )
    decision = kernel.evaluate(marginal, state, market_time)
    assert ReasonCode.NEGATIVE_EXPECTED_VALUE in codes(decision)


# --------------------------------------------------------------------------- #
# order sanity
# --------------------------------------------------------------------------- #


def test_zero_lots_is_rejected(kernel, approved_draft, state, market_time):
    decision = kernel.evaluate(approved_draft.with_lots(0), state, market_time)
    assert ReasonCode.NON_POSITIVE_LOTS in codes(decision)


def test_missing_stop_is_rejected(kernel, approved_draft, state, market_time):
    decision = kernel.evaluate(replace(approved_draft, stop_price=0.0), state, market_time)
    assert ReasonCode.NO_STOP_LOSS in codes(decision)


def test_stop_on_wrong_side_is_rejected(kernel, approved_draft, state, market_time):
    bad = replace(approved_draft, stop_price=approved_draft.entry_price + 50)
    decision = kernel.evaluate(bad, state, market_time)
    assert ReasonCode.INVALID_STOP_SIDE in codes(decision)


def test_non_positive_price_is_rejected(kernel, approved_draft, state, market_time):
    decision = kernel.evaluate(replace(approved_draft, entry_price=0.0), state, market_time)
    assert ReasonCode.INVALID_PRICE in codes(decision)


# --------------------------------------------------------------------------- #
# size caps
# --------------------------------------------------------------------------- #


def test_oversize_order_is_rejected(kernel, approved_draft, state, market_time):
    oversize = approved_draft.with_lots(approved_draft.lots + 20)
    decision = kernel.evaluate(oversize, state, market_time)
    assert ReasonCode.LOTS_OVER_ORDER_CAP in codes(decision)


def test_per_trade_rupee_risk_cap_is_enforced(kernel, approved_draft, state, market_time):
    # Same lots, but a stop far enough away to blow the per-trade budget.
    wide_stop = replace(approved_draft, stop_price=approved_draft.entry_price - 400)
    decision = kernel.evaluate(wide_stop, state, market_time)
    assert ReasonCode.RISK_PER_TRADE_BREACH in codes(decision)


def test_risk_basis_shrinks_after_losses(kernel, approved_draft, state, market_time):
    """Catch-up scaling is impossible: the same draft that fit at full equity
    must fail once equity has fallen far enough."""
    assert kernel.evaluate(approved_draft, state, market_time).approved
    state.apply_pnl(-0.029 * state.start_of_day_equity)
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert ReasonCode.DAILY_BUDGET_EXHAUSTED in codes(decision)


def test_rr_below_minimum_is_rejected(kernel, sizer, loss_gate, buy_intent, state, market_time):
    poor = replace(buy_intent, target_price=buy_intent.ref_price + 45)
    draft = sizer.draft(poor, state)
    assert draft is not None
    decision = kernel.evaluate(loss_gate.evaluate(draft), state, market_time)
    assert ReasonCode.RR_BELOW_MINIMUM in codes(decision)


def test_trade_count_cap_is_enforced(kernel, approved_draft, state, market_time, risk_config):
    state.trades_today = risk_config.quality_gate.max_trades_per_day
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert ReasonCode.MAX_TRADES_PER_DAY in codes(decision)


# --------------------------------------------------------------------------- #
# portfolio caps
# --------------------------------------------------------------------------- #


def test_operating_margin_blocks_oversized_new_risk(
    kernel, approved_draft, state, market_time, sizer, loss_gate
):
    """Two lots exceed the 20% operating band; stress band is not for new risk."""
    fat = loss_gate.evaluate(sizer.resize(approved_draft, lots=2))
    decision = kernel.evaluate(fat, state, market_time)
    assert (
        ReasonCode.MARGIN_UTILISATION_BREACH in codes(decision)
        or ReasonCode.STRESS_MARGIN_NEW_RISK in codes(decision)
        or ReasonCode.LOTS_OVER_ORDER_CAP in codes(decision)
    )


def test_correlated_cluster_cap_is_enforced(kernel, approved_draft, state, market_time):
    from alpha_desk.risk.state import Position

    # One position in the India index cluster consumes the whole beta budget.
    state.positions["BANKNIFTY"] = Position(
        symbol="BANKNIFTY",
        side=Side.BUY,
        lots=1,
        quantity=30,
        avg_price=56_000.0,
        correlation_cluster="INDIA_LARGECAP_INDEX",
        margin_pct=0.115,
    )
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert ReasonCode.CLUSTER_CONCENTRATION in codes(decision)


def test_max_concurrent_positions_is_enforced(kernel, approved_draft, state, market_time):
    from alpha_desk.risk.state import Position

    state.positions["OTHER"] = Position(
        symbol="OTHER", side=Side.BUY, lots=1, quantity=1, avg_price=1.0, margin_pct=0.01
    )
    decision = kernel.evaluate(approved_draft, state, market_time)
    assert ReasonCode.MAX_CONCURRENT_POSITIONS in codes(decision)


# --------------------------------------------------------------------------- #
# session windows
# --------------------------------------------------------------------------- #


def test_no_new_risk_after_cutoff(kernel, approved_draft, state):
    late = datetime(2026, 7, 24, 15, 5, tzinfo=IST)
    state.heartbeat(late)
    decision = kernel.evaluate(approved_draft, state, late)
    assert ReasonCode.NO_NEW_RISK_WINDOW in codes(decision)


def test_outside_session_is_rejected(kernel, approved_draft, state):
    after_hours = datetime(2026, 7, 24, 19, 0, tzinfo=IST)
    state.heartbeat(after_hours)
    decision = kernel.evaluate(approved_draft, state, after_hours)
    assert ReasonCode.OUTSIDE_SESSION in codes(decision)


def test_exit_is_allowed_after_cutoff(kernel, approved_draft, state):
    late = datetime(2026, 7, 24, 15, 25, tzinfo=IST)
    state.heartbeat(late)
    exit_draft = replace(
        approved_draft, intent=replace(approved_draft.intent, reduce_only=True)
    )
    decision = kernel.evaluate(exit_draft, state, late)
    assert decision.approved, decision.reasons


# --------------------------------------------------------------------------- #
# session roll
# --------------------------------------------------------------------------- #


def test_daily_halt_expires_next_session_but_drawdown_halt_does_not(state):
    from datetime import date

    state.engage_halt(HALT_DAILY_LOSS, "loss")
    state.engage_halt(HALT_DRAWDOWN, "dd")
    state.roll_session(date(2026, 7, 27))
    assert HALT_DAILY_LOSS not in state.halts
    assert HALT_DRAWDOWN in state.halts
