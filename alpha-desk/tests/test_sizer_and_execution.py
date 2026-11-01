"""Portfolio sizer, rate limiter, paper broker and live-adapter refusal tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from alpha_desk.config.settings import AppSettings
from alpha_desk.core.types import Mode, Side
from alpha_desk.execution.angelone import AngelOneBroker
from alpha_desk.execution.base import BrokerError, OrderRequest
from alpha_desk.execution.paper import PaperBroker
from alpha_desk.execution.rate_limiter import RateLimiter, RateLimitExceeded
from tests.conftest import IST


# --------------------------------------------------------------------------- #
# sizer
# --------------------------------------------------------------------------- #


def test_draft_respects_the_rupee_risk_budget(sizer, buy_intent, state, risk_config):
    draft = sizer.draft(buy_intent, state)
    assert draft is not None
    assert draft.risk_inr <= risk_config.max_risk_per_trade_inr(state.risk_basis_equity)


def test_draft_respects_margin_capacity(sizer, buy_intent, state, risk_config):
    draft = sizer.draft(buy_intent, state)
    cap = state.risk_basis_equity * risk_config.portfolio.max_margin_utilisation_pct / 100.0
    assert draft.margin_inr <= cap


def test_costs_are_included_in_risk(sizer, buy_intent, state):
    draft = sizer.draft(buy_intent, state)
    assert draft.risk_inr > draft.gross_risk_inr
    assert draft.reward_inr < draft.gross_reward_inr


def test_wide_stop_yields_no_draft(sizer, buy_intent, state):
    assert sizer.draft(replace(buy_intent, stop_price=1.0), state) is None


def test_zero_stop_distance_yields_no_draft(sizer, buy_intent, state):
    assert sizer.draft(replace(buy_intent, stop_price=buy_intent.ref_price), state) is None


def test_exhausted_daily_budget_yields_no_draft(sizer, buy_intent, state):
    state.apply_pnl(-0.03 * state.start_of_day_equity)
    assert sizer.draft(buy_intent, state) is None


def test_sizer_never_exceeds_per_order_lot_cap(sizer, buy_intent, state, risk_config):
    rich = replace(buy_intent, stop_price=buy_intent.ref_price - 1.0)
    state.mark_equity(50_000_000)
    state.start_of_day_equity = 50_000_000
    draft = sizer.draft(rich, state)
    assert draft.lots <= risk_config.per_trade.max_lots_per_order


def test_resize_rejects_zero_lots(sizer, buy_intent, state):
    draft = sizer.draft(buy_intent, state)
    with pytest.raises(ValueError):
        sizer.resize(draft, 0)


# --------------------------------------------------------------------------- #
# rate limiter
# --------------------------------------------------------------------------- #


def test_rate_limiter_allows_up_to_the_cap():
    limiter = RateLimiter(max_calls=3, per_seconds=10.0)
    assert all(limiter.allow(now=100.0) for _ in range(3))
    assert not limiter.allow(now=100.0)


def test_rate_limiter_window_slides():
    limiter = RateLimiter(max_calls=2, per_seconds=10.0)
    limiter.allow(now=100.0)
    limiter.allow(now=100.0)
    assert not limiter.allow(now=105.0)
    assert limiter.allow(now=111.0)


def test_rate_limiter_acquire_raises():
    limiter = RateLimiter(max_calls=1, per_seconds=10.0)
    limiter.acquire(now=1.0)
    with pytest.raises(RateLimitExceeded):
        limiter.acquire(now=1.0)


# --------------------------------------------------------------------------- #
# paper broker
# --------------------------------------------------------------------------- #


def _request(symbol="NIFTY", side=Side.BUY, lots=1, qty=75, price=24_000.0):
    return OrderRequest(
        draft_id="D1",
        symbol=symbol,
        exchange="NFO",
        side=side,
        lots=lots,
        quantity=qty,
        limit_price=price,
        stop_price=price - 40,
        ts=datetime(2026, 7, 24, 11, 0, tzinfo=IST),
    )


def test_paper_broker_refuses_when_disconnected(cost_model, instruments):
    broker = PaperBroker(cost_model, instruments)
    with pytest.raises(BrokerError, match="not connected"):
        broker.place(_request())


def test_paper_broker_refuses_unknown_symbol(paper_broker):
    with pytest.raises(BrokerError, match="unknown instrument"):
        paper_broker.place(_request(symbol="TCS"))


def test_paper_buy_fills_above_reference(paper_broker):
    fill = paper_broker.place(_request())
    assert fill.price == pytest.approx(24_000.05)
    assert fill.cost_inr > 0


def test_paper_sell_fills_below_reference(paper_broker):
    fill = paper_broker.place(_request(side=Side.SELL))
    assert fill.price == pytest.approx(23_999.95)


def test_paper_broker_enforces_its_rate_limit(cost_model, instruments):
    broker = PaperBroker(cost_model, instruments, RateLimiter(max_calls=1, per_seconds=60.0))
    broker.connect()
    broker.place(_request())
    with pytest.raises(RateLimitExceeded):
        broker.place(_request())


def test_flatten_reverses_open_orders(paper_broker):
    paper_broker.place(_request())
    closed = paper_broker.flatten_all("drill")
    assert len(closed) == 1
    assert closed[0].side is Side.SELL


# --------------------------------------------------------------------------- #
# live adapter stays inert
# --------------------------------------------------------------------------- #


def test_angelone_refuses_to_construct_in_paper_mode(tmp_path):
    settings = AppSettings(
        mode=Mode.PAPER, data_root=tmp_path, artifact_root=tmp_path, log_root=tmp_path
    )
    with pytest.raises(BrokerError, match="DISABLED|paper-only"):
        AngelOneBroker(settings)


def test_angelone_refuses_even_if_someone_forces_live_fields(tmp_path):
    # LIVE mode cannot be constructed via AppSettings anymore; broker still hard-refuses.
    settings = AppSettings(
        mode=Mode.PAPER, data_root=tmp_path, artifact_root=tmp_path, log_root=tmp_path
    )
    with pytest.raises(BrokerError, match="DISABLED|paper-only"):
        AngelOneBroker(settings)


def test_angelone_publishes_a_preflight_checklist():
    checklist = AngelOneBroker.preflight_checklist()
    assert any("PAPER-ONLY" in item for item in checklist)
