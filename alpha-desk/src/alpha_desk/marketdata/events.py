"""Vendor-neutral live stream event types.

Ingestion and paper paths depend on these shapes only. Adapters (TrueData,
Angel One, …) emit the same kinds so a provider swap does not rewrite the desk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EventKind(str, Enum):
    TICK = "tick"
    BID_ASK = "bid_ask"
    BAR = "bar"
    GREEKS = "greeks"
    TOUCHLINE = "touchline"
    HEARTBEAT = "heartbeat"
    MARKET_STATUS = "market_status"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNROUTED = "unrouted"


@dataclass(frozen=True)
class StreamEvent:
    kind: EventKind
    payload: object
    received_at: datetime
    latency_s: float | None = None
    vendor_token: str = ""
