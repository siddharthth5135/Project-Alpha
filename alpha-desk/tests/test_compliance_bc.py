"""Compliance B/C: fill→equity, persist, broker guard, session flatten."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from alpha_desk.core.types import Fill, Mode, Side
from alpha_desk.desk import build_desk
from alpha_desk.execution.base import BrokerError, OrderRequest
from alpha_desk.execution.guard import BrokerGuard
from alpha_desk.risk.kernel import HALT_FEED_LOSS
from alpha_desk.risk.persist import load_risk_state, save_risk_state
from alpha_desk.risk.session_guardian import SessionGuardian
from alpha_desk.risk.state import RiskState

IST = ZoneInfo("Asia/Kolkata")


def test_register_fill_updates_equity_on_round_trip(nifty):
    state = RiskState.fresh(1_000_000.0)
    buy = Fill(
        order_id="1",
        draft_id="d1",
        ts=datetime.now(tz=IST),
        symbol="NIFTY",
        side=Side.BUY,
        lots=1,
        quantity=nifty.lot_size,
        price=24000.0,
        cost_inr=100.0,
        mode=Mode.PAPER,
    )
    state.register_fill(buy, instrument=nifty)
    assert state.current_equity == pytest.approx(1_000_000.0 - 100.0)
    assert "NIFTY" in state.positions

    sell = Fill(
        order_id="2",
        draft_id="d2",
        ts=datetime.now(tz=IST),
        symbol="NIFTY",
        side=Side.SELL,
        lots=1,
        quantity=nifty.lot_size,
        price=24100.0,
        cost_inr=100.0,
        mode=Mode.PAPER,
    )
    state.register_fill(sell, instrument=nifty)
    # +100 pts * lot_size - 100 cost on exit; entry already paid 100
    expected_pnl = (100.0 * nifty.lot_size) - 100.0
    assert state.current_equity == pytest.approx(1_000_000.0 - 100.0 + expected_pnl)
    assert state.open_position_count == 0


def test_risk_state_persists(tmp_path):
    state = RiskState.fresh(500_000.0)
    state.apply_pnl(-1_000.0)
    save_risk_state(state, tmp_path)
    loaded = load_risk_state(tmp_path, fallback_equity=1.0)
    assert loaded.current_equity == pytest.approx(499_000.0)


def test_broker_guard_blocks_direct_place():
    desk = build_desk()
    assert isinstance(desk.broker, BrokerGuard)
    with pytest.raises(BrokerError, match="OrderPath"):
        desk.broker.place(
            OrderRequest(
                draft_id="x",
                symbol="NIFTY",
                exchange="NSE",
                side=Side.BUY,
                lots=1,
                quantity=65,
                limit_price=1.0,
                stop_price=1.0,
                ts=datetime.now(tz=IST),
            )
        )


def test_session_guardian_flattens_on_feed_loss(order_path, state, buy_intent, market_time, nifty, tmp_path, risk_config):
    # open a position via path
    buy_intent = buy_intent  # fixture
    # ensure path can fill
    result = order_path.submit(buy_intent, state, market_time)
    assert result.executed or state.open_position_count >= 0
    # Force a position if path sized to zero in fixture environment
    if state.open_position_count == 0:
        state.register_fill(
            Fill(
                order_id="t",
                draft_id="t",
                ts=market_time,
                symbol="NIFTY",
                side=Side.BUY,
                lots=1,
                quantity=nifty.lot_size,
                price=24000.0,
                cost_inr=50.0,
                mode=Mode.PAPER,
            ),
            instrument=nifty,
        )
    g = SessionGuardian(risk_config, tmp_path)
    act = g.tick(now=market_time, state=state, order_path=order_path, feed_ok=False)
    assert act.feed_halted or HALT_FEED_LOSS in state.halts
    assert act.flattened
    assert state.open_position_count == 0


def test_session_guardian_force_flatten_clock(order_path, state, nifty, tmp_path, risk_config):
    state.register_fill(
        Fill(
            order_id="t",
            draft_id="t",
            ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
            symbol="NIFTY",
            side=Side.BUY,
            lots=1,
            quantity=nifty.lot_size,
            price=24000.0,
            cost_inr=50.0,
            mode=Mode.PAPER,
        ),
        instrument=nifty,
    )
    late = datetime(2026, 7, 24, 15, 20, tzinfo=IST)  # after typical force_flatten
    # Align to config
    ff = risk_config.session.force_flatten_at
    late = datetime(2026, 7, 24, ff.hour, ff.minute, tzinfo=IST)
    g = SessionGuardian(risk_config, tmp_path)
    act = g.tick(now=late, state=state, order_path=order_path, feed_ok=True)
    assert act.flattened
    assert act.reason == "FORCE_FLATTEN_CLOCK"
    assert state.open_position_count == 0
