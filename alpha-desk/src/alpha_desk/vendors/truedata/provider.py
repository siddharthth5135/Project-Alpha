"""TrueData adapter for the market-data port.

This class is the boundary. Above it, everything speaks :class:`Contract`,
:class:`BarInterval` and canonical frames; below it lives every TrueData quirk:
symbol templating, per-plan history depth, request chunking, segment codes and
the positional websocket protocol.

Requests are planned against the declared plan limits before any network call,
so a backfill that the subscription cannot serve fails immediately with a clear
reason instead of returning a truncated series that would quietly train a model
on a shorter history than intended.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from alpha_desk.marketdata.calendar import MarketCalendar, load_market_calendar
from alpha_desk.marketdata.contracts import (
    IST,
    BarInterval,
    ContinuationStyle,
    Contract,
    OptionRight,
    Provenance,
    Segment,
    SessionStatus,
)
from alpha_desk.marketdata.errors import (
    MarketDataError,
    NoDataError,
    NotEntitledError,
    ProviderConfigError,
    UpstreamError,
)
from alpha_desk.marketdata.normalize import (
    NormalizedFrame,
    normalize_bars,
    normalize_option_chain,
    normalize_symbol_master,
    normalize_ticks,
)
from alpha_desk.marketdata.provider import Capability, ProviderDescriptor, ProviderLimits
from alpha_desk.vendors.truedata.config import TrueDataConfig
from alpha_desk.vendors.truedata.rest import TrueDataRestClient
from alpha_desk.vendors.truedata.stream import EventKind, StreamEvent, TrueDataStream
from alpha_desk.vendors.truedata.symbols import TrueDataSymbology, index_symbol
from alpha_desk.vendors.truedata.transport import (
    HttpTransport,
    RequestsHttpTransport,
    WebsocketClientTransport,
    WsTransport,
)

PROVIDER_NAME = "truedata"

# Candidate vendor segment codes per canonical segment, tried in order. TrueData
# has moved index symbols between codes historically, so we probe rather than
# assume.
VENDOR_SEGMENTS: dict[Segment, tuple[str, ...]] = {
    Segment.EQUITY: ("eq",),
    Segment.INDEX: ("in", "ind", "eq"),
    Segment.FUTURES: ("fo",),
    Segment.OPTIONS: ("fo",),
    Segment.CURRENCY: ("cds",),
    Segment.COMMODITY: ("mcx",),
}

# Conservative per-request spans. TrueData truncates very large windows, and a
# truncated window is indistinguishable from missing data downstream.
CHUNK_DAYS: dict[BarInterval, int] = {
    BarInterval.TICK: 1,
    BarInterval.M1: 30,
    BarInterval.M2: 30,
    BarInterval.M3: 45,
    BarInterval.M5: 60,
    BarInterval.M10: 90,
    BarInterval.M15: 120,
    BarInterval.M30: 180,
    BarInterval.M60: 365,
    BarInterval.EOD: 3650,
}


@dataclass
class FetchOutcome:
    """What a fetch produced, including what it refused to keep."""

    frame: pd.DataFrame
    rejected: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: list[str] = field(default_factory=list)
    requests: int = 0
    elapsed_s: float = 0.0


class TrueDataProvider:
    def __init__(
        self,
        config: TrueDataConfig,
        rest: TrueDataRestClient,
        *,
        symbology: TrueDataSymbology | None = None,
        stream: TrueDataStream | None = None,
        calendar: MarketCalendar | None = None,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], pd.Timestamp] | None = None,
    ):
        self._config = config
        self._rest = rest
        self._symbology = symbology or TrueDataSymbology()
        self._stream = stream
        self._calendar = calendar or load_market_calendar()
        self._clock = clock
        self._today = today or (lambda: pd.Timestamp.now(tz=IST))
        self._master_loaded: set[str] = set()
        self.last_outcome: FetchOutcome | None = None
        self.descriptor = _descriptor(config)

    # -- construction ------------------------------------------------------ #

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        config: TrueDataConfig | None = None,
        http_transport: HttpTransport | None = None,
        ws_transport: WsTransport | None = None,
        require_credentials: bool = True,
        calendar: MarketCalendar | None = None,
    ) -> TrueDataProvider:
        settings = config or TrueDataConfig.from_env(env)
        if require_credentials:
            settings.require_credentials()
        symbology = TrueDataSymbology()
        rest = TrueDataRestClient(settings, http_transport or RequestsHttpTransport())
        stream = TrueDataStream(
            settings,
            ws_transport or WebsocketClientTransport(),
            symbology,
        )
        return cls(settings, rest, symbology=symbology, stream=stream, calendar=calendar)

    @property
    def config(self) -> TrueDataConfig:
        return self._config

    @property
    def symbology(self) -> TrueDataSymbology:
        return self._symbology

    @property
    def rest(self) -> TrueDataRestClient:
        return self._rest

    @property
    def calendar(self) -> MarketCalendar:
        return self._calendar

    # -- reference data ---------------------------------------------------- #

    def ensure_master(self, segments: Sequence[Segment] | None = None) -> dict[str, int]:
        """Load the symbol master once per segment; resolution depends on it."""
        wanted = segments or self._config.segments
        loaded: dict[str, int] = {}
        for segment in wanted:
            for code in VENDOR_SEGMENTS.get(segment, ()):
                if code in self._master_loaded:
                    break
                try:
                    counts = self._rest.load_symbology(self._symbology, [code])
                except (NoDataError, NotEntitledError, MarketDataError):
                    continue
                self._master_loaded.add(code)
                loaded.update(counts)
                break
        return loaded

    def fetch_symbol_master(self, segment: Segment) -> pd.DataFrame:
        self.ensure_master([segment])
        contracts = [
            self._symbology.from_vendor(entry.vendor_symbol)
            for entry in _entries_for(self._symbology, segment)
        ]
        return normalize_symbol_master(contracts, provenance=self._provenance())

    def fetch_expiries(self, underlying: str, segment: Segment) -> list[date]:
        self.ensure_master([segment])
        from_master = self._symbology.expiries(underlying, segment)
        if from_master and segment is Segment.FUTURES:
            return from_master
        code = VENDOR_SEGMENTS.get(segment, ("fo",))[0]
        try:
            expiries = self._rest.get_symbol_expiry_list(underlying.upper(), code)
        except NoDataError:
            expiries = []
        return expiries or from_master

    def index_constituents(self, index_name: str) -> pd.DataFrame:
        self.descriptor.require(Capability.INDEX_CONSTITUENTS)
        return self._rest.get_index_components(index_name)

    def corporate_actions(self, symbol: str) -> pd.DataFrame:
        self.descriptor.require(Capability.CORPORATE_ACTIONS)
        return self._rest.get_corporate_actions(symbol.upper())

    def corporate_actions_range(self, start: date, end: date) -> pd.DataFrame:
        self.descriptor.require(Capability.CORPORATE_ACTIONS)
        return self._rest.get_corporate_actions_range(start, end)

    def symbol_name_changes(self, start: date, end: date) -> pd.DataFrame:
        self.descriptor.require(Capability.SYMBOL_MASTER)
        return self._rest.get_symbol_name_changes(start, end)

    def indices(self) -> pd.DataFrame:
        self.descriptor.require(Capability.INDEX_CONSTITUENTS)
        return self._rest.get_indices()

    def bhavcopy(self, vendor_segment: str, day: date) -> pd.DataFrame:
        self.descriptor.require(Capability.BHAVCOPY)
        return self._rest.get_bhavcopy(vendor_segment, day)

    def fetch_option_ltp_greeks(
        self, underlying: str, expiry: date, strike: float, right: OptionRight
    ) -> pd.DataFrame:
        self.descriptor.require(Capability.OPTION_CHAIN_GREEKS)
        return self._rest.get_ltp_with_greeks(underlying.upper(), expiry, strike, right)

    def fetch_option_tick_greeks(
        self, underlying: str, expiry: date, strike: float, right: OptionRight
    ) -> pd.DataFrame:
        self.descriptor.require(Capability.HISTORICAL_OPTION_GREEKS)
        return self._rest.get_tick_history_with_greeks(
            underlying.upper(), expiry, strike, right
        )

    # -- history ----------------------------------------------------------- #

    def fetch_bars(
        self,
        contract: Contract,
        interval: BarInterval,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        return self.fetch_bars_detailed(contract, interval, start, end).frame

    def fetch_bars_detailed(
        self,
        contract: Contract,
        interval: BarInterval,
        start: datetime,
        end: datetime,
    ) -> FetchOutcome:
        self.descriptor.require(Capability.HISTORICAL_BARS)
        if interval is BarInterval.TICK:
            raise ProviderConfigError("use fetch_ticks for tick data")

        enriched = self._resolve(contract)
        window_start, notes = self._clamp_to_plan(interval, start, end)
        began = self._clock()

        chunks: list[pd.DataFrame] = []
        rejects: list[pd.DataFrame] = []
        requests = 0
        for chunk_start, chunk_end in self._plan_chunks(interval, window_start, end):
            try:
                raw = self._rest.get_bars(
                    enriched.vendor_symbol, interval, chunk_start, chunk_end
                )
            except NoDataError:
                notes.append(f"no data for {chunk_start.date()}..{chunk_end.date()}")
                requests += 1
                continue
            requests += 1
            normalized = normalize_bars(
                raw, contract=enriched, interval=interval, provenance=self._provenance(enriched)
            )
            normalized.raise_if_all_rejected(f"{enriched.contract_id} {interval.value}")
            if normalized.accepted:
                chunks.append(normalized.rows)
            if normalized.dropped:
                rejects.append(normalized.rejected)
            notes.extend(normalized.notes)

        frame = _concat_unique(chunks, key="ts")
        outcome = FetchOutcome(
            frame=frame,
            rejected=_concat(rejects),
            notes=notes,
            requests=requests,
            elapsed_s=self._clock() - began,
        )
        self.last_outcome = outcome
        return outcome

    def fetch_ticks(self, contract: Contract, start: datetime, end: datetime) -> pd.DataFrame:
        self.descriptor.require(Capability.HISTORICAL_TICKS)
        enriched = self._resolve(contract)
        window_start, notes = self._clamp_to_plan(BarInterval.TICK, start, end)

        frames: list[pd.DataFrame] = []
        rejects: list[pd.DataFrame] = []
        requests = 0
        for chunk_start, chunk_end in self._plan_chunks(BarInterval.TICK, window_start, end):
            try:
                raw = self._rest.get_ticks(enriched.vendor_symbol, chunk_start, chunk_end)
            except NoDataError:
                requests += 1
                continue
            requests += 1
            normalized = normalize_ticks(
                raw, contract=enriched, provenance=self._provenance(enriched)
            )
            if normalized.accepted:
                frames.append(normalized.rows)
            if normalized.dropped:
                rejects.append(normalized.rejected)

        frame = _concat(frames)
        self.last_outcome = FetchOutcome(
            frame=frame, rejected=_concat(rejects), notes=notes, requests=requests
        )
        return frame

    # -- options ----------------------------------------------------------- #

    def fetch_option_chain(
        self,
        underlying: str,
        expiry: date,
        *,
        with_greeks: bool = False,
    ) -> pd.DataFrame:
        return self.fetch_option_chain_detailed(
            underlying, expiry, with_greeks=with_greeks
        ).frame

    def fetch_option_chain_detailed(
        self,
        underlying: str,
        expiry: date,
        *,
        with_greeks: bool = False,
    ) -> FetchOutcome:
        capability = (
            Capability.OPTION_CHAIN_GREEKS if with_greeks else Capability.OPTION_CHAIN_SNAPSHOT
        )
        self.descriptor.require(capability)

        root = underlying.upper()
        raw = (
            self._rest.get_option_chain_with_greeks(root, expiry)
            if with_greeks
            else self._rest.get_option_chain(root, expiry)
        )
        prepared = _prepare_chain(raw, underlying=root, expiry=expiry, symbology=self._symbology)
        normalized = normalize_option_chain(prepared, provenance=self._provenance())
        normalized.raise_if_all_rejected(f"{root} chain {expiry}")
        outcome = FetchOutcome(
            frame=normalized.rows, rejected=normalized.rejected, notes=normalized.notes, requests=1
        )
        self.last_outcome = outcome
        return outcome

    def option_universe(
        self,
        underlying: str,
        expiry: date,
        *,
        spot: float,
        strikes_each_side: int = 10,
        strike_step: float | None = None,
    ) -> list[Contract]:
        """ATM-anchored strike ladder as canonical contracts.

        Built from the master's actual strike list when available, so we never
        subscribe to a strike the exchange does not list.
        """
        root = underlying.upper()
        self.ensure_master([Segment.OPTIONS])
        listed = self._symbology.strikes(root, expiry)
        if not listed:
            if strike_step is None:
                raise ProviderConfigError(
                    f"{root}: no listed strikes in the master and no strike_step given"
                )
            atm = round(spot / strike_step) * strike_step
            listed = [
                atm + i * strike_step
                for i in range(-strikes_each_side, strikes_each_side + 1)
            ]
        else:
            atm = min(listed, key=lambda k: abs(k - spot))
            index = listed.index(atm)
            low = max(0, index - strikes_each_side)
            listed = listed[low : index + strikes_each_side + 1]

        contracts: list[Contract] = []
        for strike in listed:
            for right in (OptionRight.CALL, OptionRight.PUT):
                contracts.append(
                    self._symbology.enrich(
                        Contract(
                            symbol=root,
                            segment=Segment.OPTIONS,
                            expiry=expiry,
                            strike=float(strike),
                            right=right,
                        )
                    )
                )
        return contracts

    def last_close(self, contract: Contract) -> float:
        """Most recent daily close. Used to anchor an ATM strike ladder."""
        enriched = self._resolve(contract)
        frame = pd.DataFrame()
        try:
            frame = self._rest.get_last_n_bars(enriched.vendor_symbol, BarInterval.EOD, 1)
        except (NoDataError, UpstreamError, MarketDataError):
            pass
        if frame.empty or "close" not in frame.columns:
            end = datetime.now(ZoneInfo(IST))
            frame = self._rest.get_bars(
                enriched.vendor_symbol, BarInterval.EOD, end - timedelta(days=10), end
            )
        if frame.empty or "close" not in frame.columns:
            raise NoDataError(f"{enriched.vendor_symbol}: no daily bar to read a close from")
        return float(pd.to_numeric(frame["close"], errors="coerce").dropna().iloc[-1])

    def spot_contract(self, underlying: str) -> Contract:
        """The cash index or equity that a derivative root settles against."""
        root = underlying.upper()
        vendor = index_symbol(root)
        segment = Segment.INDEX if vendor != root else Segment.EQUITY
        return Contract(symbol=root, segment=segment, vendor_symbol=vendor)

    def continuous_future(self, underlying: str) -> Contract:
        self.descriptor.require(Capability.CONTINUOUS_FUTURES)
        return self._symbology.enrich(
            Contract(
                symbol=underlying.upper(),
                segment=Segment.FUTURES,
                continuation=ContinuationStyle.VENDOR_SPLICED,
            )
        )

    # -- live -------------------------------------------------------------- #

    @property
    def stream_client(self) -> TrueDataStream:
        if self._stream is None:
            raise ProviderConfigError("this provider was built without a live stream transport")
        return self._stream

    def subscribe(self, contracts: Iterable[Contract]) -> None:
        client = self.stream_client
        if not client.connected:
            client.connect()
        client.subscribe(self._resolve(c) for c in contracts)

    def unsubscribe(self, contracts: Iterable[Contract]) -> None:
        self.stream_client.unsubscribe(self._resolve(c) for c in contracts)

    def stream(self) -> Iterator[StreamEvent]:
        return self.stream_client.stream()

    def market_status(self) -> SessionStatus:
        return self.stream_client.market_status()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()

    # -- internals --------------------------------------------------------- #

    def _resolve(self, contract: Contract) -> Contract:
        if contract.vendor_symbol:
            return contract
        if contract.segment in (Segment.FUTURES, Segment.OPTIONS):
            self.ensure_master([contract.segment])
        return self._symbology.enrich(contract)

    def _provenance(self, contract: Contract | None = None) -> Provenance:
        return Provenance(
            source=PROVIDER_NAME,
            vendor_symbol=contract.vendor_symbol if contract else "",
            bar_convention=self.descriptor.limits.bar_convention,
        )

    def _clamp_to_plan(
        self, interval: BarInterval, start: datetime, end: datetime
    ) -> tuple[datetime, list[str]]:
        depth_days = self._config.history_days.get(interval)
        if not depth_days:
            return start, []

        # Depth is measured from today, not from the requested end: the vendor
        # keeps a rolling window, so an old window is gone regardless of its span.
        floor = self._today() - pd.Timedelta(days=depth_days)
        stop = _to_ist(end)
        requested = _to_ist(start)
        if requested >= floor:
            return start, []
        if stop <= floor:
            raise NotEntitledError(
                f"{interval.value} history only goes back {depth_days} days on this plan; "
                f"the whole requested window is out of reach"
            )
        return floor.to_pydatetime(), [
            f"start clamped to plan depth ({depth_days} days for {interval.value})"
        ]

    def _plan_chunks(
        self, interval: BarInterval, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        span_days = CHUNK_DAYS.get(interval, 30)
        cursor = _to_ist(start)
        stop = _to_ist(end)
        if cursor > stop:
            return []
        chunks: list[tuple[datetime, datetime]] = []
        while cursor <= stop:
            chunk_end = min(cursor + pd.Timedelta(days=span_days) - timedelta(seconds=1), stop)
            chunks.append((cursor.to_pydatetime(), chunk_end.to_pydatetime()))
            cursor = chunk_end + timedelta(seconds=1)
        return chunks


def _to_ist(value: datetime | str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def _descriptor(config: TrueDataConfig) -> ProviderDescriptor:
    capabilities = {
        Capability.HISTORICAL_BARS,
        Capability.HISTORICAL_OPEN_INTEREST,
        Capability.EXPIRED_CONTRACTS,
        Capability.CONTINUOUS_FUTURES,
        Capability.OPTION_CHAIN_SNAPSHOT,
        Capability.SYMBOL_MASTER,
        Capability.EXPIRY_CALENDAR,
        Capability.CORPORATE_ACTIONS,
        Capability.INDEX_CONSTITUENTS,
        Capability.BHAVCOPY,
        Capability.LIVE_TRADES,
        Capability.LIVE_BID_ASK,
        Capability.LIVE_BARS,
        Capability.MARKET_STATUS,
    }
    if config.entitled_ticks:
        capabilities.add(Capability.HISTORICAL_TICKS)
    if config.entitled_greeks:
        capabilities.add(Capability.OPTION_CHAIN_GREEKS)
        capabilities.add(Capability.HISTORICAL_OPTION_GREEKS)
        capabilities.add(Capability.LIVE_GREEKS)

    return ProviderDescriptor(
        name=PROVIDER_NAME,
        capabilities=frozenset(capabilities),
        segments=frozenset(config.segments),
        limits=ProviderLimits(
            max_concurrent_live_symbols=config.max_live_symbols,
            max_history_days=dict(config.history_days),
            rest_calls_per_minute=config.rest_calls_per_minute,
            supports_partial_day_ticks=True,
        ),
    )


def _entries_for(symbology: TrueDataSymbology, segment: Segment) -> list:
    return [
        entry
        for entry in symbology._by_vendor.values()  # noqa: SLF001 - adapter-internal view
        if entry.segment is segment
    ]


def _prepare_chain(
    raw: pd.DataFrame,
    *,
    underlying: str,
    expiry: date,
    symbology: TrueDataSymbology,
) -> pd.DataFrame:
    """Fill in the columns the chain normaliser needs.

    TrueData's chain rows identify the leg by symbol; strike, right and expiry
    are re-derived from that symbol when the payload omits them so a column
    reorder cannot mislabel a put as a call.
    """
    frame = raw.copy()
    if frame.empty:
        return frame

    if "vendor_symbol" in frame.columns:
        parsed = [_safe_parse(symbology, value) for value in frame["vendor_symbol"]]
        frame["strike"] = [
            c.strike if c is not None and c.strike is not None else frame.get("strike", pd.Series(dtype=float)).get(i)
            for i, c in enumerate(parsed)
        ]
        frame["right"] = [
            c.right.value if c is not None and c.right is not None else _existing(frame, "right", i)
            for i, c in enumerate(parsed)
        ]
        frame["expiry"] = [
            c.expiry if c is not None and c.expiry is not None else expiry for c in parsed
        ]
    else:
        if "right" not in frame.columns and "right_or_series" in frame.columns:
            frame["right"] = frame["right_or_series"]
        frame["expiry"] = frame.get("expiry", expiry)

    frame["underlying"] = underlying
    if "ts" not in frame.columns:
        frame["ts"] = pd.Timestamp.now(tz=IST)
    return frame


def _safe_parse(symbology: TrueDataSymbology, value: object) -> Contract | None:
    try:
        return symbology.from_vendor(str(value))
    except Exception:
        return None


def _existing(frame: pd.DataFrame, column: str, index: int) -> object:
    if column not in frame.columns:
        return None
    return frame[column].iloc[index]


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [f for f in frames if not f.empty]
    if not usable:
        return pd.DataFrame()
    return pd.concat(usable, ignore_index=True)


def _concat_unique(frames: list[pd.DataFrame], *, key: str) -> pd.DataFrame:
    frame = _concat(frames)
    if frame.empty:
        return frame
    return (
        frame.drop_duplicates(subset=[key], keep="last")
        .sort_values(key)
        .reset_index(drop=True)
    )
