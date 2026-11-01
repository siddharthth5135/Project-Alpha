"""Angel One SmartAPI *order* adapter — deliberately inert until Gate G5.

Market-data (candles / quotes) lives in ``alpha_desk.vendors.angelone`` and is
gated by ``ANGELONE_ALLOW_MARKETDATA``. This broker class is separate: constructing
it without live mode + GO signature raises; placing an order raises until wired.

Nothing here should be softened to "just test it quickly".
"""

from __future__ import annotations

from alpha_desk.config.settings import AppSettings
from alpha_desk.core.types import Fill, Mode
from alpha_desk.execution.base import Broker, BrokerError, OrderRequest
from alpha_desk.execution.rate_limiter import RateLimiter


class AngelOneBroker(Broker):
    """ORDER adapter — permanently disabled in paper-only phase.

    Market-data (quotes/candles) is separate (``vendors.angelone``).
    Live order placement will be rewritten only after paper gates pass and
    you explicitly ask for it. This class must not place orders.
    """

    mode = Mode.LIVE
    DEFAULT_LIMITER = (5, 1.0)

    def __init__(self, settings: AppSettings, rate_limiter: RateLimiter | None = None) -> None:
        del rate_limiter
        raise BrokerError(
            "Live ORDER trading is DISABLED (paper-only phase). "
            "AlphaDesk will not place Angel orders until you manually re-enable "
            "after paper gates + explicit permission. Market-data capture is separate."
        )

    def connect(self) -> None:
        raise NotImplementedError("live orders disabled (paper-only)")

    def is_connected(self) -> bool:
        return False

    def place(self, request: OrderRequest) -> Fill:
        raise NotImplementedError("live orders disabled (paper-only)")

    def cancel(self, order_id: str) -> None:
        raise NotImplementedError("live orders disabled (paper-only)")

    def flatten_all(self, reason: str) -> list[Fill]:
        raise NotImplementedError("live orders disabled (paper-only)")

    @staticmethod
    def preflight_checklist() -> list[str]:
        return [
            "PAPER-ONLY PHASE: AngelOneBroker intentionally disabled",
            "Do not enable live orders without HoQR written GO after paper gates",
            "Market-data may use Angel separately (ANGELONE_ALLOW_MARKETDATA)",
        ]
