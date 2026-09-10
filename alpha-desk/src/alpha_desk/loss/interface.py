"""Loss Prediction gate - the soft checkpoint before the hard kernel.

The gate answers one question: on THIS size, is the adverse path likely enough
to make the trade a bad bet after costs? It may shrink or skip a trade. It may
never enlarge one, and it may never overrule the Risk Kernel.

Thresholds are relative to each trade's own break-even probability

    p_breakeven = reward_after_costs / (reward_after_costs + risk_after_costs)

because an absolute cutoff is meaningless: a real 1:4 setup is supposed to hit
its stop most of the time, while a 1:0.5 scalp needs to win almost always. The
gate therefore asks for expected-value headroom, not for a high win rate.

In Phase 1.0 the model behind it is a transparent heuristic. It is replaced by
a calibrated GBDT artifact at step P1.2.3 without touching this interface.
"""

from __future__ import annotations

from typing import Callable, Protocol

from alpha_desk.config.settings import RiskConfig
from alpha_desk.core.types import DraftOrder, LossAction, LossAdvice


class LossModel(Protocol):
    """Anything that can score the adverse path of a drafted order."""

    model_id: str

    def p_hit_stop(self, draft: DraftOrder, context: dict[str, float]) -> float:
        """Probability the stop is hit before the target, in [0, 1]."""

    def expected_shortfall_inr(self, draft: DraftOrder, context: dict[str, float]) -> float:
        """Expected rupee loss conditional on the adverse path."""


Resizer = Callable[[DraftOrder, int], DraftOrder]


def breakeven_probability(draft: DraftOrder) -> float:
    """Adverse probability at which this trade's expected value turns zero."""
    reward, risk = draft.reward_inr, draft.risk_inr
    if reward <= 0 or risk <= 0:
        return 0.0
    return reward / (reward + risk)


def expected_value_inr(draft: DraftOrder, p_hit_stop: float) -> float:
    return (1.0 - p_hit_stop) * draft.reward_inr - p_hit_stop * draft.risk_inr


class LossGate:
    def __init__(
        self,
        model: LossModel,
        config: RiskConfig,
        resizer: Resizer | None = None,
    ) -> None:
        self._model = model
        self._cfg = config
        # Cutting size changes the cost per lot, so the sizer rebuilds the draft
        # when it can. Without one we keep the larger cost, which overstates risk.
        self._resize: Resizer = resizer or (lambda draft, lots: draft.with_lots(lots))

    @property
    def model_id(self) -> str:
        return self._model.model_id

    def evaluate(self, draft: DraftOrder, context: dict[str, float] | None = None) -> DraftOrder:
        ctx = context or {}
        p = self._clamp(self._model.p_hit_stop(draft, ctx))
        es = self._model.expected_shortfall_inr(draft, ctx)
        gate = self._cfg.loss_gate

        p_be = breakeven_probability(draft)
        hold_at, cut_at = gate.thresholds(p_be)

        if p > gate.absolute_block_p_hit_stop:
            return self._skip(draft, p, es, f"p_hit_stop {p:.3f} above absolute backstop "
                              f"{gate.absolute_block_p_hit_stop}")

        if p_be <= 0:
            return self._skip(draft, p, es, "trade has no positive reward after costs")

        if p > cut_at:
            return self._skip(
                draft,
                p,
                es,
                f"p_hit_stop {p:.3f} leaves no EV headroom (break-even {p_be:.3f}, "
                f"cut threshold {cut_at:.3f})",
            )

        if p > hold_at:
            cut_lots = int(draft.lots * gate.cut_fraction)
            if cut_lots < 1:
                return self._skip(draft, p, es, "cut would fall below one lot, so skip instead")
            resized = self._resize(draft, cut_lots)
            advice = LossAdvice(
                action=LossAction.CUT,
                p_hit_stop=p,
                expected_shortfall_inr=es,
                model_id=self._model.model_id,
                reason=f"p_hit_stop {p:.3f} within cut band (hold below {hold_at:.3f}); "
                f"{draft.lots} -> {cut_lots} lots",
            )
            return resized.with_loss_advice(advice)

        advice = LossAdvice(
            action=LossAction.HOLD,
            p_hit_stop=p,
            expected_shortfall_inr=es,
            model_id=self._model.model_id,
            reason=f"EV headroom ok (p {p:.3f} <= hold threshold {hold_at:.3f})",
        )
        return draft.with_loss_advice(advice)

    def _skip(self, draft: DraftOrder, p: float, es: float, reason: str) -> DraftOrder:
        return draft.with_loss_advice(
            LossAdvice(
                action=LossAction.SKIP,
                p_hit_stop=p,
                expected_shortfall_inr=es,
                model_id=self._model.model_id,
                reason=reason,
            )
        )

    @staticmethod
    def _clamp(p: float) -> float:
        if p != p:  # NaN from a broken model must fail closed, not pass through
            return 1.0
        return min(1.0, max(0.0, p))
