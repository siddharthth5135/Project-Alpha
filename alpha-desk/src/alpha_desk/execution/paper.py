"""Paper broker - simulated fills, real code path.

Fills are modelled pessimistically: the order crosses the spread and pays the
same slippage the cost model assumes, so paper results cannot flatter live
ones through an optimistic fill assumption.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alpha_desk.core.types import Fill, Instrument, Mode, Side
from alpha_desk.costs.india import CostModel
from alpha_desk.execution.base import Broker, BrokerError, OrderRequest
from alpha_desk.execution.rate_limiter import RateLimiter


class PaperBroker(Broker):
    mode = Mode.PAPER

    def __init__(
        self,
        cost_model: CostModel,
        instruments: dict[str, Instrument],
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._costs = cost_model
        self._instruments = instruments
        self._limiter = rate_limiter or RateLimiter(max_calls=10, per_seconds=1.0)
        self._connected = False
        self._open: dict[str, Fill] = {}
        self.fills: list[Fill] = []

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def place(self, request: OrderRequest) -> Fill:
        if not self._connected:
            raise BrokerError("paper broker is not connected")
        self._limiter.acquire()

        instrument = self._instruments.get(request.symbol)
        if instrument is None:
            raise BrokerError(f"unknown instrument {request.symbol!r}")

        ref_price = request.limit_price or 0.0
        if ref_price <= 0:
            raise BrokerError("paper fills need a reference price")

        fill_price = self._fill_price(instrument, request.side, ref_price)
        cost = self._costs.leg_cost(
            product=instrument.product,
            side=request.side,
            price=fill_price,
            quantity=request.quantity,
            slippage_profile=instrument.slippage_profile,
            tick_size=instrument.tick_size,
        )
        fill = Fill(
            order_id=f"PAPER-{uuid.uuid4().hex[:10]}",
            draft_id=request.draft_id,
            ts=request.ts,
            symbol=request.symbol,
            side=request.side,
            lots=request.lots,
            quantity=request.quantity,
            price=fill_price,
            cost_inr=cost.total,
            mode=self.mode,
        )
        self._open[fill.order_id] = fill
        self.fills.append(fill)
        return fill

    def cancel(self, order_id: str) -> None:
        self._open.pop(order_id, None)

    def flatten_all(self, reason: str) -> list[Fill]:
        closed: list[Fill] = []
        for order_id, fill in list(self._open.items()):
            instrument = self._instruments[fill.symbol]
            exit_side = fill.side.opposite
            price = self._fill_price(instrument, exit_side, fill.price)
            cost = self._costs.leg_cost(
                product=instrument.product,
                side=exit_side,
                price=price,
                quantity=fill.quantity,
                slippage_profile=instrument.slippage_profile,
                tick_size=instrument.tick_size,
            )
            closed.append(
                Fill(
                    order_id=f"PAPER-FLAT-{uuid.uuid4().hex[:8]}",
                    draft_id=fill.draft_id,
                    ts=datetime.now(timezone.utc),
                    symbol=fill.symbol,
                    side=exit_side,
                    lots=fill.lots,
                    quantity=fill.quantity,
                    price=price,
                    cost_inr=cost.total,
                    mode=self.mode,
                )
            )
            del self._open[order_id]
        self.fills.extend(closed)
        return closed

    @property
    def open_orders(self) -> dict[str, Fill]:
        return dict(self._open)

    def _fill_price(self, instrument: Instrument, side: Side, ref_price: float) -> float:
        """Always fill one tick against us."""
        adverse_tick = instrument.tick_size * (1 if side is Side.BUY else -1)
        return round(ref_price + adverse_tick, 2)
