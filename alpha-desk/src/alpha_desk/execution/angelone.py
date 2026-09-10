"""Angel One SmartAPI adapter - deliberately inert until Gate G5.

The class exists so the interface and the compliance checklist are settled
before credentials arrive. Constructing it without credentials raises;
constructing it outside live mode with a GO signature raises; placing an order
raises until the SmartAPI client is wired in (step P1.0.8).

Nothing here should be softened to "just test it quickly".
"""

from __future__ import annotations

from datetime import datetime

from alpha_desk.config.settings import AppSettings
from alpha_desk.core.types import Fill, Mode
from alpha_desk.execution.base import Broker, BrokerError, OrderRequest
from alpha_desk.execution.rate_limiter import RateLimiter


class AngelOneBroker(Broker):
    mode = Mode.LIVE

    # SmartAPI publishes per-second limits per endpoint; stay far below them.
    DEFAULT_LIMITER = (5, 1.0)

    def __init__(self, settings: AppSettings, rate_limiter: RateLimiter | None = None) -> None:
        if not settings.live_enabled:
            raise BrokerError(
                "AngelOneBroker refuses to construct without ALPHA_MODE=live and a "
                "HoQR GO signature. Live stays OFF."
            )
        if not settings.broker_api_key or not settings.broker_client_code:
            raise BrokerError("Angel One credentials are missing from the environment")

        self._settings = settings
        self._limiter = rate_limiter or RateLimiter(*self.DEFAULT_LIMITER)
        self._session = None

    def connect(self) -> None:
        raise NotImplementedError(
            "SmartAPI session wiring is step P1.0.8, scheduled after HoQR shares "
            "credentials and the static-IP plan is confirmed."
        )

    def is_connected(self) -> bool:
        return self._session is not None

    def place(self, request: OrderRequest) -> Fill:
        raise NotImplementedError("live order placement is blocked until Gate G5 is signed")

    def cancel(self, order_id: str) -> None:
        raise NotImplementedError("live cancel is blocked until Gate G5 is signed")

    def flatten_all(self, reason: str) -> list[Fill]:
        raise NotImplementedError("live flatten is blocked until Gate G5 is signed")

    @staticmethod
    def preflight_checklist() -> list[str]:
        """Operational items that must be true before the adapter is enabled."""
        return [
            "F&O segment enabled on the Angel One account",
            "SmartAPI app created; API key, client code, MPIN, TOTP secret in local .env",
            "Static IP whitelisted with the broker",
            "Algo/strategy registration completed as required for the account type",
            "Rate limiter verified against published SmartAPI limits",
            "Kill-switch drill rehearsed against the live adapter in a dry run",
            "Written HoQR GO signature recorded in ALPHA_LIVE_GO_SIGNATURE",
        ]
