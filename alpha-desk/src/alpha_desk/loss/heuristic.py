"""Placeholder loss model for P1.0.

Baseline: for a driftless random walk the chance of touching a stop at
distance S before a target at distance T is T / (S + T). That baseline implies
exactly zero edge - expected value equals minus costs - which is the honest
prior before a model is trained.

The stub then shrinks that probability in proportion to the strategy's alpha
score, i.e. it *assumes* the skill that P1.2 is supposed to measure. That
assumption is fabricated. It exists so the order path can be exercised end to
end before a trained artifact exists, and `is_stub` is checked by the
promotion path so no evaluation can ever be run against it.
"""

from __future__ import annotations

from alpha_desk.core.types import DraftOrder


class HeuristicLossModel:
    model_id = "heuristic_loss_v0_STUB"
    is_stub = True

    def __init__(self, skill_per_score: float = 0.25, max_skill: float = 0.5) -> None:
        self._skill_per_score = skill_per_score
        self._max_skill = max_skill

    def p_hit_stop(self, draft: DraftOrder, context: dict[str, float]) -> float:
        stop_distance = abs(draft.entry_price - draft.stop_price)
        target_distance = abs(draft.target_price - draft.entry_price)
        if stop_distance <= 0 or target_distance <= 0:
            return 1.0

        baseline = target_distance / (stop_distance + target_distance)

        # Assumed skill (see module docstring - this is the fabricated part).
        skill = min(self._max_skill, self._skill_per_score * abs(draft.intent.alpha_score))
        p = baseline * (1.0 - skill)

        # Wide markets take out tight stops.
        atr = context.get("atr", 0.0)
        if atr > 0:
            crowding = min(2.0, atr / stop_distance)
            p += 0.15 * max(0.0, crowding - 1.0)

        if not draft.intent.regime_ok:
            p += 0.10

        return min(1.0, max(0.0, p))

    def expected_shortfall_inr(self, draft: DraftOrder, context: dict[str, float]) -> float:
        # Stops slip. Assume the realised loss overshoots the stop by a quarter
        # of the stop distance unless the caller supplies a measured overshoot.
        overshoot = context.get("stop_overshoot_fraction", 0.25)
        return draft.risk_inr * (1.0 + overshoot)
