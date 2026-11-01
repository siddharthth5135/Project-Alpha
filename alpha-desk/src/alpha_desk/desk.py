"""Desk assembly - one function that wires the Phase 1 components together.

Scripts, tests and (later) the paper loop all build the desk the same way, so
there is no second code path where a component quietly goes missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alpha_desk.audit.logger import AuditEvent, AuditLog
from alpha_desk.config.settings import (
    AppSettings,
    CostConfig,
    InstrumentMaster,
    RiskConfig,
    StrategySpec,
    load_cost_config,
    load_instrument_master,
    load_risk_config,
    load_settings,
    load_strategy_spec,
)
from alpha_desk.core.instruments import build_all
from alpha_desk.core.types import Instrument, Mode
from alpha_desk.costs.india import CostModel
from alpha_desk.data.warehouse import ParquetWarehouse
from alpha_desk.execution.base import Broker
from alpha_desk.execution.guard import BrokerGuard
from alpha_desk.execution.paper import PaperBroker
from alpha_desk.execution.rate_limiter import RateLimiter
from alpha_desk.loss.heuristic import HeuristicLossModel
from alpha_desk.loss.interface import LossGate, LossModel
from alpha_desk.pipeline.order_path import OrderPath
from alpha_desk.portfolio.sizer import PortfolioSizer
from alpha_desk.registry.trial_registry import TrialRegistry
from alpha_desk.risk.kernel import RiskKernel
from alpha_desk.risk.killswitch import KillSwitch
from alpha_desk.risk.persist import load_risk_state, save_risk_state
from alpha_desk.risk.session_guardian import SessionGuardian
from alpha_desk.risk.state import RiskState


@dataclass
class Desk:
    settings: AppSettings
    risk_config: RiskConfig
    cost_config: CostConfig
    instrument_master: InstrumentMaster
    strategy_spec: StrategySpec
    instruments: dict[str, Instrument]
    cost_model: CostModel
    kill_switch: KillSwitch
    kernel: RiskKernel
    loss_gate: LossGate
    sizer: PortfolioSizer
    broker: Broker  # Guaranteed BrokerGuard in build_desk (no direct place)
    audit: AuditLog
    warehouse: ParquetWarehouse
    order_path: OrderPath
    state: RiskState
    guardian: SessionGuardian

    def registry(self) -> TrialRegistry:
        return TrialRegistry(self.settings.data_root / "registry" / "trials.sqlite")

    def save_state(self) -> None:
        save_risk_state(self.state, self.settings.data_root)

    def preflight(self) -> list[str]:
        """Facts a human should see before trusting a session. Empty list = clean."""
        warnings: list[str] = []
        if not self.cost_model.verified:
            warnings.append("cost schedule not reconciled against a contract note")
        if not self.risk_config.is_signed:
            warnings.append("risk_config.yaml is unsigned by HoQR")
        if not self.instrument_master.margins_are_authoritative:
            warnings.append(
                f"instrument margins are {self.instrument_master.margin_source!r} "
                "(broker SPAN not wired yet)"
            )
        if not self.risk_config.session.allow_overnight:
            warnings.append("overnight holds forbidden (Phase 1 flat-by-close policy)")
        if self.kill_switch.is_engaged():
            warnings.append(f"kill switch engaged: {self.kill_switch.reason()}")
        if self.settings.mode is Mode.LIVE:
            warnings.append("MODE IS LIVE")
        return warnings


def build_desk(
    *,
    settings: AppSettings | None = None,
    config_dir: Path | None = None,
    loss_model: LossModel | None = None,
    broker: Broker | None = None,
    equity: float | None = None,
) -> Desk:
    settings = settings or load_settings()
    cfg_dir = config_dir

    risk_config = load_risk_config(cfg_dir / "risk_config.yaml" if cfg_dir else None)
    cost_config = load_cost_config(cfg_dir / "costs_india.yaml" if cfg_dir else None)
    master = load_instrument_master(cfg_dir / "instruments.yaml" if cfg_dir else None)
    spec = load_strategy_spec(cfg_dir / "strategy_spec_001.yaml" if cfg_dir else None)

    instruments = build_all(master)
    cost_model = CostModel(cost_config)
    kill_switch = KillSwitch(settings.data_root / "state")
    kernel = RiskKernel(risk_config, kill_switch, settings)
    sizer = PortfolioSizer(risk_config, cost_model)
    gate = LossGate(loss_model or HeuristicLossModel(), risk_config, resizer=sizer.resize)
    audit = AuditLog(settings.log_root / "audit")

    if broker is None:
        if settings.mode is Mode.LIVE:
            raise RuntimeError(
                "live mode requires an explicitly constructed broker; "
                "build_desk will not create one for you"
            )
        inner = PaperBroker(cost_model, instruments, RateLimiter(max_calls=5, per_seconds=1.0))
        inner.connect()
        broker = BrokerGuard(inner)
    elif not isinstance(broker, BrokerGuard):
        # Tests may pass a raw PaperBroker into OrderPath via fixtures; if they
        # pass it as desk broker, wrap so desk.broker.place cannot skip the kernel.
        broker = BrokerGuard(broker)

    # OrderPath must hold the *inner* executable broker, not the guard.
    exec_broker = broker._inner if isinstance(broker, BrokerGuard) else broker
    state = load_risk_state(
        settings.data_root,
        fallback_equity=equity if equity is not None else risk_config.capital.account_equity_inr,
    )
    path = OrderPath(sizer, gate, kernel, exec_broker, audit)
    guardian = SessionGuardian(risk_config, settings.data_root)

    audit.write(
        AuditEvent.CONFIG_LOADED,
        mode=settings.mode.value,
        risk_config_version=risk_config.version,
        risk_config_signed=risk_config.is_signed,
        costs_verified=cost_model.verified,
        cost_rates_as_of=cost_config.rates_as_of,
        strategy_id=spec.strategy_id,
        strategy_status=spec.status,
        instruments=sorted(instruments),
        equity=state.current_equity,
    )

    desk = Desk(
        settings=settings,
        risk_config=risk_config,
        cost_config=cost_config,
        instrument_master=master,
        strategy_spec=spec,
        instruments=instruments,
        cost_model=cost_model,
        kill_switch=kill_switch,
        kernel=kernel,
        loss_gate=gate,
        sizer=sizer,
        broker=broker,
        audit=audit,
        warehouse=ParquetWarehouse(settings.data_root),
        order_path=path,
        state=state,
        guardian=guardian,
    )
    desk.save_state()
    return desk
