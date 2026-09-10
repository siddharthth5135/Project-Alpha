"""Vendor-agnostic market-data layer.

Import from here, never from a vendor package. The only module allowed to know
that TrueData exists is the adapter under ``alpha_desk.vendors``.
"""

from alpha_desk.marketdata.contracts import (
    BarInterval,
    BarTimestampConvention,
    ContinuationStyle,
    Contract,
    Exchange,
    MarketSession,
    OptionRight,
    Provenance,
    Segment,
    SessionStatus,
    contract_from_id,
)
from alpha_desk.marketdata.errors import (
    AuthError,
    DataQualityError,
    MarketDataError,
    NoDataError,
    NotEntitledError,
    ProviderConfigError,
    RateLimitError,
    SubscriptionLimitError,
    SymbolResolutionError,
    TransportError,
    UpstreamError,
)
from alpha_desk.marketdata.provider import (
    Capability,
    HistoricalDataProvider,
    LiveDataProvider,
    MarketDataProvider,
    ProviderDescriptor,
    ProviderLimits,
)
from alpha_desk.marketdata.quality import QualityReport, QualityThresholds, Verdict
from alpha_desk.marketdata.registry import available_providers, get_provider, register_provider

__all__ = [
    "AuthError",
    "BarInterval",
    "BarTimestampConvention",
    "Capability",
    "ContinuationStyle",
    "Contract",
    "DataQualityError",
    "Exchange",
    "HistoricalDataProvider",
    "LiveDataProvider",
    "MarketDataError",
    "MarketDataProvider",
    "MarketSession",
    "NoDataError",
    "NotEntitledError",
    "OptionRight",
    "Provenance",
    "ProviderConfigError",
    "ProviderDescriptor",
    "ProviderLimits",
    "QualityReport",
    "QualityThresholds",
    "RateLimitError",
    "Segment",
    "SessionStatus",
    "SubscriptionLimitError",
    "SymbolResolutionError",
    "TransportError",
    "UpstreamError",
    "Verdict",
    "available_providers",
    "contract_from_id",
    "get_provider",
    "register_provider",
]
