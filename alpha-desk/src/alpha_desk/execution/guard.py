"""Prevent strategies from calling ``broker.place`` outside OrderPath."""

from __future__ import annotations

from alpha_desk.core.types import Fill, Mode
from alpha_desk.execution.base import Broker, BrokerError, OrderRequest


class BrokerGuard(Broker):
    """Public desk handle: inspect OK, direct place/flatten forbidden."""

    mode = Mode.PAPER

    def __init__(self, inner: Broker) -> None:
        self._inner = inner
        self.mode = inner.mode

    @property
    def inner_class_name(self) -> str:
        return type(self._inner).__name__

    def connect(self) -> None:
        self._inner.connect()

    def disconnect(self) -> None:
        if hasattr(self._inner, "disconnect"):
            self._inner.disconnect()

    def is_connected(self) -> bool:
        return self._inner.is_connected()

    def place(self, request: OrderRequest):
        raise BrokerError(
            "direct broker.place() is forbidden; submit only via OrderPath "
            "(compliance C3 / P1.4 funnel)"
        )

    def cancel(self, order_id: str) -> None:
        raise BrokerError("direct broker.cancel() is forbidden; use OrderPath")

    def flatten_all(self, reason: str):
        raise BrokerError(
            "direct broker.flatten_all() is forbidden; use OrderPath.flatten()"
        )

    def __getattr__(self, name: str):
        return getattr(self._inner, name)
