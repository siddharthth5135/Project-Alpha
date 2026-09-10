"""Shared value objects for the whole order path.

These are deliberately dumb data holders. Every decision lives in a module
that takes them as input, so a draft order can be reconstructed from the audit
log and replayed through the same code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum


class Mode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class Product(str, Enum):
    EQUITY_DELIVERY = "equity_delivery"
    EQUITY_INTRADAY = "equity_intraday"
    FUTURES = "futures"
    OPTIONS = "options"


class LossAction(str, Enum):
    HOLD = "HOLD"        # proceed at drafted size
    CUT = "CUT"          # proceed at reduced size
    SKIP = "SKIP"        # do not enter
    FLATTEN = "FLATTEN"  # close existing exposure


class Verdict(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    product: Product
    lot_size: int
    tick_size: float
    exchange: str = "NFO"
    # Approximate SPAN + exposure margin as a percentage of notional. The
    # broker's margin calculator is authoritative before live.
    margin_pct: float = 100.0
    slippage_profile: str = "index_futures"
    correlation_cluster: str = "UNCLUSTERED"
    expiry: str | None = None

    def __post_init__(self) -> None:
        if self.lot_size <= 0:
            raise ValueError(f"{self.symbol}: lot_size must be positive")
        if self.tick_size <= 0:
            raise ValueError(f"{self.symbol}: tick_size must be positive")
        if not 0 < self.margin_pct <= 100:
            raise ValueError(f"{self.symbol}: margin_pct must be in (0, 100]")

    def margin_inr(self, price: float, quantity: int) -> float:
        return price * quantity * self.margin_pct / 100.0


@dataclass(frozen=True)
class Intent:
    """What the strategy wants. Carries no size - sizing is a separate layer."""

    ts: datetime
    instrument: Instrument
    side: Side
    ref_price: float
    stop_price: float
    target_price: float
    strategy_id: str
    alpha_score: float = 0.0
    regime_ok: bool = True
    liquidity_ok: bool = True
    # Exits must stay possible when the desk is halted, so they are tagged.
    reduce_only: bool = False
    feature_set_hash: str = ""
    trial_id: str | None = None
    intent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    @property
    def stop_distance(self) -> float:
        return abs(self.ref_price - self.stop_price)

    @property
    def target_distance(self) -> float:
        return abs(self.target_price - self.ref_price)


@dataclass(frozen=True)
class CostEstimate:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    sebi: float = 0.0
    ipft: float = 0.0
    stamp_duty: float = 0.0
    gst: float = 0.0
    dp_charges: float = 0.0
    slippage: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.brokerage
            + self.stt
            + self.exchange_txn
            + self.sebi
            + self.ipft
            + self.stamp_duty
            + self.gst
            + self.dp_charges
            + self.slippage
        )

    def __add__(self, other: "CostEstimate") -> "CostEstimate":
        return CostEstimate(
            brokerage=self.brokerage + other.brokerage,
            stt=self.stt + other.stt,
            exchange_txn=self.exchange_txn + other.exchange_txn,
            sebi=self.sebi + other.sebi,
            ipft=self.ipft + other.ipft,
            stamp_duty=self.stamp_duty + other.stamp_duty,
            gst=self.gst + other.gst,
            dp_charges=self.dp_charges + other.dp_charges,
            slippage=self.slippage + other.slippage,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "brokerage": self.brokerage,
            "stt": self.stt,
            "exchange_txn": self.exchange_txn,
            "sebi": self.sebi,
            "ipft": self.ipft,
            "stamp_duty": self.stamp_duty,
            "gst": self.gst,
            "dp_charges": self.dp_charges,
            "slippage": self.slippage,
            "total": self.total,
        }


@dataclass(frozen=True)
class LossAdvice:
    """Output of the soft ML gate. Advisory - the kernel still has the veto."""

    action: LossAction
    p_hit_stop: float
    expected_shortfall_inr: float
    model_id: str
    reason: str = ""


@dataclass(frozen=True)
class DraftOrder:
    """A sized proposal. Immutable; layers return modified copies."""

    intent: Intent
    lots: int
    entry_price: float
    stop_price: float
    target_price: float
    round_turn_cost_inr: float
    loss_advice: LossAdvice | None = None
    draft_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    @property
    def instrument(self) -> Instrument:
        return self.intent.instrument

    @property
    def side(self) -> Side:
        return self.intent.side

    @property
    def quantity(self) -> int:
        return self.lots * self.instrument.lot_size

    @property
    def notional_inr(self) -> float:
        return self.quantity * self.entry_price

    @property
    def margin_inr(self) -> float:
        return self.instrument.margin_inr(self.entry_price, self.quantity)

    @property
    def gross_risk_inr(self) -> float:
        """Rupees lost if the stop fills, before costs."""
        return abs(self.entry_price - self.stop_price) * self.quantity

    @property
    def risk_inr(self) -> float:
        """Rupees lost if the stop fills, including round-turn costs."""
        return self.gross_risk_inr + self.round_turn_cost_inr

    @property
    def gross_reward_inr(self) -> float:
        return abs(self.target_price - self.entry_price) * self.quantity

    @property
    def reward_inr(self) -> float:
        return self.gross_reward_inr - self.round_turn_cost_inr

    @property
    def rr_after_costs(self) -> float:
        if self.risk_inr <= 0:
            return 0.0
        return self.reward_inr / self.risk_inr

    def with_lots(self, lots: int, round_turn_cost_inr: float | None = None) -> "DraftOrder":
        cost = self.round_turn_cost_inr if round_turn_cost_inr is None else round_turn_cost_inr
        return replace(self, lots=lots, round_turn_cost_inr=cost)

    def with_loss_advice(self, advice: LossAdvice) -> "DraftOrder":
        return replace(self, loss_advice=advice)


@dataclass(frozen=True)
class RiskReason:
    code: str
    detail: str


@dataclass(frozen=True)
class RiskDecision:
    verdict: Verdict
    approved_lots: int
    reasons: tuple[RiskReason, ...] = ()
    halts_triggered: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        return self.verdict is Verdict.APPROVED

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(r.code for r in self.reasons)


@dataclass(frozen=True)
class Fill:
    order_id: str
    draft_id: str
    ts: datetime
    symbol: str
    side: Side
    lots: int
    quantity: int
    price: float
    cost_inr: float
    mode: Mode
