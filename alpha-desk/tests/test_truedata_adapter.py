"""TrueData adapter: config, auth, REST, symbology, stream, provider.

Everything here runs against the deterministic doubles in
``alpha_desk.vendors.truedata.mock``. No network, no credentials.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import pytest

from alpha_desk.marketdata.contracts import (
    BarInterval,
    ContinuationStyle,
    Contract,
    OptionRight,
    Segment,
)
from alpha_desk.marketdata.errors import (
    AuthError,
    NoDataError,
    NotEntitledError,
    ProviderConfigError,
    SubscriptionLimitError,
)
from alpha_desk.marketdata.provider import Capability
from alpha_desk.marketdata.registry import available_providers, get_provider
from alpha_desk.vendors.truedata.auth import TokenManager
from alpha_desk.vendors.truedata.config import TrueDataConfig
from alpha_desk.vendors.truedata.mock import (
    MOCK_EXPIRIES,
    MOCK_TOKEN,
    FakeHttpTransport,
    FakeWsTransport,
    bar_frame,
    build_http_transport,
    build_mock_provider,
    greeks_frame,
    heartbeat_frame,
    market_status_frame,
    mock_config,
    touchline_frame,
    trade_frame,
)
from alpha_desk.vendors.truedata.rest import TrueDataRestClient, parse_payload
from alpha_desk.vendors.truedata.stream import EventKind, TrueDataStream
from alpha_desk.vendors.truedata.symbols import MasterEntry, SymbolFormat, TrueDataSymbology
from alpha_desk.vendors.truedata.transport import HttpResponse, RetryPolicy

IST = "Asia/Kolkata"
WINDOW = (datetime(2025, 8, 4, 9, 15), datetime(2025, 8, 14, 15, 30))


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


def test_config_refuses_plaintext_endpoints():
    with pytest.raises(ProviderConfigError, match="non-TLS"):
        TrueDataConfig(history_url="http://history.truedata.in")
    with pytest.raises(ProviderConfigError, match="websocket"):
        TrueDataConfig(ws_host="ws://push.truedata.in")


def test_config_never_exposes_the_password():
    config = TrueDataConfig(username="trial796", password="s3cret")
    redacted = config.redacted()
    assert redacted["password"] == "***"
    assert "s3cret" not in repr(config)
    assert redacted["username"] != "trial796"


def test_config_from_env_reads_plan_limits():
    config = TrueDataConfig.from_env(
        {
            "TRUEDATA_USER": "u",
            "TRUEDATA_PASSWORD": "p",
            "TRUEDATA_WS_PORT": "8084",
            "TRUEDATA_MAX_LIVE_SYMBOLS": "250",
            "TRUEDATA_HISTORY_DAYS_M1": "1825",
            "TRUEDATA_ENTITLED_GREEKS": "false",
            "TRUEDATA_SEGMENTS": "FUT,OPT",
        }
    )
    assert config.ws_url.endswith(":8084")
    assert config.max_live_symbols == 250
    assert config.history_days[BarInterval.M1] == 1825
    assert config.entitled_greeks is False
    assert config.segments == (Segment.FUTURES, Segment.OPTIONS)


def test_hist_url_alias_is_accepted():
    config = TrueDataConfig.from_env(
        {
            "TRUEDATA_USER": "u",
            "TRUEDATA_PASSWORD": "p",
            "TRUEDATA_HIST_URL": "https://history.truedata.in/",
        }
    )
    assert config.history_url == "https://history.truedata.in"


def test_missing_credentials_fail_before_any_network_call():
    with pytest.raises(ProviderConfigError, match="TRUEDATA_USER"):
        TrueDataConfig(username="", password="").require_credentials()


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_token_is_cached_and_refreshed_before_expiry():
    clock = _Clock()
    transport = build_http_transport()
    manager = TokenManager(mock_config(), transport, clock=clock)

    assert manager.access_token() == MOCK_TOKEN
    manager.access_token()
    assert transport.count("/token") == 1

    clock.now += 3_600 - 299  # inside the refresh margin
    manager.access_token()
    assert transport.count("/token") == 2
    assert manager.auth_count == 2


def test_bad_credentials_raise_auth_error_without_retrying():
    transport = build_http_transport()
    config = mock_config(username="", password="")
    manager = TokenManager(config, transport)
    with pytest.raises(ProviderConfigError):
        manager.access_token()

    manager = TokenManager(mock_config(), transport)
    transport.handlers["/token"] = lambda _p: HttpResponse(
        status=401, body='{"error":"invalid_grant"}'
    )
    with pytest.raises(AuthError):
        manager.access_token()


def test_missing_expires_in_falls_back_to_one_hour():
    transport = build_http_transport()
    transport.handlers["/token"] = lambda _p: HttpResponse(
        status=200, body=json.dumps({"access_token": "abc"})
    )
    clock = _Clock()
    manager = TokenManager(mock_config(), transport, clock=clock)
    manager.access_token()
    assert manager.token_expires_in == pytest.approx(3600.0)


# --------------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------------- #


def _client(transport: FakeHttpTransport | None = None, **config_overrides) -> TrueDataRestClient:
    return TrueDataRestClient(
        mock_config(**config_overrides),
        transport or build_http_transport(),
        sleep=lambda _s: None,
    )


def test_expired_token_triggers_exactly_one_reauth_and_replay():
    transport = build_http_transport()
    client = _client(transport)
    client.get_bars("NIFTY-I", BarInterval.M15, *WINDOW)
    calls_before = transport.count("/token")

    original = transport.handlers["/getbars"]
    state = {"first": True}

    def flaky(params):
        if state["first"]:
            state["first"] = False
            return HttpResponse(status=401, body='{"error":"invalid_token"}')
        return original(params)

    transport.handlers["/getbars"] = flaky
    frame = client.get_bars("NIFTY-I", BarInterval.M15, *WINDOW)
    assert not frame.empty
    assert transport.count("/token") == calls_before + 1


def test_transient_5xx_is_retried_then_succeeds():
    transport = build_http_transport()
    transport.fail_times["/getbars"] = [1]
    client = _client(transport)
    frame = client.get_bars("NIFTY-I", BarInterval.M15, *WINDOW)
    assert not frame.empty
    assert transport.count("/getbars") == 2


def test_persistent_5xx_gives_up_after_max_retries():
    transport = build_http_transport()
    transport.fail_times["/getbars"] = [1, 2, 3, 4, 5]
    client = _client(transport)
    with pytest.raises(Exception, match="vendor error"):
        client.get_bars("NIFTY-I", BarInterval.M15, *WINDOW)
    assert transport.count("/getbars") == 3


def test_no_data_and_not_entitled_are_distinct():
    transport = build_http_transport()
    client = _client(transport)

    transport.handlers["/getbars"] = lambda _p: HttpResponse(
        status=200, body='{"status":"No data available","Records":[]}'
    )
    with pytest.raises(NoDataError):
        client.get_bars("NIFTY-I", BarInterval.M15, *WINDOW)

    transport.handlers["/getbars"] = lambda _p: HttpResponse(
        status=200, body='{"status":"Error","message":"Symbol not subscribed"}'
    )
    with pytest.raises(NotEntitledError):
        client.get_bars("NIFTY-I", BarInterval.M15, *WINDOW)


def test_rest_records_call_stats():
    client = _client()
    client.get_bars("NIFTY-I", BarInterval.M15, *WINDOW)
    stat = client.calls[-1]
    assert stat.endpoint == "getbars"
    assert stat.rows > 0


def test_expiry_list_is_parsed_to_dates():
    client = _client()
    assert client.get_symbol_expiry_list("NIFTY", "fo") == list(MOCK_EXPIRIES)


def test_greeks_are_refused_when_the_plan_excludes_them():
    client = _client(entitled_greeks=False)
    with pytest.raises(NotEntitledError, match="greeks"):
        client.get_option_chain_with_greeks("NIFTY", MOCK_EXPIRIES[0])


@pytest.mark.parametrize(
    "body",
    [
        '{"status":"Success","Records":[["2025-08-14T09:15:00",100,101,99,100.5,10,1000]]}',
        '{"status":"Success","Records":[{"timestamp":"2025-08-14T09:15:00","open":100,'
        '"high":101,"low":99,"close":100.5,"volume":10,"oi":1000}]}',
        "timestamp,open,high,low,close,volume,oi\n2025-08-14T09:15:00,100,101,99,100.5,10,1000\n",
    ],
)
def test_payload_shapes_all_yield_canonical_columns(body):
    frame = parse_payload(
        body, ["ts", "open", "high", "low", "close", "volume", "oi"], endpoint="getbars"
    )
    assert list(frame.columns)[:7] == ["ts", "open", "high", "low", "close", "volume", "oi"]
    assert len(frame) == 1


def test_retry_policy_backs_off_with_a_cap():
    policy = RetryPolicy(base_s=1.0, max_s=8.0, jitter=False)
    assert [policy.delay_for(n) for n in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert policy.delay_for(1, retry_after=30.0) == 8.0


# --------------------------------------------------------------------------- #
# symbology
# --------------------------------------------------------------------------- #


def test_compact_option_round_trip():
    symbology = TrueDataSymbology()
    contract = Contract(
        symbol="NIFTY",
        segment=Segment.OPTIONS,
        expiry=date(2025, 8, 28),
        strike=24000.0,
        right=OptionRight.PUT,
    )
    vendor = symbology.to_vendor(contract)
    assert vendor == "NIFTY25AUG2824000PE"
    parsed = symbology.from_vendor(vendor)
    assert (parsed.symbol, parsed.strike, parsed.right, parsed.expiry) == (
        "NIFTY",
        24000.0,
        OptionRight.PUT,
        date(2025, 8, 28),
    )


def test_future_and_continuous_symbols():
    symbology = TrueDataSymbology()
    monthly = Contract(symbol="NIFTY", segment=Segment.FUTURES, expiry=date(2025, 9, 25))
    assert symbology.to_vendor(monthly) == "NIFTY25SEP25FUT"

    continuous = Contract(
        symbol="NIFTY", segment=Segment.FUTURES, continuation=ContinuationStyle.VENDOR_SPLICED
    )
    assert symbology.to_vendor(continuous) == "NIFTY-I"
    parsed = symbology.from_vendor("NIFTY-I")
    assert parsed.is_continuous and parsed.expiry is None


def test_long_form_symbols_are_parsed():
    symbology = TrueDataSymbology()
    option = symbology.from_vendor("OPTIDX_NIFTY_28AUG2025_CE_24000")
    assert (option.right, option.strike, option.expiry) == (
        OptionRight.CALL,
        24000.0,
        date(2025, 8, 28),
    )
    future = symbology.from_vendor("FUTIDX_NIFTY_28AUG2025_XX_0")
    assert future.segment is Segment.FUTURES and future.expiry == date(2025, 8, 28)


def test_index_names_map_both_ways():
    symbology = TrueDataSymbology()
    spot = Contract(symbol="BANKNIFTY", segment=Segment.INDEX)
    assert symbology.to_vendor(spot) == "NIFTY BANK"
    assert symbology.from_vendor("NIFTY BANK").symbol == "BANKNIFTY"


def test_master_entry_wins_over_the_template():
    symbology = TrueDataSymbology(fmt=SymbolFormat(future="{root}-WRONG-{yy}"))
    symbology.load_master(
        [
            MasterEntry(
                vendor_symbol="NIFTY25SEP25FUT",
                segment=Segment.FUTURES,
                root="NIFTY",
                expiry=date(2025, 9, 25),
                lot_size=75,
                tick_size=0.05,
                vendor_token="4321",
            )
        ]
    )
    contract = Contract(symbol="NIFTY", segment=Segment.FUTURES, expiry=date(2025, 9, 25))
    enriched = symbology.enrich(contract)
    assert enriched.vendor_symbol == "NIFTY25SEP25FUT"
    assert (enriched.lot_size, enriched.tick_size, enriched.vendor_token) == (75, 0.05, "4321")


def test_unexpressible_contract_raises():
    symbology = TrueDataSymbology()
    with pytest.raises(Exception):
        symbology.to_vendor(Contract(symbol="X", segment=Segment.OPTIONS))


# --------------------------------------------------------------------------- #
# stream
# --------------------------------------------------------------------------- #


def _stream(ws: FakeWsTransport, **overrides) -> TrueDataStream:
    symbology = TrueDataSymbology()
    return TrueDataStream(mock_config(**overrides), ws, symbology, recv_timeout_s=0.0)


def test_websocket_requires_credentials_in_the_url():
    stream = _stream(FakeWsTransport(), username="", password="")
    with pytest.raises(ProviderConfigError):
        stream.connect()


def test_subscription_limit_is_enforced_locally():
    stream = _stream(FakeWsTransport(), max_live_symbols=1)
    stream.connect()
    stream.subscribe([Contract(symbol="RELIANCE", segment=Segment.EQUITY)])
    with pytest.raises(SubscriptionLimitError, match="licensed limit"):
        stream.subscribe([Contract(symbol="TCS", segment=Segment.EQUITY)])


def test_router_maps_every_documented_message_type():
    ws = FakeWsTransport()
    stream = _stream(ws)
    stream.connect()
    stream.subscribe([Contract(symbol="RELIANCE", segment=Segment.EQUITY)])

    touchline = stream._route(ws.frames.pop(0))
    assert touchline[0].kind is EventKind.TOUCHLINE

    ts = "2025-08-14T09:16:00"
    tick = stream._route(trade_frame("1001", ts, 1400.5, sequence=1))[0]
    assert tick.kind is EventKind.TICK
    assert tick.payload.contract_id == "NSE:EQ:RELIANCE"
    assert tick.payload.bid < tick.payload.ask

    bar = stream._route(bar_frame("1001", ts, 1401.0))[0]
    assert bar.kind is EventKind.BAR and bar.payload.interval is BarInterval.M1

    greeks = stream._route(greeks_frame("1001", ts))[0]
    assert greeks.kind is EventKind.GREEKS and greeks.payload.iv == 13.45

    assert stream._route(heartbeat_frame())[0].kind is EventKind.HEARTBEAT
    status = stream._route(market_status_frame())[0]
    assert status.kind is EventKind.MARKET_STATUS
    assert stream.market_status().is_open("NSE")


def test_sequence_gaps_and_unknown_keys_are_counted_not_fatal():
    ws = FakeWsTransport()
    stream = _stream(ws)
    stream.connect()
    stream.subscribe([Contract(symbol="RELIANCE", segment=Segment.EQUITY)])
    stream._route(ws.frames.pop(0))

    ts = "2025-08-14T09:16:00"
    stream._route(trade_frame("1001", ts, 100.0, sequence=1))
    stream._route(trade_frame("1001", ts, 100.5, sequence=7))
    assert stream.stats.sequence_gaps == 1

    events = stream._route(json.dumps({"somethingnew": [1, 2, 3]}))
    assert events[0].kind is EventKind.UNROUTED
    assert stream.stats.unrouted == {"somethingnew": 1}

    stream._route("not json at all")
    assert stream.stats.parse_errors == 1


def test_reconnect_replays_subscriptions():
    ws = FakeWsTransport()
    stream = _stream(ws)
    stream.connect()
    contract = Contract(symbol="RELIANCE", segment=Segment.EQUITY)
    stream.subscribe([contract])
    assert stream.subscribed_symbols == ["RELIANCE"]

    ws.close()
    stream._reconnect(0)

    assert ws.connects == 2
    assert stream.stats.reconnects == 1
    assert stream.subscribed_symbols == ["RELIANCE"]
    assert json.loads(ws.sent[-1])["method"] == "addsymbol"


def test_latency_is_measured_from_exchange_timestamp():
    ws = FakeWsTransport()
    stream = _stream(ws)
    stream.connect()
    stream.subscribe([Contract(symbol="RELIANCE", segment=Segment.EQUITY)])
    stream._route(ws.frames.pop(0))

    recent = (pd.Timestamp.now(tz=IST) - pd.Timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%S")
    event = stream._route(trade_frame("1001", recent, 100.0))[0]
    assert 1.0 < event.latency_s < 10.0
    assert stream.stats.latency_p(0.5) is not None


# --------------------------------------------------------------------------- #
# provider
# --------------------------------------------------------------------------- #


def test_provider_loads_master_and_resolves_option_universe():
    provider = build_mock_provider()
    assert provider.symbology.master_size > 80

    universe = provider.option_universe(
        "NIFTY", MOCK_EXPIRIES[0], spot=24_012.0, strikes_each_side=2
    )
    assert len(universe) == 10
    assert {c.right for c in universe} == {OptionRight.CALL, OptionRight.PUT}
    assert all(c.vendor_symbol.startswith("NIFTY25AUG28") for c in universe)
    assert all(c.lot_size == 75 for c in universe)


def test_provider_returns_normalised_bars_with_provenance():
    provider = build_mock_provider()
    contract = provider.continuous_future("NIFTY")
    outcome = provider.fetch_bars_detailed(contract, BarInterval.M15, *WINDOW)

    frame = outcome.frame
    assert not frame.empty
    assert set(frame["source"]) == {"truedata"}
    assert frame["ts"].is_monotonic_increasing
    assert str(frame["ts"].dt.tz) == IST
    assert frame["contract_id"].nunique() == 1
    assert outcome.requests >= 1


def test_provider_chunks_long_windows():
    provider = build_mock_provider()
    contract = provider.continuous_future("NIFTY")
    outcome = provider.fetch_bars_detailed(
        contract, BarInterval.M1, datetime(2025, 5, 1), datetime(2025, 8, 14, 15, 30)
    )
    assert outcome.requests >= 4
    assert not outcome.frame["ts"].duplicated().any()


def test_duplicate_bars_from_the_vendor_are_collapsed():
    provider = build_mock_provider(http=build_http_transport(duplicate_bars=True))
    contract = provider.continuous_future("NIFTY")
    outcome = provider.fetch_bars_detailed(contract, BarInterval.M15, *WINDOW)
    assert not outcome.frame["ts"].duplicated().any()
    assert any("duplicate" in note for note in outcome.notes)


def test_history_beyond_the_plan_is_refused_not_truncated():
    provider = build_mock_provider(
        config=mock_config(history_days={BarInterval.M1: 30}),
        load_master=False,
    )
    contract = provider.continuous_future("NIFTY")
    with pytest.raises(NotEntitledError, match="out of reach"):
        provider.fetch_bars_detailed(
            contract, BarInterval.M1, datetime(2020, 1, 1), datetime(2020, 2, 1)
        )


def test_partial_window_is_clamped_with_a_note():
    provider = build_mock_provider(config=mock_config(history_days={BarInterval.M15: 10}))
    contract = provider.continuous_future("NIFTY")
    now = pd.Timestamp.now(tz=IST)
    outcome = provider.fetch_bars_detailed(
        contract,
        BarInterval.M15,
        (now - pd.Timedelta(days=60)).to_pydatetime(),
        now.to_pydatetime(),
    )
    assert any("clamped" in note for note in outcome.notes)
    assert not outcome.frame.empty


def test_option_chain_with_greeks_is_normalised():
    provider = build_mock_provider()
    outcome = provider.fetch_option_chain_detailed(
        "NIFTY", MOCK_EXPIRIES[0], with_greeks=True
    )
    frame = outcome.frame
    assert len(frame) == 22
    assert set(frame["right"]) == {"CE", "PE"}
    assert frame["iv"].gt(0).all()
    assert frame["delta"].abs().le(1.0).all()
    assert set(frame["expiry"]) == {MOCK_EXPIRIES[0]}


def test_descriptor_reflects_plan_entitlements():
    entitled = build_mock_provider(load_master=False)
    assert entitled.descriptor.supports(Capability.OPTION_CHAIN_GREEKS)

    restricted = build_mock_provider(
        config=mock_config(entitled_greeks=False, entitled_ticks=False), load_master=False
    )
    assert not restricted.descriptor.supports(Capability.OPTION_CHAIN_GREEKS)
    assert not restricted.descriptor.supports(Capability.HISTORICAL_TICKS)
    with pytest.raises(NotEntitledError):
        restricted.fetch_option_chain("NIFTY", MOCK_EXPIRIES[0], with_greeks=True)


def test_registry_exposes_the_mock_provider_without_credentials():
    assert "truedata" in available_providers()
    provider = get_provider("truedata_mock", load_master=False)
    assert provider.descriptor.name == "truedata"


def test_subscribe_uses_resolved_vendor_symbols():
    ws = FakeWsTransport(on_subscribe=lambda symbols: [touchline_frame(symbols)])
    provider = build_mock_provider(ws=ws)
    universe = provider.option_universe(
        "NIFTY", MOCK_EXPIRIES[0], spot=24_000.0, strikes_each_side=1
    )
    provider.subscribe(universe)
    sent = json.loads(ws.sent[-1])
    assert sent["method"] == "addsymbol"
    assert "NIFTY25AUG2824000CE" in sent["symbols"]
