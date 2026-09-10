"""Portfolio construction - deterministic draft sizing.

Fixed rupee risk per trade, converted to whole lots, then trimmed until every
cap holds. Phase 1 deliberately avoids Black-Litterman / HRP style machinery:
with one strategy and one cluster it would add failure modes without adding
information.

No LLM sits here, now or later. The optimiser is code.
"""

from __future__ import annotations

from alpha_desk.config.settings import RiskConfig
from alpha_desk.core.types import DraftOrder, Intent
from alpha_desk.costs.india import CostModel
from alpha_desk.risk.state import RiskState

_MAX_TRIM_ITERATIONS = 64


class PortfolioSizer:
    def __init__(self, config: RiskConfig, cost_model: CostModel) -> None:
        self._cfg = config
        self._costs = cost_model

    def draft(self, intent: Intent, state: RiskState) -> DraftOrder | None:
        """Return a sized draft, or None when no lot count fits the budget."""
        risk_per_unit = intent.stop_distance
        if risk_per_unit <= 0 or intent.ref_price <= 0:
            return None

        instrument = intent.instrument
        lot_size = instrument.lot_size
        budget = self._rupee_budget(state)
        if budget <= 0:
            return None

        # First pass ignores costs, then the trim loop pays for them.
        lots = int(budget // (risk_per_unit * lot_size))
        lots = min(lots, self._lot_cap(intent, state))
        if lots < 1:
            return None

        for _ in range(_MAX_TRIM_ITERATIONS):
            draft = self._build(intent, lots)
            if draft.risk_inr <= budget:
                return draft
            lots -= 1
            if lots < 1:
                return None
        return None

    def resize(self, draft: DraftOrder, lots: int) -> DraftOrder:
        """Rebuild a draft at a new lot count with costs recomputed.

        The loss gate calls this when it cuts size: fewer lots carry a higher
        per-lot cost because brokerage is partly flat, so reusing the old total
        would misstate both risk and R:R.
        """
        if lots < 1:
            raise ValueError("resize needs at least one lot")
        return self._build(draft.intent, lots)

    # ------------------------------------------------------------------ #

    def _build(self, intent: Intent, lots: int) -> DraftOrder:
        instrument = intent.instrument
        quantity = lots * instrument.lot_size
        cost = self._costs.round_turn_inr(
            instrument=instrument,
            entry_side=intent.side,
            entry_price=intent.ref_price,
            exit_price=intent.stop_price,
            quantity=quantity,
        )
        return DraftOrder(
            intent=intent,
            lots=lots,
            entry_price=intent.ref_price,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
            round_turn_cost_inr=cost,
        )

    def _rupee_budget(self, state: RiskState) -> float:
        pt = self._cfg.per_trade
        basis = state.risk_basis_equity
        pct_cap = basis * pt.max_risk_per_trade_pct / 100.0
        remaining_day = self._cfg.max_daily_loss_inr(state.start_of_day_equity) + min(
            0.0, state.day_pnl
        )
        return max(0.0, min(pct_cap, pt.max_rupee_risk_per_trade, remaining_day))

    def _lot_cap(self, intent: Intent, state: RiskState) -> int:
        pt, pf = self._cfg.per_trade, self._cfg.portfolio
        instrument = intent.instrument
        existing = state.positions.get(instrument.symbol)
        used = existing.lots if existing and existing.side is intent.side else 0

        margin_budget = (
            state.risk_basis_equity * pf.max_margin_utilisation_pct / 100.0
            - state.total_margin_inr
        )
        margin_per_lot = instrument.margin_inr(intent.ref_price, instrument.lot_size)
        lots_by_margin = int(margin_budget // margin_per_lot) if margin_per_lot > 0 else 0

        return max(
            0,
            min(pt.max_lots_per_order, pt.max_lots_per_instrument - used, lots_by_margin),
        )
