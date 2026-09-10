"""Broker interface shared by paper and live.

Paper and live differ by a flag and an adapter, never by a separate decision
path. Anything that would be true only in paper belongs in the adapter, not in
the strategy or risk layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from alpha_desk.core.types import DraftOrder, Fill, Mode, Side


class BrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderRequest:
    draft_id: str
    symbol: str
    exchange: str
    side: Side
    lots: int
    quantity: int
    limit_price: float | None
    stop_price: float
    ts: datetime
    tag: str = "ALPHA1"

    @classmethod
    def from_draft(cls, draft: DraftOrder, ts: datetime, limit_price: float | None = None):
        return cls(
            draft_id=draft.draft_id,
            symbol=draft.instrument.symbol,
            exchange=draft.instrument.exchange,
            side=draft.side,
            lots=draft.lots,
            quantity=draft.quantity,
            limit_price=limit_price if limit_price is not None else draft.entry_price,
            stop_price=draft.stop_price,
            ts=ts,
        )


class Broker(ABC):
    mode: Mode

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def place(self, request: OrderRequest) -> Fill: ...

    @abstractmethod
    def cancel(self, order_id: str) -> None: ...

    @abstractmethod
    def flatten_all(self, reason: str) -> list[Fill]: ...
