"""The market-data port.

This is the only surface the rest of the desk is allowed to depend on. A
provider declares what it can do via :class:`Capability` so callers degrade
explicitly instead of discovering gaps as exceptions mid-backfill. Methods that
a provider cannot serve raise :class:`NotEntitledError`, they never return
silently empty frames.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Protocol, runtime_checkable

import pandas as pd

from alpha_desk.marketdata.contracts import (
    BarInterval,
    BarTimestampConvention,
    Contract,
    Segment,
    SessionStatus,
)


class Capability(str, Enum):
    HISTORICAL_BARS = "historical_bars"
    HISTORICAL_TICKS = "historical_ticks"
    HISTORICAL_OPEN_INTEREST = "historical_oi"
    HISTORICAL_OPTION_GREEKS = "historical_option_greeks"
    EXPIRED_CONTRACTS = "expired_contracts"
    CONTINUOUS_FUTURES = "continuous_futures"
    OPTION_CHAIN_SNAPSHOT = "option_chain_snapshot"
    OPTION_CHAIN_GREEKS = "option_chain_greeks"
    SYMBOL_MASTER = "symbol_master"
    EXPIRY_CALENDAR = "expiry_calendar"
    CORPORATE_ACTIONS = "corporate_actions"
    INDEX_CONSTITUENTS = "index_constituents"
    BHAVCOPY = "bhavcopy"
    LIVE_TRADES = "live_trades"
    LIVE_BID_ASK = "live_bid_ask"
    LIVE_BARS = "live_bars"
    LIVE_GREEKS = "live_greeks"
    MARKET_STATUS = "market_status"


@dataclass(frozen=True)
class ProviderLimits:
    """Hard numbers that shape ingestion planning.

    Backfill planners read these instead of hardcoding vendor quirks, so a
    vendor swap changes chunk sizes automatically rather than silently
    truncating history.
    """

    max_concurrent_live_symbols: int = 0
    max_bars_per_request: int | None = None
    max_ticks_per_request: int | None = None
    max_history_days: dict[BarInterval, int] = field(default_factory=dict)
    rest_calls_per_minute: int | None = None
    bar_convention: BarTimestampConvention = BarTimestampConvention.INTERVAL_START
    supports_partial_day_ticks: bool = False

    def history_days(self, interval: BarInterval) -> int | None:
        return self.max_history_days.get(interval)


@dataclass(frozen=True)
class ProviderDescriptor:
    name: str
    capabilities: frozenset[Capability]
    segments: frozenset[Segment]
    limits: ProviderLimits = field(default_factory=ProviderLimits)
    research_only: bool = False

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, *capabilities: Capability) -> None:
        from alpha_desk.marketdata.errors import NotEntitledError

        missing = [c.value for c in capabilities if c not in self.capabilities]
        if missing:
            raise NotEntitledError(f"provider {self.name!r} lacks capability: {missing}")


LiveHandler = Callable[[object], None]


@runtime_checkable
class HistoricalDataProvider(Protocol):
    """Batch history. Every frame is normalised and carries provenance."""

    descriptor: ProviderDescriptor

    def fetch_bars(
        self,
        contract: Contract,
        interval: BarInterval,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame: ...

    def fetch_ticks(
        self,
        contract: Contract,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame: ...

    def fetch_option_chain(
        self,
        underlying: str,
        expiry: date,
        *,
        with_greeks: bool = False,
    ) -> pd.DataFrame: ...

    def fetch_symbol_master(self, segment: Segment) -> pd.DataFrame: ...

    def fetch_expiries(self, underlying: str, segment: Segment) -> list[date]: ...


@runtime_checkable
class LiveDataProvider(Protocol):
    """Streaming. Implementations own reconnection; callers own persistence."""

    descriptor: ProviderDescriptor

    def subscribe(self, contracts: Iterable[Contract]) -> None: ...

    def unsubscribe(self, contracts: Iterable[Contract]) -> None: ...

    def stream(self) -> Iterator[object]: ...

    def market_status(self) -> SessionStatus: ...

    def close(self) -> None: ...


@runtime_checkable
class MarketDataProvider(HistoricalDataProvider, LiveDataProvider, Protocol):
    """A vendor that serves both history and live data."""
