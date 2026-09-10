"""End-to-end order path tests (playbook step P1.4.3 exit gate, pulled forward).

Proves that the loss gate, the risk kernel and the kill switch each stop
execution, and that every stage lands in the audit log.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from alpha_desk.audit.logger import AuditEvent
from alpha_desk.core.types import LossAction
from alpha_desk.loss.interface import LossGate
from alpha_desk.pipeline.order_path import OrderPath, PathStatus
from alpha_desk.risk.kernel import ReasonCode
from tests.conftest import IST


class _AlwaysSkip:
    model_id = "always_skip"

    def p_hit_stop(self, draft, context):
        return 0.99

    def expected_shortfall_inr(self, draft, context):
        return draft.risk_inr


def test_happy_path_reaches_a_fill(order_path, buy_intent, state, market_time, paper_broker):
    result = order_path.submit(buy_intent, state, market_time)
    assert result.status is PathStatus.FILLED, result.detail
    assert result.fill is not None
    assert len(paper_broker.fills) == 1
    assert state.open_position_count == 1


def test_fill_price_is_adverse_to_us(order_path, buy_intent, state, market_time):
    result = order_path.submit(buy_intent, state, market_time)
    assert result.fill.price > buy_intent.ref_price


def test_loss_gate_skip_blocks_execution(
    sizer, kernel, paper_broker, audit, risk_config, buy_intent, state, market_time
):
    gate = LossGate(_AlwaysSkip(), risk_config, resizer=sizer.resize)
    path = OrderPath(sizer, gate, kernel, paper_broker, audit)
    result = path.submit(buy_intent, state, market_time)
    assert result.status is PathStatus.REJECTED_BY_RISK
    assert ReasonCode.LOSS_GATE_BLOCKED in result.decision.reason_codes
    assert paper_broker.fills == []


def test_risk_veto_blocks_execution(order_path, buy_intent, state, market_time, paper_broker):
    """Drawdown halt, not daily loss: a daily-loss breach also empties the risk
    budget, so the sizer would stop the trade before the kernel ever sees it."""
    state.peak_equity = 1_200_000
    result = order_path.submit(buy_intent, state, market_time)
    assert result.status is PathStatus.REJECTED_BY_RISK
    assert ReasonCode.DRAWDOWN_BREACH in result.decision.reason_codes
    assert paper_broker.fills == []


def test_kill_switch_blocks_execution(
    order_path, kill_switch, buy_intent, state, market_time, paper_broker
):
    kill_switch.engage("drill", engaged_by="HoQR")
    result = order_path.submit(buy_intent, state, market_time)
    assert result.status is PathStatus.REJECTED_BY_RISK
    assert ReasonCode.KILL_SWITCH_ENGAGED in result.decision.reason_codes
    assert paper_broker.fills == []


def test_unaffordable_trade_produces_no_size(order_path, buy_intent, state, market_time):
    huge_stop = replace(buy_intent, stop_price=buy_intent.ref_price - 5_000)
    result = order_path.submit(huge_stop, state, market_time)
    assert result.status is PathStatus.NO_SIZE


def test_audit_log_records_every_stage(order_path, buy_intent, state, market_time, audit):
    order_path.submit(buy_intent, state, market_time)
    events = [r["event"] for r in audit.read_all()]
    for expected in (
        AuditEvent.SIGNAL,
        AuditEvent.DRAFT,
        AuditEvent.LOSS_ADVICE,
        AuditEvent.RISK_DECISION,
        AuditEvent.ORDER_SENT,
        AuditEvent.ORDER_FILLED,
    ):
        assert expected.value in events


def test_rejected_orders_are_audited_too(
    order_path, kill_switch, buy_intent, state, market_time, audit
):
    kill_switch.engage("drill")
    order_path.submit(buy_intent, state, market_time)
    events = [r["event"] for r in audit.read_all()]
    assert AuditEvent.RISK_DECISION.value in events
    assert AuditEvent.ORDER_SENT.value not in events


def test_second_entry_is_blocked_by_margin(order_path, buy_intent, state, market_time):
    first = order_path.submit(buy_intent, state, market_time)
    assert first.status is PathStatus.FILLED
    second = order_path.submit(
        replace(buy_intent, intent_id="second"), state, market_time
    )
    assert second.status in (PathStatus.NO_SIZE, PathStatus.REJECTED_BY_RISK)


def test_flatten_closes_open_orders(order_path, buy_intent, state, market_time, paper_broker):
    order_path.submit(buy_intent, state, market_time)
    assert paper_broker.open_orders
    assert state.open_position_count == 1
    closed = order_path.flatten("kill switch drill", state)
    assert len(closed) == 1
    assert paper_broker.open_orders == {}
    assert state.open_position_count == 0


def test_late_session_entry_is_refused(order_path, buy_intent, state):
    late = datetime(2026, 7, 24, 15, 10, tzinfo=IST)
    state.heartbeat(late)
    result = order_path.submit(replace(buy_intent, ts=late), state, late)
    assert result.status is PathStatus.REJECTED_BY_RISK
    assert ReasonCode.NO_NEW_RISK_WINDOW in result.decision.reason_codes
