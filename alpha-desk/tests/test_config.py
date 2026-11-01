"""Config invariants (playbook LAWs enforced at load time)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from alpha_desk.config.settings import (
    CONFIG_DIR,
    AppSettings,
    RiskConfig,
    StrategySpec,
    load_instrument_master,
    load_risk_config,
    load_strategy_spec,
)
from alpha_desk.core.types import Mode


def _risk_dict() -> dict:
    return yaml.safe_load((CONFIG_DIR / "risk_config.yaml").read_text(encoding="utf-8"))


def test_shipped_configs_load(risk_config, cost_config):
    assert risk_config.version
    assert cost_config.broker == "ANGEL_ONE"


def test_daily_loss_above_hard_ceiling_is_refused():
    raw = _risk_dict()
    raw["daily_loss"]["max_daily_loss_pct"] = 9.0
    with pytest.raises(ValueError, match="hard ceiling"):
        RiskConfig.model_validate(raw)


def test_per_trade_risk_above_hard_ceiling_is_refused():
    raw = _risk_dict()
    raw["per_trade"]["max_risk_per_trade_pct"] = 5.0
    with pytest.raises(ValueError):
        RiskConfig.model_validate(raw)


def test_return_target_scaling_cannot_be_enabled():
    raw = _risk_dict()
    raw["invariants"]["no_return_target_scaling"] = False
    with pytest.raises(ValueError, match="invariant"):
        RiskConfig.model_validate(raw)


def test_llm_cannot_be_placed_in_the_trading_path():
    raw = _risk_dict()
    raw["invariants"]["llm_in_trading_path"] = True
    with pytest.raises(ValueError, match="invariant"):
        RiskConfig.model_validate(raw)


def test_loss_gate_cannot_be_made_optional():
    raw = _risk_dict()
    raw["loss_gate"]["mandatory"] = False
    with pytest.raises(ValueError, match="optional"):
        RiskConfig.model_validate(raw)


def test_paper_gate_must_require_zero_uncaught_breaches():
    raw = _risk_dict()
    raw["paper_gate"]["max_uncaught_risk_breaches"] = 1
    with pytest.raises(ValueError, match="zero uncaught"):
        RiskConfig.model_validate(raw)


def test_unknown_config_key_is_refused():
    raw = _risk_dict()
    raw["surprise_setting"] = True
    with pytest.raises(ValueError):
        RiskConfig.model_validate(raw)


def test_session_times_must_be_ordered():
    raw = _risk_dict()
    raw["session"]["no_new_risk_after"] = "08:00"
    with pytest.raises(ValueError, match="ordered"):
        RiskConfig.model_validate(raw)


def test_random_kfold_promote_is_refused():
    raw = yaml.safe_load((CONFIG_DIR / "strategy_spec_001.yaml").read_text(encoding="utf-8"))
    raw["validation"]["random_kfold_promote"] = True
    with pytest.raises(ValueError, match="random k-fold"):
        StrategySpec.model_validate(raw)


def test_live_mode_refused_in_paper_only_phase(tmp_path: Path):
    with pytest.raises(ValueError, match="paper-only"):
        AppSettings(
            mode=Mode.LIVE, data_root=tmp_path, artifact_root=tmp_path, log_root=tmp_path
        )


def test_paper_mode_needs_no_signature(tmp_path: Path):
    settings = AppSettings(
        mode=Mode.PAPER, data_root=tmp_path, artifact_root=tmp_path, log_root=tmp_path
    )
    assert not settings.live_enabled


def test_shipped_risk_config_is_signed_by_hoqr(risk_config):
    assert risk_config.is_signed
    assert risk_config.signed_by.strip()
    assert risk_config.signed_on.strip()


def test_instrument_master_refuses_to_guess_lot_sizes():
    master = load_instrument_master()
    with pytest.raises(KeyError, match="not in the instrument master"):
        master.spec("RELIANCE")


def test_strategy_spec_matches_locked_decisions():
    spec = load_strategy_spec()
    assert spec.bars.primary_timeframe == "15m"
    assert spec.horizon.bars == 8
    assert spec.status == "RESEARCH"
    assert set(spec.universe.underlyings) == {"NIFTY", "BANKNIFTY"}


def test_risk_helpers_match_ic_rupee_limits(risk_config):
    """IC cut operating risk to 1% / Rs 10k and daily loss to 3% / Rs 30k."""
    equity = risk_config.capital.account_equity_inr
    assert equity == 1_000_000
    assert risk_config.max_daily_loss_inr(equity) == pytest.approx(30_000)
    assert risk_config.max_risk_per_trade_inr(equity) == pytest.approx(10_000)
    assert risk_config.max_risk_per_trade_inr(5_000_000) == pytest.approx(10_000)
    assert risk_config.portfolio.max_margin_utilisation_pct == 20.0
    assert risk_config.portfolio.stress_margin_utilisation_pct == 30.0
    assert risk_config.portfolio.max_concurrent_positions == 1
    assert risk_config.session.allow_overnight is False


def test_one_index_lot_fits_operating_margin_and_a_second_does_not(risk_config, instruments):
    """Operating 20% funds one lot at the research margin snapshot; two lots do not."""
    equity = risk_config.capital.account_equity_inr
    cap = equity * risk_config.portfolio.max_margin_utilisation_pct / 100.0
    for symbol, price in (("NIFTY", 24_000.0), ("BANKNIFTY", 56_000.0)):
        instrument = instruments[symbol]
        one_lot = instrument.margin_inr(price, instrument.lot_size)
        assert one_lot <= cap, f"{symbol} cannot fund one lot under operating cap"
        assert 2 * one_lot > cap, f"{symbol} would allow a second lot under operating cap"


def test_instrument_lots_match_live_nse_circular(instruments):
    assert instruments["NIFTY"].lot_size == 65
    assert instruments["BANKNIFTY"].lot_size == 30
    assert instruments["FINNIFTY"].lot_size == 60


def test_margins_are_tagged_non_authoritative():
    master = load_instrument_master()
    assert not master.margins_are_authoritative
    assert "not_authoritative" in master.margin_source


def test_overnight_cannot_be_enabled_in_phase1():
    raw = _risk_dict()
    raw["session"]["allow_overnight"] = True
    with pytest.raises(ValueError, match="allow_overnight"):
        RiskConfig.model_validate(raw)


def test_stress_margin_below_operating_is_refused():
    raw = _risk_dict()
    raw["portfolio"]["stress_margin_utilisation_pct"] = 10.0
    with pytest.raises(ValueError, match="stress_margin"):
        RiskConfig.model_validate(raw)
