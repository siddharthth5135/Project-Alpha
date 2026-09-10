"""The one and only route from a strategy intent to a broker order.

    Intent -> Portfolio draft -> Loss gate -> Risk Kernel -> Execution

Nothing may call the broker except this class, and this class cannot reach the
broker without a risk approval that itself required a loss advisory. Every
stage writes to the audit log, including the stages that say no.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from alpha_desk.audit.logger import AuditEvent, AuditLog
from alpha_desk.core.types import DraftOrder, Fill, Intent, RiskDecision
from alpha_desk.execution.base import Broker, BrokerError, OrderRequest
from alpha_desk.loss.interface import LossGate
from alpha_desk.portfolio.sizer import PortfolioSizer
from alpha_desk.risk.kernel import RiskKernel
from alpha_desk.risk.state import RiskState


class PathStatus(str, Enum):
    FILLED = "FILLED"
    NO_SIZE = "NO_SIZE"
    REJECTED_BY_RISK = "REJECTED_BY_RISK"
    BROKER_ERROR = "BROKER_ERROR"


@dataclass(frozen=True)
class PathResult:
    status: PathStatus
    intent: Intent
    draft: DraftOrder | None = None
    decision: RiskDecision | None = None
    fill: Fill | None = None
    detail: str = ""

    @property
    def executed(self) -> bool:
        return self.status is PathStatus.FILLED


class OrderPath:
    def __init__(
        self,
        sizer: PortfolioSizer,
        loss_gate: LossGate,
        kernel: RiskKernel,
        broker: Broker,
        audit: AuditLog,
    ) -> None:
        self._sizer = sizer
        self._loss = loss_gate
        self._kernel = kernel
        self._broker = broker
        self._audit = audit

    def submit(
        self,
        intent: Intent,
        state: RiskState,
        now: datetime,
        context: dict[str, float] | None = None,
    ) -> PathResult:
        self._audit.write(
            AuditEvent.SIGNAL,
            intent_id=intent.intent_id,
            symbol=intent.instrument.symbol,
            side=intent.side.value,
            strategy_id=intent.strategy_id,
            alpha_score=intent.alpha_score,
            ref_price=intent.ref_price,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
            regime_ok=intent.regime_ok,
            reduce_only=intent.reduce_only,
        )
        state.new_signals_today += 1

        draft = self._sizer.draft(intent, state)
        if draft is None:
            self._audit.write(
                AuditEvent.ORDER_REJECTED,
                intent_id=intent.intent_id,
                stage="PORTFOLIO",
                detail="no lot count fits the rupee risk budget",
            )
            return PathResult(
                status=PathStatus.NO_SIZE,
                intent=intent,
                detail="no lot count fits the rupee risk budget",
            )

        self._audit.write(
            AuditEvent.DRAFT,
            intent_id=intent.intent_id,
            draft_id=draft.draft_id,
            lots=draft.lots,
            quantity=draft.quantity,
            notional_inr=round(draft.notional_inr, 2),
            risk_inr=round(draft.risk_inr, 2),
            rr_after_costs=round(draft.rr_after_costs, 3),
            round_turn_cost_inr=round(draft.round_turn_cost_inr, 2),
        )

        draft = self._loss.evaluate(draft, context)
        advice = draft.loss_advice
        assert advice is not None  # LossGate always attaches advice
        self._audit.write(
            AuditEvent.LOSS_ADVICE,
            draft_id=draft.draft_id,
            action=advice.action.value,
            p_hit_stop=round(advice.p_hit_stop, 4),
            expected_shortfall_inr=round(advice.expected_shortfall_inr, 2),
            model_id=advice.model_id,
            reason=advice.reason,
            lots_after_advice=draft.lots,
        )

        decision = self._kernel.evaluate(draft, state, now)
        self._audit.write(
            AuditEvent.RISK_DECISION,
            draft_id=draft.draft_id,
            verdict=decision.verdict.value,
            approved_lots=decision.approved_lots,
            reasons=[{"code": r.code, "detail": r.detail} for r in decision.reasons],
            halts_triggered=list(decision.halts_triggered),
        )
        for halt in decision.halts_triggered:
            self._audit.write(AuditEvent.HALT, halt=halt, detail=state.halt_details.get(halt, ""))

        if not decision.approved:
            return PathResult(
                status=PathStatus.REJECTED_BY_RISK,
                intent=intent,
                draft=draft,
                decision=decision,
                detail="; ".join(r.code for r in decision.reasons),
            )

        request = OrderRequest.from_draft(draft.with_lots(decision.approved_lots), ts=now)
        self._audit.write(
            AuditEvent.ORDER_SENT,
            draft_id=draft.draft_id,
            symbol=request.symbol,
            side=request.side.value,
            lots=request.lots,
            quantity=request.quantity,
            limit_price=request.limit_price,
            mode=self._broker.mode.value,
        )

        try:
            fill = self._broker.place(request)
        except BrokerError as exc:
            self._audit.write(
                AuditEvent.ERROR, draft_id=draft.draft_id, stage="EXECUTION", detail=str(exc)
            )
            return PathResult(
                status=PathStatus.BROKER_ERROR,
                intent=intent,
                draft=draft,
                decision=decision,
                detail=str(exc),
            )

        state.register_fill(fill, instrument=intent.instrument)
        self._audit.write(
            AuditEvent.ORDER_FILLED,
            draft_id=draft.draft_id,
            order_id=fill.order_id,
            price=fill.price,
            quantity=fill.quantity,
            cost_inr=round(fill.cost_inr, 2),
        )
        return PathResult(
            status=PathStatus.FILLED,
            intent=intent,
            draft=draft,
            decision=decision,
            fill=fill,
        )

    def flatten(self, reason: str, state: RiskState) -> list[Fill]:
        fills = self._broker.flatten_all(reason)
        for fill in fills:
            # Closing fills must clear RiskState the same way entries registered it.
            # Otherwise the book looks flat at the broker while the kernel still
            # thinks margin is tied up — the next entry dies at the sizer.
            state.register_fill(fill)
        self._audit.write(AuditEvent.FLATTEN, reason=reason, closed=len(fills))
        return fills
