"""Trial universe: options first, live watchlist drops expired contracts."""

from __future__ import annotations

from datetime import date

from alpha_desk.ingest.universe import build_trial_universe
from alpha_desk.vendors.truedata.mock import MOCK_EXPIRIES, build_mock_provider


def test_universe_puts_options_ahead_of_futures_and_cash():
    provider = build_mock_provider()
    universe = build_trial_universe(
        provider, expiries_ahead=1, strikes_each_side=1, today=date(2025, 8, 14)
    )
    assert universe.options
    assert universe.continuous_futures
    assert universe.equities
    assert universe.dated_futures

    history = universe.history_targets()
    first_option = next(i for i, c in enumerate(history) if c.segment.value == "OPT")
    first_future = next(i for i, c in enumerate(history) if c.segment.value == "FUT")
    first_equity = next(i for i, c in enumerate(history) if c.segment.value == "EQ")
    assert first_option < first_future < first_equity


def test_live_watchlist_drops_expired_contracts_and_respects_cap():
    provider = build_mock_provider()
    universe = build_trial_universe(
        provider,
        expiries_ahead=1,
        expired_behind=1,
        strikes_each_side=2,
        today=date(2025, 9, 1),
    )
    expired = [c for c in universe.options if c.expiry and c.expiry < date(2025, 9, 1)]
    assert expired  # history still keeps the August weekly
    watchlist = universe.live_watchlist(limit=8)
    assert all(c.expiry is None or c.expiry >= date(2025, 9, 1) for c in watchlist)
    assert len(watchlist) <= 8


def test_expired_behind_keeps_recent_history_when_today_is_after_listed_expiries():
    provider = build_mock_provider()
    universe = build_trial_universe(
        provider, expiries_ahead=2, expired_behind=2, today=date(2026, 8, 15)
    )
    assert {c.expiry for c in universe.dated_futures} == set(MOCK_EXPIRIES)
    assert universe.options
    assert universe.live_watchlist(50)  # continuous + cash still subscribe


def test_last_close_anchors_the_atm_ladder():
    provider = build_mock_provider()
    spot = provider.spot_contract("NIFTY")
    close = provider.last_close(spot)
    assert close > 0
    universe = build_trial_universe(
        provider, expiries_ahead=1, strikes_each_side=1, today=date(2025, 8, 14)
    )
    strikes = sorted({c.strike for c in universe.options if c.symbol == "NIFTY"})
    assert min(strikes) <= close <= max(strikes)
