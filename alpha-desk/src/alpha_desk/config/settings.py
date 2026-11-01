"""Configuration loading with fail-closed validation.

Two rules drive this module:

1. A config that violates a desk invariant (return-target scaling, LLM in the
   trading path, daily loss above the hard ceiling) must refuse to load. A
   wrong number in YAML should stop the process, not reach the broker.
2. Live mode requires an explicit written GO signature in the environment.
   Absence of the signature is treated as "no", never as "default to paper".
"""

from __future__ import annotations

import os
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alpha_desk.core.types import Mode, Product

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "configs"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# risk config
# --------------------------------------------------------------------------- #


class CapitalConfig(_Base):
    account_equity_inr: float = Field(gt=0)
    first_live_capital_ceiling_inr: float = Field(ge=0)


class DailyLossConfig(_Base):
    max_daily_loss_pct: float = Field(gt=0)
    hard_ceiling_pct: float = Field(gt=0)
    basis: str = "start_of_day_equity"

    @model_validator(mode="after")
    def _within_ceiling(self) -> "DailyLossConfig":
        if self.max_daily_loss_pct > self.hard_ceiling_pct:
            raise ValueError(
                f"max_daily_loss_pct {self.max_daily_loss_pct} exceeds hard ceiling "
                f"{self.hard_ceiling_pct}"
            )
        return self


class DrawdownConfig(_Base):
    max_drawdown_halt_pct: float = Field(gt=0, le=50)
    basis: str = "peak_equity"
    resume_requires_written_signoff: bool = True


class PerTradeConfig(_Base):
    max_risk_per_trade_pct: float = Field(gt=0)
    hard_ceiling_pct: float = Field(gt=0)
    max_rupee_risk_per_trade: float = Field(gt=0)
    max_lots_per_order: int = Field(gt=0)
    max_lots_per_instrument: int = Field(gt=0)
    require_stop_loss: bool = True

    @model_validator(mode="after")
    def _within_ceiling(self) -> "PerTradeConfig":
        if self.max_risk_per_trade_pct > self.hard_ceiling_pct:
            raise ValueError("max_risk_per_trade_pct exceeds its hard ceiling")
        if self.max_lots_per_order > self.max_lots_per_instrument:
            raise ValueError("max_lots_per_order exceeds max_lots_per_instrument")
        return self


class PortfolioLimits(_Base):
    max_concurrent_positions: int = Field(gt=0)
    max_margin_utilisation_pct: float = Field(gt=0, le=100)  # operating / new risk
    stress_margin_utilisation_pct: float = Field(gt=0, le=100)
    max_single_instrument_margin_pct: float = Field(gt=0, le=100)
    max_correlated_cluster_positions: int = Field(gt=0)

    @model_validator(mode="after")
    def _stress_above_operating(self) -> "PortfolioLimits":
        if self.stress_margin_utilisation_pct < self.max_margin_utilisation_pct:
            raise ValueError(
                "stress_margin_utilisation_pct must be >= max_margin_utilisation_pct"
            )
        if self.max_single_instrument_margin_pct > self.max_margin_utilisation_pct:
            raise ValueError(
                "max_single_instrument_margin_pct cannot exceed operating margin cap"
            )
        return self


class QualityGate(_Base):
    min_rr_after_costs: float = Field(gt=0)
    premium_rr_tag: float = Field(gt=0)
    min_alpha_score_abs: float = Field(ge=0)
    max_new_signals_per_day: int = Field(gt=0)
    max_trades_per_day: int = Field(gt=0)


class LossGateConfig(_Base):
    mandatory: bool = True
    ev_safety_margin: float = Field(gt=0, lt=1)
    cut_band: float = Field(gt=0, lt=1)
    cut_fraction: float = Field(gt=0, lt=1)
    absolute_block_p_hit_stop: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "LossGateConfig":
        if self.ev_safety_margin + self.cut_band >= 1:
            raise ValueError("ev_safety_margin + cut_band must stay below 1")
        if not self.mandatory:
            raise ValueError("loss gate cannot be made optional (playbook LAW 7)")
        return self

    def thresholds(self, p_breakeven: float) -> tuple[float, float]:
        """Return (hold_at, cut_at) probabilities for a trade's break-even point."""
        cut_at = p_breakeven * (1.0 - self.ev_safety_margin)
        hold_at = p_breakeven * (1.0 - self.ev_safety_margin - self.cut_band)
        return hold_at, cut_at


class SessionConfig(_Base):
    timezone: str = "Asia/Kolkata"
    market_open: time
    market_close: time
    no_new_risk_after: time
    force_flatten_at: time
    allow_overnight: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> "SessionConfig":
        if not self.market_open < self.no_new_risk_after <= self.force_flatten_at <= self.market_close:
            raise ValueError("session times must be ordered open < no_new_risk <= flatten <= close")
        if self.allow_overnight:
            raise ValueError(
                "allow_overnight cannot be true in Phase 1 until an explicit gap-risk "
                "model is approved by HoQR (IC condition)"
            )
        return self


class OpsConfig(_Base):
    flatten_on_data_feed_loss: bool
    max_heartbeat_age_seconds: int = Field(gt=0)
    flatten_on_broker_disconnect: bool
    max_order_age_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def _must_flatten(self) -> "OpsConfig":
        if not self.flatten_on_data_feed_loss:
            raise ValueError("flatten_on_data_feed_loss cannot be disabled")
        return self


class Invariants(_Base):
    no_return_target_scaling: bool
    risk_kernel_veto_is_absolute: bool
    paper_and_live_share_code_path: bool
    llm_in_trading_path: bool

    @model_validator(mode="after")
    def _enforce(self) -> "Invariants":
        violations = []
        if not self.no_return_target_scaling:
            violations.append("no_return_target_scaling must be true")
        if not self.risk_kernel_veto_is_absolute:
            violations.append("risk_kernel_veto_is_absolute must be true")
        if not self.paper_and_live_share_code_path:
            violations.append("paper_and_live_share_code_path must be true")
        if self.llm_in_trading_path:
            violations.append("llm_in_trading_path must be false in Phase 1")
        if violations:
            raise ValueError("desk invariant violated: " + "; ".join(violations))
        return self


class PaperGate(_Base):
    min_sessions: int = Field(gt=0)
    min_closed_trades: int = Field(gt=0)
    max_uncaught_risk_breaches: int = Field(ge=0)
    max_implementation_shortfall_pct: float = Field(gt=0)
    min_audit_completeness_pct: float = Field(gt=0, le=100)

    @field_validator("max_uncaught_risk_breaches")
    @classmethod
    def _zero_breaches(cls, v: int) -> int:
        if v != 0:
            raise ValueError("paper gate must require zero uncaught risk breaches")
        return v


class RiskConfig(_Base):
    version: str
    signed_by: str = ""
    signed_on: str = ""
    capital: CapitalConfig
    daily_loss: DailyLossConfig
    drawdown: DrawdownConfig
    per_trade: PerTradeConfig
    portfolio: PortfolioLimits
    quality_gate: QualityGate
    loss_gate: LossGateConfig
    session: SessionConfig
    ops: OpsConfig
    invariants: Invariants
    paper_gate: PaperGate

    @property
    def is_signed(self) -> bool:
        return bool(self.signed_by.strip() and self.signed_on.strip())

    def max_daily_loss_inr(self, start_of_day_equity: float) -> float:
        return start_of_day_equity * self.daily_loss.max_daily_loss_pct / 100.0

    def max_risk_per_trade_inr(self, equity: float) -> float:
        pct_cap = equity * self.per_trade.max_risk_per_trade_pct / 100.0
        return min(pct_cap, self.per_trade.max_rupee_risk_per_trade)


# --------------------------------------------------------------------------- #
# cost config
# --------------------------------------------------------------------------- #


class BrokerageLeg(_Base):
    mode: str
    flat_inr: float = Field(ge=0)
    pct: float = Field(default=0.0, ge=0)
    min_inr: float = Field(default=0.0, ge=0)

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, v: str) -> str:
        if v not in {"min", "flat", "pct"}:
            raise ValueError(f"unknown brokerage mode {v!r}")
        return v


class SlippageProfile(_Base):
    ticks: int = Field(ge=0)
    bps: float = Field(ge=0)


class SlippageConfig(_Base):
    model: str
    index_futures: SlippageProfile
    stock_futures: SlippageProfile
    equity: SlippageProfile
    options: SlippageProfile

    def profile(self, name: str) -> SlippageProfile:
        try:
            return getattr(self, name)
        except AttributeError as exc:  # pragma: no cover - guarded by config validation
            raise KeyError(f"unknown slippage profile {name!r}") from exc


class CostConfig(_Base):
    broker: str
    exchange: str
    currency: str
    rates_as_of: str
    verified_against_contract_note: bool
    brokerage: dict[str, BrokerageLeg]
    stt: dict[str, dict[str, float]]
    exchange_txn_pct: dict[str, float]
    ipft_pct: dict[str, float]
    sebi_turnover_pct: float = Field(ge=0)
    stamp_duty_buy_pct: dict[str, float]
    gst_pct: float = Field(ge=0)
    dp_charges: dict[str, float]
    slippage: SlippageConfig

    @model_validator(mode="after")
    def _products_present(self) -> "CostConfig":
        required = {p.value for p in Product}
        missing = required - set(self.brokerage)
        if missing:
            raise ValueError(f"brokerage schedule missing products: {sorted(missing)}")
        return self


# --------------------------------------------------------------------------- #
# instrument master + strategy spec
# --------------------------------------------------------------------------- #


class InstrumentSpec(_Base):
    kind: str
    exchange: str
    lot_size: int = Field(gt=0)
    tick_size: float = Field(gt=0)
    margin_pct: float = Field(gt=0, le=100)
    slippage_profile: str
    correlation_cluster: str
    data_proxy: str


class ExpiryRules(_Base):
    roll_rule: str
    roll_days_before_expiry: int = Field(ge=0)
    note: str = ""


class InstrumentMaster(_Base):
    lots_as_of: str
    lots_source: str = ""
    authoritative_source: str
    margin_as_of: str = ""
    margin_source: str = "research_snapshot_not_authoritative"
    margin_note: str = ""
    instruments: dict[str, InstrumentSpec]
    expiry_rules: ExpiryRules

    def spec(self, symbol: str) -> InstrumentSpec:
        try:
            return self.instruments[symbol]
        except KeyError as exc:
            raise KeyError(
                f"{symbol!r} is not in the instrument master. Refusing to guess a lot size."
            ) from exc

    @property
    def margins_are_authoritative(self) -> bool:
        return self.margin_source == "broker_span_live"


class HorizonSpec(_Base):
    bars: int = Field(gt=0)
    label_definition: str


class BarSpec(_Base):
    primary_timeframe: str
    context_timeframe: str


class UniverseSpec(_Base):
    underlyings: list[str]
    product: str
    data_proxy: dict[str, str]


class ValidationSpec(_Base):
    scheme: str
    embargo_bars: int = Field(gt=0)
    random_kfold_promote: bool

    @field_validator("random_kfold_promote")
    @classmethod
    def _forbid(cls, v: bool) -> bool:
        if v:
            raise ValueError("random k-fold may never be a promote path (playbook LAW 6)")
        return v


class StrategySpec(_Base):
    model_config = ConfigDict(extra="allow", frozen=True)

    strategy_id: str
    status: str
    created: str
    universe: UniverseSpec
    bars: BarSpec
    horizon: HorizonSpec
    features: list[str]
    models: dict[str, str]
    validation: ValidationSpec

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        allowed = {"RESEARCH", "PAPER", "LIMITED_LIVE", "RETIRED"}
        if v not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return v


# --------------------------------------------------------------------------- #
# runtime settings (environment)
# --------------------------------------------------------------------------- #


class AppSettings(_Base):
    mode: Mode
    data_root: Path
    artifact_root: Path
    log_root: Path
    live_go_signature: str = ""
    broker_api_key: str = ""
    broker_client_code: str = ""

    @model_validator(mode="after")
    def _live_needs_signature(self) -> "AppSettings":
        if self.mode is Mode.LIVE:
            raise ValueError(
                "ALPHA_MODE=live is refused in paper-only phase. "
                "Live ORDER trading stays OFF until you manually re-enable later. "
                "Keep ALPHA_MODE=paper."
            )
        return self

    @property
    def live_enabled(self) -> bool:
        # Paper-only phase: never true.
        return False


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"config {path} must be a mapping")
    return data


def load_risk_config(path: Path | None = None) -> RiskConfig:
    return RiskConfig.model_validate(_read_yaml(path or CONFIG_DIR / "risk_config.yaml"))


def load_cost_config(path: Path | None = None) -> CostConfig:
    return CostConfig.model_validate(_read_yaml(path or CONFIG_DIR / "costs_india.yaml"))


def load_instrument_master(path: Path | None = None) -> InstrumentMaster:
    return InstrumentMaster.model_validate(_read_yaml(path or CONFIG_DIR / "instruments.yaml"))


def load_strategy_spec(path: Path | None = None) -> StrategySpec:
    return StrategySpec.model_validate(_read_yaml(path or CONFIG_DIR / "strategy_spec_001.yaml"))


def load_settings(env: dict[str, str] | None = None) -> AppSettings:
    """Read runtime settings from the environment (.env is loaded if present)."""
    if env is None:
        _load_dotenv()
        env = dict(os.environ)

    default_data = REPO_ROOT / "data"
    return AppSettings(
        mode=Mode(env.get("ALPHA_MODE", "paper").strip().lower()),
        data_root=Path(env.get("ALPHA_DATA_ROOT") or default_data),
        artifact_root=Path(env.get("ALPHA_ARTIFACT_ROOT") or REPO_ROOT / "artifacts"),
        log_root=Path(env.get("ALPHA_LOG_ROOT") or REPO_ROOT / "logs"),
        live_go_signature=env.get("ALPHA_LIVE_GO_SIGNATURE", ""),
        broker_api_key=env.get("ANGELONE_API_KEY", ""),
        broker_client_code=env.get("ANGELONE_CLIENT_CODE", ""),
    )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        return
    load_dotenv(REPO_ROOT / ".env", override=False)


@lru_cache(maxsize=1)
def load_all() -> tuple[AppSettings, RiskConfig, CostConfig, InstrumentMaster, StrategySpec]:
    return (
        load_settings(),
        load_risk_config(),
        load_cost_config(),
        load_instrument_master(),
        load_strategy_spec(),
    )
