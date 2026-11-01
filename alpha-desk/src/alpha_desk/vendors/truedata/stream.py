"""TrueData live websocket client.

The wire format is positional: a trade arrives as an array whose meaning is
given by its order. Those orders are declared in :class:`MessageLayout` as data
rather than hardcoded in the parser, so the first live session can confirm them
against captured frames and any future field insertion is a one-line change.

Everything unknown is counted, not crashed on: an unrecognised message key
increments ``unrouted`` and is optionally captured to disk. A feed that silently
stops is treated as a failure — if no frame (including heartbeat) arrives within
the configured timeout, the socket is torn down, reconnected with jittered
backoff, and every subscription is replayed.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from alpha_desk.marketdata.contracts import (
    IST,
    Bar,
    BarInterval,
    Contract,
    Greeks,
    MarketSession,
    SessionStatus,
    Tick,
)
from alpha_desk.marketdata.errors import (
    AuthError,
    SubscriptionLimitError,
    TransportError,
)
from alpha_desk.marketdata.events import EventKind, StreamEvent
from alpha_desk.vendors.truedata.config import TrueDataConfig
from alpha_desk.vendors.truedata.symbols import TrueDataSymbology
from alpha_desk.vendors.truedata.transport import RetryPolicy, WsTransport

# Re-export for older imports (td_smoke, tests).
__all__ = ["EventKind", "StreamEvent", "TrueDataStream", "MessageLayout"]


@dataclass(frozen=True)
class MessageLayout:
    """Positional field order for each websocket message type."""

    trade: tuple[str, ...] = (
        "vendor_token",
        "ts",
        "ltp",
        "ltq",
        "atp",
        "volume",
        "oi",
        "prev_oi",
        "turnover",
        "special_tag",
        "sequence",
        "bid",
        "bid_qty",
        "ask",
        "ask_qty",
    )
    bidask: tuple[str, ...] = (
        "vendor_token",
        "ts",
        "bid",
        "bid_qty",
        "ask",
        "ask_qty",
    )
    bar: tuple[str, ...] = (
        "vendor_token",
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "oi",
    )
    greeks: tuple[str, ...] = (
        "vendor_token",
        "ts",
        "iv",
        "delta",
        "theta",
        "gamma",
        "vega",
        "rho",
    )
    touchline: tuple[str, ...] = (
        "vendor_symbol",
        "vendor_token",
        "ts",
        "ltp",
        "ltq",
        "atp",
        "volume",
        "day_open",
        "day_high",
        "day_low",
        "prev_close",
        "oi",
        "prev_oi",
        "turnover",
        "bid",
        "bid_qty",
        "ask",
        "ask_qty",
    )


_DATA_KEYS = ("trade", "bidask", "greeks", "symbollist", "bar")

# Session acknowledgements that carry no data and are not anomalies.
_ACK_TOKENS = (
    "symbols added",
    "symbol added",
    "symbols removed",
    "symbol removed",
    "real time data service",
    "you are already logged in",
)

BAR_MESSAGE_INTERVALS = {
    "bar1min": BarInterval.M1,
    "bar5min": BarInterval.M5,
    "bar15min": BarInterval.M15,
    "bar30min": BarInterval.M30,
    "bar60min": BarInterval.M60,
}


@dataclass
class StreamStats:
    connects: int = 0
    reconnects: int = 0
    messages: dict[str, int] = field(default_factory=dict)
    unrouted: dict[str, int] = field(default_factory=dict)
    parse_errors: int = 0
    sequence_gaps: int = 0
    stale_timeouts: int = 0
    latency_outliers: int = 0
    last_frame_at: float | None = None
    last_heartbeat_at: float | None = None
    latency_samples: list[float] = field(default_factory=list)

    def count(self, key: str) -> None:
        self.messages[key] = self.messages.get(key, 0) + 1

    def latency_p(self, quantile: float) -> float | None:
        if not self.latency_samples:
            return None
        series = pd.Series(self.latency_samples)
        return float(series.quantile(quantile))

    def as_dict(self) -> dict[str, object]:
        return {
            "connects": self.connects,
            "reconnects": self.reconnects,
            "messages": dict(self.messages),
            "unrouted": dict(self.unrouted),
            "parse_errors": self.parse_errors,
            "sequence_gaps": self.sequence_gaps,
            "stale_timeouts": self.stale_timeouts,
            "latency_outliers": self.latency_outliers,
            "latency_p50_s": self.latency_p(0.5),
            "latency_p95_s": self.latency_p(0.95),
            "latency_max_s": max(self.latency_samples) if self.latency_samples else None,
        }


class TrueDataStream:
    """Supervised live feed. Owns reconnection; the caller owns persistence."""

    def __init__(
        self,
        config: TrueDataConfig,
        transport: WsTransport,
        symbology: TrueDataSymbology,
        *,
        layout: MessageLayout | None = None,
        retry: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: pd.Timestamp.now(tz=IST).to_pydatetime(),
        recv_timeout_s: float = 1.0,
        capture_dir: Path | None = None,
    ):
        self._config = config
        self._transport = transport
        self._symbology = symbology
        self._layout = layout or MessageLayout()
        self._retry = retry or RetryPolicy(
            max_attempts=10**6,
            base_s=1.0,
            max_s=config.reconnect_max_backoff_s,
        )
        self._clock = clock
        self._wall = wall_clock
        self._recv_timeout_s = recv_timeout_s
        self._capture_dir = capture_dir or config.raw_capture_dir
        self._subscribed: dict[str, Contract] = {}
        self._by_token: dict[str, Contract] = {}
        self._last_sequence: dict[str, int] = {}
        self._session = SessionStatus(ts=self._wall(), segment_status={})
        self._closed = False
        self.stats = StreamStats()

    # -- lifecycle --------------------------------------------------------- #

    @property
    def connected(self) -> bool:
        return self._transport.connected

    @property
    def subscribed_symbols(self) -> list[str]:
        return sorted(self._subscribed)

    def connect(self) -> None:
        self._config.require_credentials()
        url = (
            f"{self._config.ws_url}?user={self._config.username}"
            f"&password={self._config.password}"
        )
        self._transport.connect(url, timeout=self._config.connect_timeout_s)
        self.stats.connects += 1
        self.stats.last_frame_at = self._clock()

        greeting = self._transport.recv(timeout=self._config.connect_timeout_s)
        if greeting is None:
            raise TransportError("truedata websocket accepted the socket but sent no greeting")
        payload = self._decode(greeting)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise AuthError(f"truedata websocket rejected the session: {payload.get('message')}")

    def close(self) -> None:
        self._closed = True
        self._transport.close()

    def __enter__(self) -> TrueDataStream:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- subscriptions ----------------------------------------------------- #

    def subscribe(self, contracts: Iterable[Contract]) -> None:
        pending: dict[str, Contract] = {}
        for contract in contracts:
            enriched = self._symbology.enrich(contract)
            symbol = enriched.vendor_symbol.upper()
            if symbol in self._subscribed:
                continue
            pending[symbol] = enriched

        if not pending:
            return

        total = len(self._subscribed) + len(pending)
        if total > self._config.max_live_symbols:
            raise SubscriptionLimitError(
                f"subscription of {total} symbols exceeds the licensed limit "
                f"({self._config.max_live_symbols}); reduce the watchlist or upgrade the plan"
            )

        self._send({"method": "addsymbol", "symbols": sorted(pending)})
        self._subscribed.update(pending)

    def unsubscribe(self, contracts: Iterable[Contract]) -> None:
        symbols = []
        for contract in contracts:
            symbol = self._symbology.enrich(contract).vendor_symbol.upper()
            if symbol in self._subscribed:
                symbols.append(symbol)
        if not symbols:
            return
        self._send({"method": "removesymbol", "symbols": sorted(symbols)})
        for symbol in symbols:
            contract = self._subscribed.pop(symbol, None)
            if contract is not None:
                token = contract.vendor_token
                self._by_token.pop(token, None)

    def market_status(self) -> SessionStatus:
        return self._session

    # -- streaming --------------------------------------------------------- #

    def stream(self) -> Iterator[StreamEvent]:
        """Yield events forever, reconnecting as needed, until ``close()``."""
        if not self.connected:
            self.connect()
        yield StreamEvent(EventKind.CONNECTED, self._config.ws_url, self._wall())

        attempt = 0
        while not self._closed:
            try:
                frame = self._transport.recv(timeout=self._recv_timeout_s)
            except TransportError:
                yield from self._handle_drop("socket error")
                attempt = self._reconnect(attempt)
                continue

            now = self._clock()
            if frame is None:
                if self._is_stale(now):
                    self.stats.stale_timeouts += 1
                    yield from self._handle_drop("heartbeat timeout")
                    attempt = self._reconnect(attempt)
                continue

            attempt = 0
            self.stats.last_frame_at = now
            self._capture(frame)
            for event in self._route(frame):
                yield event

    def _handle_drop(self, reason: str) -> Iterator[StreamEvent]:
        self._transport.close()
        yield StreamEvent(EventKind.DISCONNECTED, reason, self._wall())

    def _reconnect(self, attempt: int) -> int:
        if self._closed:
            return attempt
        attempt += 1
        self._retry.wait(attempt)
        try:
            self.connect()
        except (TransportError, AuthError):
            return attempt
        self.stats.reconnects += 1
        replay = list(self._subscribed.values())
        self._subscribed.clear()
        self._by_token.clear()
        self._last_sequence.clear()
        if replay:
            self.subscribe(replay)
        return 0

    def _is_stale(self, now: float) -> bool:
        last = self.stats.last_frame_at
        if last is None:
            return False
        return (now - last) > self._config.heartbeat_timeout_s

    def _send(self, message: dict[str, object]) -> None:
        self._transport.send(json.dumps(message))

    # -- routing ----------------------------------------------------------- #

    def _route(self, frame: str) -> list[StreamEvent]:
        payload = self._decode(frame)
        if payload is None:
            return []
        if not isinstance(payload, dict):
            self.stats.parse_errors += 1
            return []

        received = self._wall()
        events: list[StreamEvent] = []

        # A frame is either data or control. Control frames spread their fields
        # across sibling keys (market status names one key per segment), so they
        # are interpreted as a whole rather than key by key.
        data_keys = [key for key in payload if key.lower().startswith(_DATA_KEYS)]
        if not data_keys:
            message = payload.get("message") or payload.get("Message")
            if message is not None:
                return self._control_event(str(message), payload, received)

        for key, value in sorted(payload.items(), key=_key_priority):
            lowered = key.lower()
            if data_keys and lowered not in {k.lower() for k in data_keys}:
                continue
            if lowered == "trade":
                events.extend(self._trade_event(value, received))
            elif lowered == "bidask":
                events.extend(self._bidask_event(value, received))
            elif lowered in BAR_MESSAGE_INTERVALS:
                events.extend(self._bar_event(value, received, BAR_MESSAGE_INTERVALS[lowered]))
            elif lowered == "greeks":
                events.extend(self._greeks_event(value, received))
            elif lowered == "symbollist":
                events.extend(self._touchline_events(value, received))
            elif lowered in {"success", "timestamp", "username", "message", "symbolsadded", "symbolsremoved"}:
                continue
            else:
                self.stats.unrouted[key] = self.stats.unrouted.get(key, 0) + 1
                events.append(StreamEvent(EventKind.UNROUTED, {key: value}, received))
        return events

    def _decode(self, frame: str) -> object:
        try:
            return json.loads(frame)
        except json.JSONDecodeError:
            self.stats.parse_errors += 1
            return None

    def _trade_event(self, value: object, received: datetime) -> list[StreamEvent]:
        row = self._map(value, self._layout.trade)
        if row is None:
            return []
        self.stats.count("trade")
        token = str(row.get("vendor_token", ""))
        contract = self._resolve(token)
        ts = _parse_ts(row.get("ts"))
        sequence = _as_int(row.get("sequence"))
        self._check_sequence(token, sequence)

        tick = Tick(
            ts=ts,
            contract_id=contract.contract_id if contract else token,
            ltp=_as_float(row.get("ltp")),
            ltq=_as_int(row.get("ltq")),
            atp=_as_float(row.get("atp")),
            volume=_as_int(row.get("volume")),
            oi=_as_int(row.get("oi")),
            prev_oi=_as_int(row.get("prev_oi")),
            turnover=_as_float(row.get("turnover")),
            bid=_as_float(row.get("bid")),
            bid_qty=_as_int(row.get("bid_qty")),
            ask=_as_float(row.get("ask")),
            ask_qty=_as_int(row.get("ask_qty")),
            special_tag=str(row.get("special_tag") or ""),
            sequence=sequence,
        )
        latency = self._latency(ts, received)
        return [StreamEvent(EventKind.TICK, tick, received, latency, token)]

    def _bidask_event(self, value: object, received: datetime) -> list[StreamEvent]:
        row = self._map(value, self._layout.bidask)
        if row is None:
            return []
        self.stats.count("bidask")
        token = str(row.get("vendor_token", ""))
        contract = self._resolve(token)
        ts = _parse_ts(row.get("ts"))
        tick = Tick(
            ts=ts,
            contract_id=contract.contract_id if contract else token,
            ltp=0.0,
            bid=_as_float(row.get("bid")),
            bid_qty=_as_int(row.get("bid_qty")),
            ask=_as_float(row.get("ask")),
            ask_qty=_as_int(row.get("ask_qty")),
        )
        return [StreamEvent(EventKind.BID_ASK, tick, received, self._latency(ts, received), token)]

    def _bar_event(
        self, value: object, received: datetime, interval: BarInterval
    ) -> list[StreamEvent]:
        row = self._map(value, self._layout.bar)
        if row is None:
            return []
        self.stats.count(f"bar{interval.value}")
        token = str(row.get("vendor_token", ""))
        contract = self._resolve(token)
        ts = _parse_ts(row.get("ts"))
        bar = Bar(
            ts=ts,
            contract_id=contract.contract_id if contract else token,
            open=_as_float(row.get("open")),
            high=_as_float(row.get("high")),
            low=_as_float(row.get("low")),
            close=_as_float(row.get("close")),
            volume=_as_int(row.get("volume")),
            oi=_as_int(row.get("oi")),
            interval=interval,
        )
        return [StreamEvent(EventKind.BAR, bar, received, self._latency(ts, received), token)]

    def _greeks_event(self, value: object, received: datetime) -> list[StreamEvent]:
        row = self._map(value, self._layout.greeks)
        if row is None:
            return []
        self.stats.count("greeks")
        token = str(row.get("vendor_token", ""))
        contract = self._resolve(token)
        ts = _parse_ts(row.get("ts"))
        greeks = Greeks(
            ts=ts,
            contract_id=contract.contract_id if contract else token,
            iv=_as_float(row.get("iv")),
            delta=_as_float(row.get("delta")),
            gamma=_as_float(row.get("gamma")),
            theta=_as_float(row.get("theta")),
            vega=_as_float(row.get("vega")),
            rho=_as_float(row.get("rho")),
        )
        return [StreamEvent(EventKind.GREEKS, greeks, received, self._latency(ts, received), token)]

    def _touchline_events(self, value: object, received: datetime) -> list[StreamEvent]:
        rows = value if isinstance(value, list) else []
        events: list[StreamEvent] = []
        for entry in rows:
            row = self._map(entry, self._layout.touchline)
            if row is None:
                continue
            self.stats.count("touchline")
            vendor_symbol = str(row.get("vendor_symbol", "")).upper()
            token = str(row.get("vendor_token", ""))
            contract = self._subscribed.get(vendor_symbol)
            if contract is not None and token:
                bound = Contract(
                    symbol=contract.symbol,
                    segment=contract.segment,
                    exchange=contract.exchange,
                    expiry=contract.expiry,
                    strike=contract.strike,
                    right=contract.right,
                    lot_size=contract.lot_size,
                    tick_size=contract.tick_size,
                    continuation=contract.continuation,
                    vendor_symbol=contract.vendor_symbol,
                    vendor_token=token,
                )
                self._subscribed[vendor_symbol] = bound
                self._by_token[token] = bound
            events.append(StreamEvent(EventKind.TOUCHLINE, row, received, None, token))
        return events

    def _control_event(
        self, message: str, payload: dict[str, object], received: datetime
    ) -> list[StreamEvent]:
        lowered = message.strip().lower()
        if "heartbeat" in lowered:
            self.stats.count("heartbeat")
            self.stats.last_heartbeat_at = self._clock()
            return [StreamEvent(EventKind.HEARTBEAT, message, received)]
        if "marketstatus" in lowered or "market status" in lowered:
            self.stats.count("marketstatus")
            self._session = _parse_market_status(payload, received)
            return [StreamEvent(EventKind.MARKET_STATUS, self._session, received)]
        self.stats.count("control")
        if any(token in lowered for token in _ACK_TOKENS):
            return []
        return [StreamEvent(EventKind.UNROUTED, payload, received)]

    def _map(self, value: object, layout: tuple[str, ...]) -> dict[str, object] | None:
        if isinstance(value, dict):
            return {str(k).lower(): v for k, v in value.items()}
        if not isinstance(value, (list, tuple)):
            self.stats.parse_errors += 1
            return None
        row = {name: value[i] for i, name in enumerate(layout) if i < len(value)}
        for extra in range(len(layout), len(value)):
            row[f"field_{extra}"] = value[extra]
        return row

    def _resolve(self, token: str) -> Contract | None:
        return self._by_token.get(token)

    def _check_sequence(self, token: str, sequence: int) -> None:
        if not token or sequence <= 0:
            return
        previous = self._last_sequence.get(token)
        if previous is not None and sequence > previous + 1:
            self.stats.sequence_gaps += 1
        self._last_sequence[token] = sequence

    def _latency(self, ts: datetime, received: datetime) -> float | None:
        try:
            latency = (pd.Timestamp(received) - pd.Timestamp(ts)).total_seconds()
        except (TypeError, ValueError):
            return None
        if -5.0 < latency < 600.0:
            self.stats.latency_samples.append(latency)
        else:
            # Either the vendor replayed stale data or our clock is skewed;
            # either way it must not pollute the latency distribution.
            self.stats.latency_outliers += 1
        return latency

    def _capture(self, frame: str) -> None:
        if self._capture_dir is None:
            return
        self._capture_dir.mkdir(parents=True, exist_ok=True)
        day = pd.Timestamp.now(tz=IST).strftime("%Y%m%d")
        path = self._capture_dir / f"truedata_ws_{day}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(frame.rstrip("\n") + "\n")


def _key_priority(item: tuple[str, object]) -> tuple[int, str]:
    key = item[0].lower()
    return (0 if key.startswith(_DATA_KEYS) else 1, key)


def _parse_market_status(payload: dict[str, object], received: datetime) -> SessionStatus:
    statuses: dict[str, MarketSession] = {}
    for key, value in payload.items():
        if key.lower() in {"message", "timestamp"}:
            continue
        text = str(value).strip().upper().replace(" ", "_")
        if text in MarketSession.__members__:
            statuses[key.upper()] = MarketSession[text]
        elif text in {m.value for m in MarketSession}:
            statuses[key.upper()] = MarketSession(text)
    return SessionStatus(ts=received, segment_status=statuses)


def _parse_ts(value: object) -> datetime:
    stamp = pd.Timestamp(str(value))
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    return stamp.tz_convert(IST).to_pydatetime()


def _as_float(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: object) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0
