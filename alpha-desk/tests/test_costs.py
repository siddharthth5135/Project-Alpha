"""Cost model tests (playbook step P1.0.4).

Golden numbers are recomputed by hand from configs/costs_india.yaml. If a rate
in the YAML changes, these tests should fail loudly - that is the point.
"""

from __future__ import annotations

import pytest

from alpha_desk.core.types import Product, Side
from alpha_desk.costs.india import CostModel, UnverifiedCostsError


def test_futures_buy_leg_matches_hand_calculation(cost_model, nifty):
    price, qty = 24_000.0, 75  # one NIFTY lot, Rs 18,00,000 turnover
    cost = cost_model.leg_cost(
        product=Product.FUTURES,
        side=Side.BUY,
        price=price,
        quantity=qty,
        slippage_profile="index_futures",
        tick_size=0.05,
    )
    turnover = price * qty

    assert cost.brokerage == pytest.approx(20.0)          # flat beats 0.03%
    assert cost.stt == pytest.approx(0.0)                 # futures STT is sell-side
    assert cost.exchange_txn == pytest.approx(turnover * 0.0000173)
    assert cost.sebi == pytest.approx(turnover * 0.000001)
    assert cost.ipft == pytest.approx(turnover * 0.000001)
    assert cost.stamp_duty == pytest.approx(turnover * 0.00002)
    assert cost.gst == pytest.approx(
        (cost.brokerage + cost.exchange_txn + cost.sebi + cost.ipft) * 0.18
    )
    assert cost.slippage == pytest.approx(0.05 * qty + turnover * 0.0002)


def test_futures_sell_leg_pays_stt_and_no_stamp_duty(cost_model):
    cost = cost_model.leg_cost(
        product=Product.FUTURES, side=Side.SELL, price=24_000.0, quantity=75
    )
    assert cost.stt == pytest.approx(24_000.0 * 75 * 0.0002)
    assert cost.stamp_duty == pytest.approx(0.0)


def test_brokerage_uses_percentage_when_cheaper(cost_model):
    """A tiny ticket pays 0.03%, not the Rs 20 flat, subject to the Rs 2 floor."""
    cost = cost_model.leg_cost(
        product=Product.EQUITY_INTRADAY, side=Side.BUY, price=100.0, quantity=100
    )
    assert cost.brokerage == pytest.approx(max(10_000 * 0.0003, 2.0))


def test_brokerage_floor_applies(cost_model):
    cost = cost_model.leg_cost(
        product=Product.EQUITY_INTRADAY, side=Side.BUY, price=10.0, quantity=10
    )
    assert cost.brokerage == pytest.approx(2.0)


def test_options_charges_use_premium_rates(cost_model):
    cost = cost_model.leg_cost(
        product=Product.OPTIONS,
        side=Side.SELL,
        price=120.0,
        quantity=75,
        slippage_profile="options",
    )
    premium_turnover = 120.0 * 75
    assert cost.brokerage == pytest.approx(20.0)
    assert cost.stt == pytest.approx(premium_turnover * 0.001)
    assert cost.exchange_txn == pytest.approx(premium_turnover * 0.0003503)


def test_delivery_sell_pays_dp_charges(cost_model):
    cost = cost_model.leg_cost(
        product=Product.EQUITY_DELIVERY, side=Side.SELL, price=1_500.0, quantity=100
    )
    assert cost.dp_charges == pytest.approx(20.0)


def test_delivery_buy_has_no_dp_charges(cost_model):
    cost = cost_model.leg_cost(
        product=Product.EQUITY_DELIVERY, side=Side.BUY, price=1_500.0, quantity=100
    )
    assert cost.dp_charges == pytest.approx(0.0)


def test_round_turn_is_sum_of_both_legs(cost_model, nifty):
    entry = cost_model.leg_cost(
        product=Product.FUTURES, side=Side.BUY, price=24_000.0, quantity=75
    )
    exit_leg = cost_model.leg_cost(
        product=Product.FUTURES, side=Side.SELL, price=23_960.0, quantity=75
    )
    rt = cost_model.round_turn(
        instrument=nifty,
        entry_side=Side.BUY,
        entry_price=24_000.0,
        exit_price=23_960.0,
        quantity=75,
    )
    assert rt.total == pytest.approx(entry.total + exit_leg.total)


def test_round_turn_on_one_nifty_lot_is_material(cost_model, nifty):
    """Sanity band: a NIFTY round turn should cost roughly Rs 1,000-1,600.

    If this drifts far outside the band, either a rate is wrong or slippage was
    quietly tuned down to make a backtest look better.
    """
    total = cost_model.round_turn_inr(
        instrument=nifty, entry_side=Side.BUY, entry_price=24_000.0, quantity=75
    )
    assert 1_000 < total < 1_600, total


def test_costs_scale_with_size(cost_model, nifty):
    one = cost_model.round_turn_inr(
        instrument=nifty, entry_side=Side.BUY, entry_price=24_000.0, quantity=75
    )
    four = cost_model.round_turn_inr(
        instrument=nifty, entry_side=Side.BUY, entry_price=24_000.0, quantity=300
    )
    # Flat brokerage means cost per lot falls slightly as size grows.
    assert four < 4 * one
    assert four > 3.5 * one


def test_invalid_inputs_raise(cost_model):
    with pytest.raises(ValueError):
        cost_model.leg_cost(product=Product.FUTURES, side=Side.BUY, price=0.0, quantity=75)
    with pytest.raises(ValueError):
        cost_model.leg_cost(product=Product.FUTURES, side=Side.BUY, price=100.0, quantity=0)


def test_unverified_schedule_blocks_promotion(cost_config):
    model = CostModel(cost_config)
    assert not model.verified
    with pytest.raises(UnverifiedCostsError):
        model.require_verified()


def test_verified_schedule_allows_promotion(cost_config):
    model = CostModel(cost_config.model_copy(update={"verified_against_contract_note": True}))
    model.require_verified()
