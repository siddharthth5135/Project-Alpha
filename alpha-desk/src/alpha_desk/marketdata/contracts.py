"""Canonical market-data value objects.

Every vendor is normalised into these types before anything else in the system
sees the data. Feature builders, labels, models, backtests and the paper loop
read only these shapes, so swapping a vendor is an adapter change and nothing
else. Vendor symbol strings never escape the adapter: they are carried as
``vendor_symbol`` provenance next to a stable ``contract_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

IST = "Asia/Kolkata"
SCHEMA_VERSION = "md-1"


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    MCX = "MCX"


class Segment(str, Enum):
    """Market segment, not the vendor's spelling of it."""

    EQUITY = "EQ"
    INDEX = "INDEX"
    FUTURES = "FUT"
    OPTIONS = "OPT"
    CURRENCY = "CDS"
    COMMODITY = "COM"


class OptionRight(str, Enum):
    CALL = "CE"
    PUT = "PE"


class ContinuationStyle(str, Enum):
    """How a continuous series was produced. Never guess this later."""

    NONE = "none"
    VENDOR_SPLICED = "vendor_spliced"
    VENDOR_ADJUSTED = "vendor_adjusted"
    DESK_ROLLED = "desk_rolled"


class BarInterval(str, Enum):
    M1 = "1m"
    M2 = "2m"
    M3 = "3m"
    M5 = "5m"
    M10 = "10m"
    M15 = "15m"
    M30 = "30m"
    M60 = "60m"
    EOD = "1d"
    TICK = "tick"

    @property
    def minutes(self) -> int:
        if self is BarInterval.TICK:
            raise ValueError("tick has no fixed duration")
        if self is BarInterval.EOD:
            return 1440
        return int(self.value.rstrip("m"))

    @property
    def is_intraday(self) -> bool:
        return self not in (BarInterval.EOD, BarInterval.TICK)


class BarTimestampConvention(str, Enum):
    """Whether a bar's timestamp marks the start or the end of its interval.

    TrueData stamps 09:15:00-09:15:59 as 09:15, i.e. interval start. Getting
    this wrong shifts every feature by one bar, so it is declared, stored and
    asserted rather than assumed.
    """

    INTERVAL_START = "interval_start"
    INTERVAL_END = "interval_end"


@dataclass(frozen=True)
class Contract:
    """One tradable (or historical) instrument in canonical form."""

    symbol: str
    segment: Segment
    exchange: Exchange = Exchange.NSE
    expiry: date | None = None
    strike: float | None = None
    right: OptionRight | None = None
    lot_size: int | None = None
    tick_size: float | None = None
    continuation: ContinuationStyle = ContinuationStyle.NONE
    vendor_symbol: str = ""
    vendor_token: str = ""

    def __post_init__(self) -> None:
        if self.segment is Segment.OPTIONS and (self.strike is None or self.right is None):
            raise ValueError(f"{self.symbol}: options need a strike and a right")
        if self.segment is not Segment.OPTIONS and (self.strike is not None or self.right):
            raise ValueError(f"{self.symbol}: only options carry strike/right")
        if self.segment in (Segment.FUTURES, Segment.OPTIONS):
            if self.expiry is None and self.continuation is ContinuationStyle.NONE:
                raise ValueError(
                    f"{self.symbol}: a derivative needs an expiry unless it is a continuous series"
                )
        if self.continuation is not ContinuationStyle.NONE and self.expiry is not None:
            raise ValueError(f"{self.symbol}: a continuous series has no single expiry")

    @property
    def contract_id(self) -> str:
        """Stable key. Two vendors describing the same contract agree here."""
        parts = [self.exchange.value, self.segment.value, self.symbol]
        if self.continuation is not ContinuationStyle.NONE:
            parts.append(f"CONT-{self.continuation.value}")
        if self.expiry is not None:
            parts.append(self.expiry.isoformat())
        if self.segment is Segment.OPTIONS:
            parts.append(f"{self.strike:g}")
            parts.append(self.right.value if self.right else "")
        return ":".join(parts)

    @property
    def is_continuous(self) -> bool:
        return self.continuation is not ContinuationStyle.NONE

    @property
    def is_expired_style(self) -> bool:
        """True when this row can only ever be history, never a live subscription."""
        return self.expiry is not None

    @property
    def storage_key(self) -> str:
        """Filesystem-safe partition key."""
        return self.contract_id.replace(":", "__").replace("/", "-")


def contract_from_id(contract_id: str) -> Contract:
    """Inverse of :attr:`Contract.contract_id`. Used by training scripts."""
    parts = contract_id.split(":")
    if len(parts) < 3:
        raise ValueError(f"unrecognised contract_id {contract_id!r}")
    exchange = Exchange(parts[0])
    segment = Segment(parts[1])
    symbol = parts[2]
    rest = parts[3:]
    if rest and rest[0].startswith("CONT-"):
        return Contract(
            symbol=symbol,
            segment=segment,
            exchange=exchange,
            continuation=ContinuationStyle(rest[0].removeprefix("CONT-")),
        )
    expiry = date.fromisoformat(rest[0]) if rest else None
    strike = float(rest[1]) if segment is Segment.OPTIONS and len(rest) > 1 else None
    right = OptionRight(rest[2]) if segment is Segment.OPTIONS and len(rest) > 2 else None
    return Contract(
        symbol=symbol,
        segment=segment,
        exchange=exchange,
        expiry=expiry,
        strike=strike,
        right=right,
    )


@dataclass(frozen=True)
class Provenance:
    """Who said this, when, and under which schema. Written with every row."""

    source: str
    vendor_symbol: str = ""
    schema_version: str = SCHEMA_VERSION
    bar_convention: BarTimestampConvention = BarTimestampConvention.INTERVAL_START
    ingested_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "vendor_symbol": self.vendor_symbol,
            "schema_version": self.schema_version,
            "bar_convention": self.bar_convention.value,
            "ingested_at": self.ingested_at,
        }


@dataclass(frozen=True)
class Tick:
    ts: datetime
    contract_id: str
    ltp: float
    ltq: int = 0
    atp: float = 0.0
    volume: int = 0
    oi: int = 0
    prev_oi: int = 0
    turnover: float = 0.0
    day_open: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    prev_close: float = 0.0
    bid: float = 0.0
    bid_qty: int = 0
    ask: float = 0.0
    ask_qty: int = 0
    special_tag: str = ""
    sequence: int = 0


@dataclass(frozen=True)
class Bar:
    ts: datetime
    contract_id: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    oi: int = 0
    interval: BarInterval = BarInterval.M1


@dataclass(frozen=True)
class Greeks:
    ts: datetime
    contract_id: str
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0


@dataclass(frozen=True)
class OptionChainRow:
    ts: datetime
    underlying: str
    expiry: date
    strike: float
    right: OptionRight
    ltp: float = 0.0
    volume: int = 0
    oi: int = 0
    prev_oi: int = 0
    bid: float = 0.0
    ask: float = 0.0
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0


class MarketSession(str, Enum):
    CLOSED = "CLOSED"
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    POST_CLOSE = "POST_CLOSE"


@dataclass(frozen=True)
class SessionStatus:
    ts: datetime
    segment_status: dict[str, MarketSession] = field(default_factory=dict)

    def is_open(self, key: str) -> bool:
        return self.segment_status.get(key) is MarketSession.OPEN


# --------------------------------------------------------------------------- #
# stored column contracts
# --------------------------------------------------------------------------- #

PROVENANCE_COLUMNS = [
    "source",
    "vendor_symbol",
    "schema_version",
    "bar_convention",
    "ingested_at",
]

BAR_CORE_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
BAR_COLUMNS_EXT = [*BAR_CORE_COLUMNS, "oi"]

TICK_COLUMNS = [
    "ts",
    "ltp",
    "ltq",
    "atp",
    "volume",
    "oi",
    "bid",
    "bid_qty",
    "ask",
    "ask_qty",
    "sequence",
]

CHAIN_COLUMNS = [
    "ts",
    "underlying",
    "expiry",
    "strike",
    "right",
    "ltp",
    "volume",
    "oi",
    "prev_oi",
    "bid",
    "ask",
    "iv",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
]

MASTER_COLUMNS = [
    "as_of",
    "contract_id",
    "symbol",
    "segment",
    "exchange",
    "expiry",
    "strike",
    "right",
    "lot_size",
    "tick_size",
    "vendor_symbol",
    "vendor_token",
]
