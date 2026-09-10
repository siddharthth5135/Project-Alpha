"""Vendor-neutral market-data failures.

Callers must be able to branch on *why* a call failed without importing a
vendor SDK or string-matching an error message. Adapters translate every
transport, HTTP and protocol failure into exactly one of these.
"""

from __future__ import annotations


class MarketDataError(RuntimeError):
    """Base class for every failure raised out of the market-data layer."""

    retryable = False


class ProviderConfigError(MarketDataError):
    """Missing or contradictory configuration. Never retryable."""


class AuthError(MarketDataError):
    """Credentials rejected, token expired, or session revoked."""


class TransportError(MarketDataError):
    """Network-level failure: DNS, TCP, TLS, timeout, socket closed."""

    retryable = True


class UpstreamError(MarketDataError):
    """Vendor returned 5xx or an unparseable body."""

    retryable = True


class RateLimitError(MarketDataError):
    """Vendor throttled us, or our own limiter refused the call."""

    retryable = True

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NotEntitledError(MarketDataError):
    """Subscription does not cover this segment, symbol count, or history depth.

    Distinct from AuthError on purpose: during a trial this is the expected
    answer for out-of-scope requests and must not trigger credential rotation.
    """


class NoDataError(MarketDataError):
    """Request was valid and served, but the vendor has no rows for it."""


class SymbolResolutionError(MarketDataError):
    """A canonical contract cannot be expressed in this vendor's symbology."""


class DataQualityError(MarketDataError):
    """Payload parsed but violates an invariant we refuse to store."""


class SubscriptionLimitError(MarketDataError):
    """Live subscription would exceed the licensed concurrent-symbol count."""
