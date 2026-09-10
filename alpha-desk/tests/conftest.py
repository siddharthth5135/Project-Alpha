from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alpha_desk.config.settings import (
    CONFIG_DIR,
    AppSettings,
    load_cost_config,
    load_instrument_master,
    load_risk_config,
    load_strategy_spec,
)
from alpha_desk.audit.logger import AuditLog
from alpha_desk.core.instruments import build_all
from alpha_desk.core.types import Intent, Mode, Side
from alpha_desk.costs.india import CostModel
from alpha_desk.execution.paper import PaperBroker
from alpha_desk.execution.rate_limiter import RateLimiter
from alpha_desk.loss.heuristic import HeuristicLossModel
from alpha_desk.loss.interface import LossGate
from alpha_desk.pipeline.order_path import OrderPath
from alpha_desk.portfolio.sizer import PortfolioSizer
from alpha_desk.risk.kernel import RiskKernel
from alpha_desk.risk.killswitch import KillSwitch
from alpha_desk.risk.state import RiskState

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def risk_config():
    return load_risk_config()


@pytest.fixture
def strategy_spec():
    return load_strategy_spec()


@pytest.fixture
def cost_config():
    return load_cost_config()


@pytest.fixture
def cost_model(cost_config):
    return CostModel(cost_config)


@pytest.fixture
def instruments():
    return build_all(load_instrument_master())


@pytest.fixture
def nifty(instruments):
    return instruments["NIFTY"]


@pytest.fixture
def banknifty(instruments):
    return instruments["BANKNIFTY"]


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        mode=Mode.PAPER,
        data_root=tmp_path / "data",
        artifact_root=tmp_path / "artifacts",
        log_root=tmp_path / "logs",
    )


@pytest.fixture
def kill_switch(tmp_path: Path) -> KillSwitch:
    return KillSwitch(tmp_path / "state")


@pytest.fixture
def kernel(risk_config, kill_switch, settings) -> RiskKernel:
    return RiskKernel(risk_config, kill_switch, settings)


@pytest.fixture
def market_time() -> datetime:
    """A normal mid-session moment: 2026-07-24 11:00 IST (a Friday)."""
    return datetime(2026, 7, 24, 11, 0, tzinfo=IST)


@pytest.fixture
def state(risk_config) -> RiskState:
    s = RiskState.fresh(
        risk_config.capital.account_equity_inr, session_date=date(2026, 7, 24)
    )
    s.heartbeat(datetime(2026, 7, 24, 11, 0, tzinfo=IST))
    return s


@pytest.fixture
def sizer(risk_config, cost_model) -> PortfolioSizer:
    return PortfolioSizer(risk_config, cost_model)


@pytest.fixture
def loss_gate(risk_config, sizer) -> LossGate:
    return LossGate(HeuristicLossModel(), risk_config, resizer=sizer.resize)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "logs" / "audit")


@pytest.fixture
def paper_broker(cost_model, instruments) -> PaperBroker:
    broker = PaperBroker(cost_model, instruments, RateLimiter(max_calls=100, per_seconds=1.0))
    broker.connect()
    return broker


@pytest.fixture
def order_path(sizer, loss_gate, kernel, paper_broker, audit) -> OrderPath:
    return OrderPath(sizer, loss_gate, kernel, paper_broker, audit)


@pytest.fixture
def buy_intent(nifty, market_time) -> Intent:
    """Long NIFTY: 40-point stop, 160-point target, R:R 4 before costs.

    The alpha score is high enough that the stub loss model leaves expected
    value headroom, so this intent is the happy path through the whole stack.
    """
    return Intent(
        ts=market_time,
        instrument=nifty,
        side=Side.BUY,
        ref_price=24_000.0,
        stop_price=23_960.0,
        target_price=24_160.0,
        strategy_id="A1_INDEX_REGIME_V0",
        alpha_score=1.5,
    )


@pytest.fixture
def approved_draft(sizer, loss_gate, buy_intent, state):
    """A draft that has been sized and has passed the loss gate."""
    draft = sizer.draft(buy_intent, state)
    assert draft is not None
    return loss_gate.evaluate(draft)
