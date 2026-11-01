"""Angel One live capture via REST quote polling.

SmartAPI WebSocket v2 is binary; until a binary decoder is locked, we poll the
official quote REST endpoint on a short interval and emit canonical tick events.
That is enough for Phase-1 store + paper on 1m/15m. No order endpoints are used.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from alpha_desk.marketdata.contracts import IST, Contract, MarketSession, SessionStatus, Tick
from alpha_desk.marketdata.errors import SubscriptionLimitError
from alpha_desk.marketdata.events import EventKind, StreamEvent
from alpha_desk.vendors.angelone.config import AngelOneConfig
from alpha_desk.vendors.angelone.rest import AngelRestClient
from alpha_desk.vendors.angelone.symbols import AngelSymbology

TZ = ZoneInfo(IST)


@dataclass
class StreamStats:
    polls: int = 0
    ticks: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, object]:
        return {"polls": self.polls, "ticks": self.ticks, "errors": self.errors}


@dataclass
class AngelStream:
    config: AngelOneConfig
    rest: AngelRestClient
    symbology: AngelSymbology
    _subscribed: list[Contract] = field(default_factory=list)
    stats: StreamStats = field(default_factory=StreamStats)
    _stop: bool = False

    def subscribe(self, contracts: Iterable[Contract]) -> None:
        wanted = list(contracts)
        if len(wanted) > self.config.max_live_symbols:
            raise SubscriptionLimitError(
                f"Angel live cap is {self.config.max_live_symbols}; got {len(wanted)}"
            )
        self._subscribed = wanted

    def unsubscribe(self, contracts: Iterable[Contract]) -> None:
        drop = {c.contract_id for c in contracts}
        self._subscribed = [c for c in self._subscribed if c.contract_id not in drop]

    def close(self) -> None:
        self._stop = True

    def stream(self) -> Iterator[StreamEvent]:
        self.config.require_network_allowed()
        self._stop = False
        yield StreamEvent(
            kind=EventKind.CONNECTED,
            payload={"mode": "quote_poll"},
            received_at=_now(),
        )
        while not self._stop:
            began = time.monotonic()
            try:
                for event in self._poll_once():
                    yield event
            except Exception as exc:  # noqa: BLE001 - keep poll loop alive
                self.stats.errors += 1
                yield StreamEvent(
                    kind=EventKind.UNROUTED,
                    payload={"error": str(exc)},
                    received_at=_now(),
                )
            self.stats.polls += 1
            yield StreamEvent(kind=EventKind.HEARTBEAT, payload={}, received_at=_now())
            elapsed = time.monotonic() - began
            sleep_for = max(0.0, self.config.poll_interval_s - elapsed)
            if sleep_for:
                time.sleep(sleep_for)

    def _poll_once(self) -> list[StreamEvent]:
        by_exch: dict[str, list[str]] = {}
        by_token: dict[str, Contract] = {}
        for contract in self._subscribed:
            entry = self.symbology.by_token.get(contract.vendor_token or "")
            if entry is None and contract.vendor_symbol:
                entry = self.symbology.by_vendor_symbol.get(contract.vendor_symbol.upper())
            if entry is None:
                continue
            by_exch.setdefault(entry.exch_seg, []).append(entry.token)
            by_token[entry.token] = entry.contract

        quotes = self.rest.get_quotes(by_exch)
        events: list[StreamEvent] = []
        received = _now()
        for row in quotes:
            token = str(row.get("symbolToken") or row.get("token") or "")
            contract = by_token.get(token)
            if contract is None:
                continue
            ltp = _f(row.get("ltp") or row.get("lastPrice"))
            if ltp is None:
                continue
            tick = Tick(
                ts=received,
                contract_id=contract.contract_id,
                ltp=ltp,
                ltq=int(_f(row.get("ltq")) or 0),
                volume=int(_f(row.get("tradeVolume") or row.get("volume")) or 0),
                oi=int(_f(row.get("opnInterest") or row.get("oi")) or 0),
                bid=_f(row.get("bidPrice") or row.get("bestBid")) or 0.0,
                ask=_f(row.get("askPrice") or row.get("bestAsk")) or 0.0,
                day_open=_f(row.get("open")) or 0.0,
                day_high=_f(row.get("high")) or 0.0,
                day_low=_f(row.get("low")) or 0.0,
                prev_close=_f(row.get("close") or row.get("prevClose")) or 0.0,
            )
            events.append(
                StreamEvent(
                    kind=EventKind.TICK,
                    payload=tick,
                    received_at=received,
                    latency_s=0.0,
                    vendor_token=token,
                )
            )
            self.stats.ticks += 1
        return events


def market_status_now() -> SessionStatus:
    now = pd.Timestamp.now(tz=IST)
    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now.weekday() >= 5:
        state = MarketSession.CLOSED
    elif open_t <= now <= close_t:
        state = MarketSession.OPEN
    else:
        state = MarketSession.CLOSED
    return SessionStatus(
        ts=now.to_pydatetime(),
        segment_status={"NSE": state, "NFO": state, "MCX": state, "CDS": state},
    )


def _now() -> datetime:
    return datetime.now(tz=TZ)


def _f(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
