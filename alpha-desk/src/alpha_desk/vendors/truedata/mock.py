"""Deterministic TrueData doubles.

The whole point of the trial is that day one is spent measuring the vendor, not
debugging our own plumbing. So the adapter is exercised end to end here against
fakes that speak the documented wire formats: positional ``Records`` arrays for
history, keyed records for the greeks host, and positional websocket frames for
the live feed.

The fakes deliberately misbehave on demand — auth expiry, 429, 5xx, a mid-stream
socket drop, a duplicated bar, a crossed quote — because those paths must be
proven before real credentials arrive, not after.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from alpha_desk.marketdata.calendar import MarketCalendar, load_market_calendar
from alpha_desk.marketdata.contracts import IST, BarInterval, Segment
from alpha_desk.marketdata.errors import TransportError
from alpha_desk.vendors.truedata.config import TrueDataConfig
from alpha_desk.vendors.truedata.provider import TrueDataProvider
from alpha_desk.vendors.truedata.rest import TrueDataRestClient
from alpha_desk.vendors.truedata.stream import MessageLayout, TrueDataStream
from alpha_desk.vendors.truedata.symbols import TrueDataSymbology
from alpha_desk.vendors.truedata.transport import HttpResponse

MOCK_TOKEN = "mock-access-token"

# One weekly and one monthly expiry, plus a monthly future, so option and
# futures paths both have realistic contracts to resolve.
MOCK_EXPIRIES = (date(2025, 8, 28), date(2025, 9, 25))
MOCK_STRIKE_STEP = 50.0
MOCK_SPOT = 24_000.0


@dataclass
class FakeHttpTransport:
    """Routes requests to canned handlers and records what was asked."""

    handlers: dict[str, Callable[[Mapping[str, object]], HttpResponse]] = field(
        default_factory=dict
    )
    requests: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)
    fail_times: dict[str, list[int]] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        timeout: float = 15.0,
    ) -> HttpResponse:
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1] if "/" in url.split("://", 1)[-1] else "/"
        payload = dict(params or {}) if method.upper() == "GET" else dict(data or {})
        self.requests.append((method.upper(), path, payload))

        self._counts[path] = self._counts.get(path, 0) + 1
        scripted = self.fail_times.get(path)
        if scripted and self._counts[path] in scripted:
            return HttpResponse(status=503, body='{"status":"Error","message":"upstream busy"}')

        handler = self.handlers.get(path)
        if handler is None:
            return HttpResponse(status=404, body='{"status":"Error","message":"no such endpoint"}')

        if path != "/token":
            authorization = (headers or {}).get("Authorization", "")
            catalog = path == "/getAllSymbols" and payload.get("user") and payload.get("password")
            if authorization != f"Bearer {MOCK_TOKEN}" and not catalog:
                return HttpResponse(status=401, body='{"error":"invalid_token"}')

        return handler(payload)

    def count(self, path: str) -> int:
        return self._counts.get(path, 0)


@dataclass
class FakeWsTransport:
    """Scripted websocket. Frames are queued; drops are scheduled by index."""

    frames: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    drop_after: int | None = None
    connected_flag: bool = False
    connects: int = 0
    on_subscribe: Callable[[list[str]], list[str]] | None = None
    _reads: int = 0

    @property
    def connected(self) -> bool:
        return self.connected_flag

    def connect(self, url: str, *, timeout: float = 10.0) -> None:
        if "user=" not in url or "password=" not in url:
            raise TransportError("websocket url is missing credentials")
        self.connected_flag = True
        self.connects += 1
        self._reads = 0
        self.frames.insert(
            0,
            json.dumps(
                {
                    "success": True,
                    "message": "TrueData Real Time Data Service",
                    "timestamp": pd.Timestamp.now(tz=IST).isoformat(),
                }
            ),
        )

    def send(self, payload: str) -> None:
        if not self.connected_flag:
            raise TransportError("send on a closed socket")
        self.sent.append(payload)
        message = json.loads(payload)
        if message.get("method") == "addsymbol":
            symbols = [str(s) for s in message.get("symbols", [])]
            extra = self.on_subscribe(symbols) if self.on_subscribe else [touchline_frame(symbols)]
            self.frames.extend(extra)

    def recv(self, timeout: float = 5.0) -> str | None:
        if not self.connected_flag:
            raise TransportError("recv on a closed socket")
        if self.drop_after is not None and self._reads >= self.drop_after:
            self.drop_after = None
            self.connected_flag = False
            raise TransportError("simulated socket drop")
        if not self.frames:
            return None
        self._reads += 1
        return self.frames.pop(0)

    def close(self) -> None:
        self.connected_flag = False


def touchline_frame(symbols: list[str]) -> str:
    rows = []
    for index, symbol in enumerate(symbols, start=1):
        rows.append(
            [
                symbol,
                str(1000 + index),
                pd.Timestamp("2025-08-14 09:15:00", tz=IST).strftime("%Y-%m-%dT%H:%M:%S"),
                MOCK_SPOT,
                50,
                MOCK_SPOT,
                125_000,
                MOCK_SPOT - 20,
                MOCK_SPOT + 30,
                MOCK_SPOT - 40,
                MOCK_SPOT - 10,
                1_500_000,
                1_480_000,
                3.1e9,
                MOCK_SPOT - 0.5,
                75,
                MOCK_SPOT + 0.5,
                50,
            ]
        )
    return json.dumps({"success": True, "message": "symbols added", "symbollist": rows})


def trade_frame(
    token: str,
    ts: str,
    ltp: float,
    *,
    sequence: int = 1,
    volume: int = 100,
    oi: int = 1_500_000,
) -> str:
    return json.dumps(
        {
            "trade": [
                token,
                ts,
                f"{ltp:.2f}",
                "25",
                f"{ltp:.2f}",
                str(volume),
                str(oi),
                str(oi - 500),
                "1000000",
                "",
                str(sequence),
                f"{ltp - 0.5:.2f}",
                "75",
                f"{ltp + 0.5:.2f}",
                "50",
            ]
        }
    )


def bar_frame(token: str, ts: str, close: float) -> str:
    return json.dumps(
        {
            "bar1min": [
                token,
                ts,
                f"{close - 1:.2f}",
                f"{close + 2:.2f}",
                f"{close - 3:.2f}",
                f"{close:.2f}",
                "15000",
                "1500000",
            ]
        }
    )


def greeks_frame(token: str, ts: str) -> str:
    return json.dumps(
        {"greeks": [token, ts, "13.45", "0.52", "-4.10", "0.0009", "12.30", "0.15"]}
    )


def heartbeat_frame() -> str:
    return json.dumps(
        {"message": "HeartBeat", "timestamp": pd.Timestamp.now(tz=IST).isoformat()}
    )


def market_status_frame(status: str = "OPEN") -> str:
    return json.dumps({"message": "marketstatus", "NSE": status, "NFO": status})


# --------------------------------------------------------------------------- #
# canned REST payloads
# --------------------------------------------------------------------------- #


def _walk(stamps: pd.DatetimeIndex, seed: int, start_price: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(stamps)
    shocks = rng.normal(0.0, 0.0008, size=n)
    close = start_price * np.exp(np.cumsum(shocks))
    spread = np.abs(rng.normal(0.0, 0.0006, size=n)) * close
    open_ = np.concatenate([[start_price], close[:-1]])
    return pd.DataFrame(
        {
            "ts": stamps,
            "open": np.round(open_, 2),
            "high": np.round(np.maximum(open_, close) + spread, 2),
            "low": np.round(np.minimum(open_, close) - spread, 2),
            "close": np.round(close, 2),
            "volume": rng.integers(5_000, 50_000, size=n),
            "oi": rng.integers(1_000_000, 2_000_000, size=n),
        }
    )


def bars_payload(
    symbol: str,
    interval: BarInterval,
    start: datetime,
    end: datetime,
    calendar: MarketCalendar,
    *,
    duplicate_last: bool = False,
) -> str:
    segment = Segment.OPTIONS if symbol.endswith(("CE", "PE")) else Segment.FUTURES
    stamps = calendar.expected_bar_index(segment, interval, pd.Timestamp(start), pd.Timestamp(end))
    if len(stamps) == 0:
        return json.dumps({"status": "No data", "Records": []})

    frame = _walk(stamps, seed=abs(hash(symbol)) % (2**32), start_price=MOCK_SPOT)
    records = [
        [
            ts.strftime("%Y-%m-%dT%H:%M:%S"),
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            int(row.volume),
            int(row.oi),
        ]
        for ts, row in zip(frame["ts"], frame.itertuples())
    ]
    if duplicate_last and records:
        records.append(list(records[-1]))
    return json.dumps({"status": "Success", "Records": records})


def ticks_payload(symbol: str, start: datetime, end: datetime) -> str:
    stamps = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="1s")[:600]
    if len(stamps) == 0:
        return json.dumps({"status": "No data", "Records": []})
    frame = _walk(stamps, seed=abs(hash(("tick", symbol))) % (2**32), start_price=MOCK_SPOT)
    records = [
        [
            ts.strftime("%Y-%m-%dT%H:%M:%S"),
            float(row.close),
            int(row.volume),
            int(row.oi),
            round(float(row.close) - 0.5, 2),
            75,
            round(float(row.close) + 0.5, 2),
            50,
        ]
        for ts, row in zip(frame["ts"], frame.itertuples())
    ]
    return json.dumps({"status": "Success", "Records": records})


def master_payload(segment: str) -> str:
    records: list[list[object]] = []
    if segment == "eq":
        for index, symbol in enumerate(["RELIANCE", "TCS", "HDFCBANK"], start=1):
            records.append([2000 + index, symbol, "EQ", f"INE00{index}", 1, 0.05, "", 0, "XX", symbol])
    elif segment in {"in", "ind"}:
        for index, symbol in enumerate(["NIFTY 50", "NIFTY BANK", "INDIA VIX"], start=1):
            records.append([3000 + index, symbol, "INDEX", "", 1, 0.05, "", 0, "XX", symbol])
    elif segment == "fo":
        token = 4000
        for root, spot, step, lot in (
            ("NIFTY", MOCK_SPOT, MOCK_STRIKE_STEP, 75),
            ("BANKNIFTY", 51_000.0, 100.0, 30),
        ):
            for expiry in MOCK_EXPIRIES:
                stamp = f"{expiry.year % 100:02d}{expiry.strftime('%b').upper()}{expiry.day:02d}"
                token += 1
                records.append(
                    [
                        token,
                        f"{root}{stamp}FUT",
                        "FUTIDX",
                        "",
                        lot,
                        0.05,
                        expiry.isoformat(),
                        0,
                        "XX",
                        root,
                    ]
                )
                for offset in range(-10, 11):
                    strike = spot + offset * step
                    for right in ("CE", "PE"):
                        token += 1
                        records.append(
                            [
                                token,
                                f"{root}{stamp}{int(strike)}{right}",
                                "OPTIDX",
                                "",
                                lot,
                                0.05,
                                expiry.isoformat(),
                                strike,
                                right,
                                root,
                            ]
                        )
            token += 1
            records.append([token, f"{root}-I", "FUTIDX", "", lot, 0.05, "", 0, "XX", root])
    return json.dumps({"status": "Success", "Records": records})


def chain_payload(underlying: str, expiry: date, *, with_greeks: bool) -> str:
    stamp = f"{expiry.year % 100:02d}{expiry.strftime('%b').upper()}{expiry.day:02d}"
    ts = pd.Timestamp("2025-08-14 11:00:00", tz=IST).strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[object] = []
    for offset in range(-5, 6):
        strike = MOCK_SPOT + offset * MOCK_STRIKE_STEP
        for right in ("CE", "PE"):
            symbol = f"{underlying}{stamp}{int(strike)}{right}"
            intrinsic = max(0.0, (MOCK_SPOT - strike) if right == "CE" else (strike - MOCK_SPOT))
            ltp = round(intrinsic + 45.0 + abs(offset) * 1.5, 2)
            if with_greeks:
                rows.append(
                    {
                        "symbol": symbol,
                        "timestamp": ts,
                        "ltp": ltp,
                        "volume": 12_000 + offset * 100,
                        "oi": 500_000 - abs(offset) * 1_000,
                        "prevoi": 495_000,
                        "iv": round(12.5 + abs(offset) * 0.4, 2),
                        "delta": round(0.5 - offset * 0.04, 4) * (1 if right == "CE" else -1),
                        "gamma": 0.0008,
                        "theta": -4.2,
                        "vega": 11.9,
                        "rho": 0.14,
                        "bid": round(ltp - 0.4, 2),
                        "ask": round(ltp + 0.4, 2),
                    }
                )
            else:
                rows.append(
                    [
                        symbol,
                        ts,
                        ltp,
                        12_000 + offset * 100,
                        500_000 - abs(offset) * 1_000,
                        495_000,
                        strike,
                        right,
                        expiry.strftime("%d-%m-%Y"),
                    ]
                )
    return json.dumps({"status": "Success", "Records": rows})


def expiry_payload() -> str:
    return json.dumps(
        {"status": "Success", "Records": [[e.strftime("%d-%m-%Y")] for e in MOCK_EXPIRIES]}
    )


def build_http_transport(
    calendar: MarketCalendar | None = None, *, duplicate_bars: bool = False
) -> FakeHttpTransport:
    cal = calendar or load_market_calendar()

    def token_handler(payload: Mapping[str, object]) -> HttpResponse:
        if not payload.get("username") or not payload.get("password"):
            return HttpResponse(status=400, body='{"error":"invalid_grant"}')
        return HttpResponse(
            status=200,
            body=json.dumps(
                {"access_token": MOCK_TOKEN, "token_type": "bearer", "expires_in": 3600}
            ),
        )

    def bars_handler(payload: Mapping[str, object]) -> HttpResponse:
        interval = _interval_from(str(payload.get("interval", "1min")))
        start = _from_td(str(payload.get("from", "250814T09:15:00")))
        end = _from_td(str(payload.get("to", "250814T15:30:00")))
        return HttpResponse(
            status=200,
            body=bars_payload(
                str(payload.get("symbol", "NIFTY-I")),
                interval,
                start,
                end,
                cal,
                duplicate_last=duplicate_bars,
            ),
        )

    def ticks_handler(payload: Mapping[str, object]) -> HttpResponse:
        start = _from_td(str(payload.get("from", "250814T09:15:00")))
        end = _from_td(str(payload.get("to", "250814T09:25:00")))
        return HttpResponse(
            status=200, body=ticks_payload(str(payload.get("symbol", "NIFTY-I")), start, end)
        )

    def chain_handler(payload: Mapping[str, object]) -> HttpResponse:
        expiry = pd.Timestamp(str(payload.get("expiry")), tz=None).date()
        return HttpResponse(
            status=200,
            body=chain_payload(str(payload.get("symbol", "NIFTY")), expiry, with_greeks=False),
        )

    def greeks_chain_handler(payload: Mapping[str, object]) -> HttpResponse:
        expiry = pd.Timestamp(str(payload.get("expiry")), tz=None).date()
        return HttpResponse(
            status=200,
            body=chain_payload(str(payload.get("symbol", "NIFTY")), expiry, with_greeks=True),
        )

    return FakeHttpTransport(
        handlers={
            "/token": token_handler,
            "/getbars": bars_handler,
            "/getlastnbars": bars_handler,
            "/getticks": ticks_handler,
            "/getlastnticks": ticks_handler,
            "/getAllSymbols": lambda p: HttpResponse(
                status=200, body=master_payload(str(p.get("segment", "eq")))
            ),
            "/getSymbolExpiryList": lambda p: HttpResponse(status=200, body=expiry_payload()),
            "/api/getoptionchain": chain_handler,
            "/api/getOptionChainwithGreeks": greeks_chain_handler,
            "/getIndexComponents": lambda p: HttpResponse(
                status=200,
                body=json.dumps(
                    {"status": "Success", "Records": [["RELIANCE", 9.1], ["HDFCBANK", 8.4]]}
                ),
            ),
            "/getCorpAction": lambda p: HttpResponse(
                status=200,
                body=json.dumps(
                    {
                        "status": "Success",
                        "Records": [["RELIANCE", "2025-07-10", "DIVIDEND", 10.0]],
                    }
                ),
            ),
        }
    )


def mock_config(**overrides: object) -> TrueDataConfig:
    defaults: dict[str, object] = {
        "username": "trial-user",
        "password": "trial-password",
        "sandbox": True,
        "ws_port": 8085,
        "rest_calls_per_minute": 10_000,
        "max_retries": 3,
        "backoff_base_s": 0.0,
        "backoff_max_s": 0.0,
        "heartbeat_timeout_s": 5.0,
        # The fakes are not plan-limited; depth checks are exercised explicitly
        # by tests that set their own history_days.
        "history_days": {interval: 7300 for interval in BarInterval},
    }
    defaults.update(overrides)
    return TrueDataConfig(**defaults)  # type: ignore[arg-type]


def build_mock_provider(
    *,
    config: TrueDataConfig | None = None,
    http: FakeHttpTransport | None = None,
    ws: FakeWsTransport | None = None,
    calendar: MarketCalendar | None = None,
    load_master: bool = True,
) -> TrueDataProvider:
    """A fully wired provider that never touches the network."""
    cal = calendar or load_market_calendar()
    settings = config or mock_config()
    transport = http or build_http_transport(cal)
    symbology = TrueDataSymbology()
    rest = TrueDataRestClient(settings, transport, sleep=lambda _s: None)
    stream = TrueDataStream(
        settings,
        ws or FakeWsTransport(),
        symbology,
        layout=MessageLayout(),
        recv_timeout_s=0.0,
    )
    provider = TrueDataProvider(
        settings, rest, symbology=symbology, stream=stream, calendar=cal
    )
    if load_master:
        provider.ensure_master()
    return provider


def _interval_from(vendor: str) -> BarInterval:
    for interval, code in {
        BarInterval.M1: "1min",
        BarInterval.M5: "5min",
        BarInterval.M15: "15min",
        BarInterval.M30: "30min",
        BarInterval.M60: "60min",
        BarInterval.EOD: "eod",
    }.items():
        if vendor.lower() == code:
            return interval
    return BarInterval.M1


def _from_td(value: str) -> datetime:
    return pd.to_datetime(value, format="%y%m%dT%H:%M:%S").to_pydatetime()
