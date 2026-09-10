"""Continuous futures / roll tests (playbook step P1.1.2).

Exit gate: no look-ahead across expiry, and a spike audit around roll dates.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from alpha_desk.data.continuous import (
    ContinuousSeriesError,
    RollRule,
    assert_returns_are_roll_invariant,
    build_continuous,
    select_front_contract,
    synthetic_contracts,
)

EXPIRIES = [date(2026, 1, 29), date(2026, 2, 26), date(2026, 3, 26)]


@pytest.fixture(scope="module")
def contracts():
    return synthetic_contracts(start="2026-01-05 09:15", periods=900, expiries=EXPIRIES)


def test_front_contract_switches_before_expiry(contracts):
    rule = RollRule(days_before_expiry=2)
    front = select_front_contract(contracts, rule)
    for _, row in front.iterrows():
        days_left = (row["expiry"] - row["ts"].date()).days
        assert days_left > rule.days_before_expiry or row["expiry"] == max(EXPIRIES)


def test_roll_happens_and_is_flagged(contracts):
    series = build_continuous(contracts)
    assert series["is_roll"].sum() >= 1
    assert not bool(series["is_roll"].iloc[0])


def test_first_contract_is_the_nearest_expiry(contracts):
    series = build_continuous(contracts)
    assert series["expiry"].iloc[0] == min(EXPIRIES)


def test_returns_are_invariant_to_later_contracts(contracts):
    assert_returns_are_roll_invariant(contracts)


def test_roll_gap_never_enters_the_return_series(contracts):
    """The basis jump between contracts is a bookkeeping artefact, not a return.
    Every return must stay within the plausible range of the underlying."""
    series = build_continuous(contracts)
    returns = series["ret"].dropna()
    assert returns.abs().max() < 0.01, returns.abs().max()


def test_raw_close_does_show_the_gap(contracts):
    """Confirms the previous test is meaningful: the naive close-to-close series
    really does contain a jump that we are excluding."""
    series = build_continuous(contracts)
    naive = series["close"].pct_change()
    roll_rows = series["is_roll"].to_numpy()
    assert naive[roll_rows].abs().max() > series["ret"].dropna().abs().max()


def test_back_adjustment_removes_the_gap(contracts):
    series = build_continuous(contracts, adjust="difference")
    adjusted_jumps = series["adj_close"].diff()[series["is_roll"].to_numpy()]
    raw_jumps = series["close"].diff()[series["is_roll"].to_numpy()]
    assert adjusted_jumps.abs().max() < raw_jumps.abs().max()


def test_unadjusted_mode_leaves_close_alone(contracts):
    series = build_continuous(contracts, adjust="none")
    assert np.allclose(series["adj_close"], series["close"])


def test_volume_rule_picks_the_liquid_contract(contracts):
    """Synthetic volume favours the near contract, so both rules should agree
    on the front month away from roll windows."""
    by_date = select_front_contract(contracts, RollRule("days_to_expiry", 2))
    by_volume = select_front_contract(contracts, RollRule("volume", 2))
    agreement = (by_date["contract"].to_numpy() == by_volume["contract"].to_numpy()).mean()
    assert agreement > 0.9


def test_unknown_roll_method_is_refused():
    with pytest.raises(ValueError, match="unknown roll method"):
        RollRule(method="vibes")


def test_unknown_adjustment_is_refused(contracts):
    with pytest.raises(ValueError, match="unknown adjustment"):
        build_continuous(contracts, adjust="magic")


def test_missing_columns_are_refused(contracts):
    with pytest.raises(ContinuousSeriesError, match="missing columns"):
        build_continuous(contracts.drop(columns=["expiry"]))


def test_single_contract_cannot_prove_invariance(contracts):
    one = contracts[contracts["expiry"] == min(EXPIRIES)]
    with pytest.raises(ContinuousSeriesError, match="at least two contracts"):
        assert_returns_are_roll_invariant(one)


def test_contracts_never_trade_after_expiry(contracts):
    assert (contracts["ts"].dt.date <= contracts["expiry"]).all()
