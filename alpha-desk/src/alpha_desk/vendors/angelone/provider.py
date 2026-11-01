"""Angel One adapter for the market-data port.

History + live quotes only. Does not place, modify, or cancel orders.
Network access requires ``ANGELONE_ALLOW_MARKETDATA=true``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

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
    NoDataError,
    NotEntitledError,
    ProviderConfigError,
)
from alpha_desk.marketdata.normalize import normalize_bars, normalize_symbol_master
from alpha_desk.marketdata.provider import Capability, ProviderDescriptor, ProviderLimits
from alpha_desk.vendors.angelone.config import AngelOneConfig
from alpha_desk.vendors.angelone.rest import AngelRestClient, exch_seg_for_contract
from alpha_desk.vendors.angelone.session import AngelSession
from alpha_desk.vendors.angelone.stream import AngelStream, market_status_now
from alpha_desk.vendors.angelone.symbols import AngelSymbology

PROVIDER_NAME = "angelone"

CHUNK_DAYS: dict[BarInterval, int] = {
    BarInterval.M1: 7,
    BarInterval.M3: 14,
    BarInterval.M5: 20,
    BarInterval.M10: 30,
    BarInterval.M15: 40,
    BarInterval.M30: 60,
    BarInterval.M60: 90,
    BarInterval.EOD: 365,
}


@dataclass
class FetchOutcome:
    frame: pd.DataFrame
    rejected: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: list[str] = field(default_factory=list)
    requests: int = 0
    elapsed_s: float = 0.0


class AngelOneProvider:
    def __init__(
        self,
        config: AngelOneConfig,
        rest: AngelRestClient,
        *,
        symbology: AngelSymbology | None = None,
        stream: AngelStream | None = None,
        calendar: MarketCalendar | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._rest = rest
        self._symbology = symbology or AngelSymbology()
        self._stream = stream or AngelStream(config, rest, self._symbology)
        self._calendar = calendar or load_market_calendar()
        self._clock = clock
        self._master_loaded = False
        self.last_outcome: FetchOutcome | None = None
        self.descriptor = _descriptor(config)

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        require_credentials: bool = True,
        calendar: MarketCalendar | None = None,
    ) -> AngelOneProvider:
        config = AngelOneConfig.from_env(env)
        if require_credentials:
            config.require_credentials()
        session = AngelSession(config)
        rest = AngelRestClient(config, session)
        symbology = AngelSymbology()
        stream = AngelStream(config, rest, symbology)
        return cls(config, rest, symbology=symbology, stream=stream, calendar=calendar)

    @property
    def config(self) -> AngelOneConfig:
        return self._config

    @property
    def symbology(self) -> AngelSymbology:
        return self._symbology

    @property
    def rest(self) -> AngelRestClient:
        return self._rest

    @property
    def stream_client(self) -> AngelStream:
        return self._stream

    @property
    def calendar(self) -> MarketCalendar:
        return self._calendar

    def ensure_master(self, segments: Sequence[Segment] | None = None) -> dict[str, int]:
        del segments  # Angel master is one blob covering all segments
        if self._master_loaded and self._symbology.by_token:
            return {"all": len(self._symbology.by_token)}
        count = self._rest.load_scrip_master(self._symbology)
        self._master_loaded = True
        return {"all": count}

    def fetch_symbol_master(self, segment: Segment) -> pd.DataFrame:
        self.ensure_master([segment])
        contracts = [
            e.contract
            for e in self._symbology.by_token.values()
            if e.contract.segment is segment
        ]
        return normalize_symbol_master(contracts, provenance=self._provenance())

    def fetch_expiries(self, underlying: str, segment: Segment) -> list[date]:
        self.ensure_master([segment])
        return self._symbology.expiries(underlying, segment)

    def continuous_future(self, underlying: str) -> Contract:
        """Nearest dated future as a desk-rolled continuous stand-in.

        Angel has no vendor continuous series; we resolve to the front month and
        tag continuation as DESK_ROLLED for provenance honesty.
        """
        self.ensure_master([Segment.FUTURES])
        dated = self._symbology.futures_for(underlying)
        today = date.today()
        live = [c for c in dated if c.expiry and c.expiry >= today]
        if not live:
            raise NoDataError(f"no live Angel futures for {underlying}")
        front = live[0]
        return Contract(
            symbol=front.symbol,
            segment=Segment.FUTURES,
            exchange=front.exchange,
            continuation=ContinuationStyle.DESK_ROLLED,
            lot_size=front.lot_size,
            tick_size=front.tick_size,
            vendor_symbol=front.vendor_symbol,
            vendor_token=front.vendor_token,
        )

    def spot_contract(self, underlying: str) -> Contract:
        self.ensure_master([Segment.INDEX, Segment.EQUITY])
        root = underlying.upper()
        spot = self._symbology.index_spot(root)
        if spot is not None:
            return spot
        eq = self._symbology.equities_named(root)
        if eq:
            return eq[0]
        return Contract(symbol=root, segment=Segment.INDEX, vendor_symbol=root)

    def last_close(self, contract: Contract) -> float:
        enriched = self._resolve(contract)
        end = pd.Timestamp.now(tz=IST)
        start = end - pd.Timedelta(days=10)
        frame = self.fetch_bars(
            enriched, BarInterval.EOD, start.to_pydatetime(), end.to_pydatetime()
        )
        if frame.empty or "close" not in frame.columns:
            raise NoDataError(f"{enriched.vendor_symbol}: no daily bar for ATM anchor")
        return float(pd.to_numeric(frame["close"], errors="coerce").dropna().iloc[-1])

    def option_universe(
        self,
        underlying: str,
        expiry: date,
        *,
        spot: float,
        strikes_each_side: int = 10,
        strike_step: float | None = None,
    ) -> list[Contract]:
        root = underlying.upper()
        self.ensure_master([Segment.OPTIONS])
        listed = self._symbology.strikes(root, expiry)
        if not listed:
            if strike_step is None:
                raise ProviderConfigError(
                    f"{root}: no listed strikes in Angel master and no strike_step given"
                )
            atm = round(spot / strike_step) * strike_step
            listed = [
                atm + i * strike_step
                for i in range(-strikes_each_side, strikes_each_side + 1)
            ]
        else:
            atm = min(listed, key=lambda s: abs(s - spot))
            atm_idx = listed.index(atm)
            lo = max(0, atm_idx - strikes_each_side)
            hi = min(len(listed), atm_idx + strikes_each_side + 1)
            listed = listed[lo:hi]

        out: list[Contract] = []
        for strike in listed:
            for right in (OptionRight.CALL, OptionRight.PUT):
                try:
                    out.append(
                        self._symbology.enrich(
                            Contract(
                                symbol=root,
                                segment=Segment.OPTIONS,
                                expiry=expiry,
                                strike=strike,
                                right=right,
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - skip unlisted strike/right
                    continue
        if not out:
            raise NoDataError(f"{root} {expiry}: no option ladder in Angel master")
        return out

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
            raise ProviderConfigError("Angel hist has no tick bars; use live poll")
        self.ensure_master()
        enriched = self._resolve(contract)
        window_start, notes = self._clamp_to_plan(interval, start, end)
        began = self._clock()
        chunks: list[pd.DataFrame] = []
        rejects: list[pd.DataFrame] = []
        requests = 0
        exch = exch_seg_for_contract(enriched, self._symbology)
        for chunk_start, chunk_end in self._plan_chunks(interval, window_start, end):
            try:
                raw = self._rest.get_bars(
                    enriched, interval, chunk_start, chunk_end, exch_seg=exch
                )
            except NoDataError:
                notes.append(f"no data for {chunk_start.date()}..{chunk_end.date()}")
                requests += 1
                continue
            requests += 1
            normalized = normalize_bars(
                raw,
                contract=enriched,
                interval=interval,
                provenance=self._provenance(enriched),
            )
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
        raise NotEntitledError("Angel SmartAPI does not expose historical ticks")

    def fetch_option_chain(
        self,
        underlying: str,
        expiry: date,
        *,
        with_greeks: bool = False,
    ) -> pd.DataFrame:
        del with_greeks
        self.ensure_master([Segment.OPTIONS])
        rows = [
            e.contract
            for e in self._symbology.by_token.values()
            if e.contract.symbol == underlying.upper()
            and e.contract.segment is Segment.OPTIONS
            and e.contract.expiry == expiry
        ]
        if not rows:
            raise NoDataError(f"no Angel options for {underlying} {expiry}")
        return normalize_symbol_master(rows, provenance=self._provenance())

    def fetch_option_chain_detailed(
        self, underlying: str, expiry: date, *, with_greeks: bool = False
    ) -> FetchOutcome:
        frame = self.fetch_option_chain(underlying, expiry, with_greeks=with_greeks)
        return FetchOutcome(frame=frame, notes=["chain from scrip master (no greeks)"])

    def subscribe(self, contracts: Iterable[Contract]) -> None:
        resolved = [self._resolve(c) for c in contracts]
        self._stream.subscribe(resolved)

    def unsubscribe(self, contracts: Iterable[Contract]) -> None:
        self._stream.unsubscribe(contracts)

    def stream(self) -> Iterator[object]:
        return self._stream.stream()

    def market_status(self) -> SessionStatus:
        return market_status_now()

    def close(self) -> None:
        self._stream.close()

    def _resolve(self, contract: Contract) -> Contract:
        self.ensure_master()
        if contract.is_continuous:
            # Map continuous request to the token we already attached on continuous_future
            if contract.vendor_token:
                return contract
            return self.continuous_future(contract.symbol)
        return self._symbology.resolve(contract)

    def _clamp_to_plan(
        self, interval: BarInterval, start: datetime, end: datetime
    ) -> tuple[datetime, list[str]]:
        notes: list[str] = []
        max_days = self._config.history_days.get(interval)
        if max_days is None:
            return start, notes
        floor = pd.Timestamp.now(tz=IST) - pd.Timedelta(days=max_days)
        start_ts = pd.Timestamp(start)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize(IST)
        if start_ts < floor:
            notes.append(f"clamped start to plan depth {max_days}d for {interval.value}")
            return floor.to_pydatetime(), notes
        return start, notes

    def _plan_chunks(
        self, interval: BarInterval, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        span = CHUNK_DAYS.get(interval, 30)
        chunks: list[tuple[datetime, datetime]] = []
        cursor = pd.Timestamp(start)
        stop = pd.Timestamp(end)
        if cursor.tzinfo is None:
            cursor = cursor.tz_localize(IST)
        if stop.tzinfo is None:
            stop = stop.tz_localize(IST)
        while cursor < stop:
            nxt = min(cursor + pd.Timedelta(days=span), stop)
            chunks.append((cursor.to_pydatetime(), nxt.to_pydatetime()))
            cursor = nxt
        return chunks

    def _provenance(self, contract: Contract | None = None) -> Provenance:
        return Provenance(
            source=PROVIDER_NAME,
            vendor_symbol=contract.vendor_symbol if contract else "",
        )


def _descriptor(config: AngelOneConfig) -> ProviderDescriptor:
    caps = frozenset(
        {
            Capability.HISTORICAL_BARS,
            Capability.SYMBOL_MASTER,
            Capability.EXPIRY_CALENDAR,
            Capability.OPTION_CHAIN_SNAPSHOT,
            Capability.LIVE_TRADES,
            Capability.LIVE_BID_ASK,
            Capability.MARKET_STATUS,
            # Explicitly NOT: EXPIRED_CONTRACTS, HISTORICAL_TICKS, CONTINUOUS_FUTURES vendor
        }
    )
    limits = ProviderLimits(
        max_concurrent_live_symbols=config.max_live_symbols,
        max_bars_per_request=8000,
        max_history_days=dict(config.history_days),
        rest_calls_per_minute=config.rest_calls_per_minute,
    )
    return ProviderDescriptor(
        name=PROVIDER_NAME,
        capabilities=caps,
        segments=frozenset(config.segments),
        limits=limits,
        research_only=False,
    )


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _concat_unique(frames: list[pd.DataFrame], *, key: str) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    if key in out.columns:
        out = out.drop_duplicates(subset=[key], keep="last").sort_values(key)
    return out.reset_index(drop=True)
