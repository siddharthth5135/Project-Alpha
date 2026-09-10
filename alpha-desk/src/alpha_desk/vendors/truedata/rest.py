"""TrueData REST client.

Covers the history, analytics and greeks hosts. Three defensive choices matter
here:

* Response shape is negotiated, not assumed. TrueData answers with positional
  ``Records`` arrays, keyed records, or CSV depending on endpoint and the
  ``response`` parameter, so every reader accepts all three and maps positions
  through a named layout.
* ``status`` text is classified. "No data" is a :class:`NoDataError`, "not
  subscribed" is a :class:`NotEntitledError`, and neither is retried.
* Requests are paced by our own limiter before they leave the process, and 401
  triggers exactly one token refresh and one replay.
"""

from __future__ import annotations

import csv
import io
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from alpha_desk.execution.rate_limiter import RateLimiter
from alpha_desk.marketdata.contracts import BarInterval, ContinuationStyle, OptionRight, Segment
from alpha_desk.marketdata.errors import (
    AuthError,
    DataQualityError,
    MarketDataError,
    NoDataError,
    NotEntitledError,
    RateLimitError,
    UpstreamError,
)
from alpha_desk.vendors.truedata.auth import TokenManager
from alpha_desk.vendors.truedata.config import TrueDataConfig
from alpha_desk.vendors.truedata.symbols import MasterEntry, TrueDataSymbology
from alpha_desk.vendors.truedata.transport import (
    HttpResponse,
    HttpTransport,
    RetryPolicy,
    classify_response,
)

TD_TIME_FORMAT = "%y%m%dT%H:%M:%S"

INTERVAL_TO_VENDOR: dict[BarInterval, str] = {
    BarInterval.M1: "1min",
    BarInterval.M2: "2min",
    BarInterval.M3: "3min",
    BarInterval.M5: "5min",
    BarInterval.M10: "10min",
    BarInterval.M15: "15min",
    BarInterval.M30: "30min",
    BarInterval.M60: "60min",
    BarInterval.EOD: "eod",
    BarInterval.TICK: "tick",
}

BAR_LAYOUT = ["ts", "open", "high", "low", "close", "volume", "oi"]
TICK_LAYOUT = ["ts", "ltp", "volume", "oi"]
TICK_BIDASK_LAYOUT = ["ts", "ltp", "volume", "oi", "bid", "bid_qty", "ask", "ask_qty"]
CHAIN_LAYOUT = [
    "vendor_symbol",
    "ts",
    "ltp",
    "volume",
    "oi",
    "prev_oi",
    "strike",
    "right",
    "expiry",
]
# Live analytics returns one row per strike with CE and PE columns side-by-side.
CHAIN_WIDE_LAYOUT = [
    "underlying",
    "expiry",
    "ts",
    "ce_volume",
    "ce_ltp",
    "ce_bid",
    "ce_bid_qty",
    "ce_ask",
    "ce_ask_qty",
    "ce_oi",
    "ce_prev_oi",
    "strike",
    "pe_ltp",
    "pe_bid",
    "pe_ask",
    "pe_oi",
    "pe_prev_oi",
    "pe_volume",
    "pe_ltp_dup",
    "pe_total_volume",
    "updated_ts",
]
MASTER_LAYOUT = [
    "vendor_token",
    "vendor_symbol",
    "series",
    "isin",
    "lot_size",
    "tick_size",
    "expiry",
    "strike",
    "right",
    "underlying",
]

# Vendor spellings we normalise on the way in. Anything unmapped is kept as-is
# so an added field is visible in the raw frame rather than dropped.
FIELD_ALIASES = {
    "time": "ts",
    "timestamp": "ts",
    "datetime": "ts",
    "date": "ts",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "vol": "volume",
    "volume": "volume",
    "openinterest": "oi",
    "open_interest": "oi",
    "oi": "oi",
    "previousoi": "prev_oi",
    "prev_oi": "prev_oi",
    "prevoi": "prev_oi",
    "ltp": "ltp",
    "lasttradedprice": "ltp",
    "ltq": "ltq",
    "lasttradedqty": "ltq",
    "atp": "atp",
    "avgtradedprice": "atp",
    "bid": "bid",
    "bidprice": "bid",
    "bidqty": "bid_qty",
    "bid_qty": "bid_qty",
    "ask": "ask",
    "askprice": "ask",
    "askqty": "ask_qty",
    "ask_qty": "ask_qty",
    "strikeprice": "strike",
    "strike": "strike",
    "optiontype": "right",
    "series": "right_or_series",
    "expiry": "expiry",
    "expirydate": "expiry",
    "symbol": "vendor_symbol",
    "symbolid": "vendor_token",
    "symbolalias": "symbol_alias",
    "underlying": "underlying",
    "metastocksymbol": "meta_symbol",
    "lotsize": "lot_size",
    "ticksize": "tick_size",
    "iv": "iv",
    "impliedvolatility": "iv",
    "delta": "delta",
    "gamma": "gamma",
    "theta": "theta",
    "vega": "vega",
    "rho": "rho",
}

_NO_DATA_TOKENS = ("no data", "nodata", "no record", "not found", "empty")
_NOT_ENTITLED_TOKENS = (
    "not subscribed",
    "not entitled",
    "no permission",
    "unauthorised symbol",
    "unauthorized symbol",
    "invalid subscription",
    "upgrade",
)


@dataclass
class RestCallStat:
    endpoint: str
    status: int
    elapsed_s: float
    rows: int
    attempts: int


class TrueDataRestClient:
    def __init__(
        self,
        config: TrueDataConfig,
        transport: HttpTransport,
        *,
        tokens: TokenManager | None = None,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._transport = transport
        self._tokens = tokens or TokenManager(config, transport, retry=retry, clock=clock)
        self._retry = retry or RetryPolicy(
            max_attempts=config.max_retries,
            base_s=config.backoff_base_s,
            max_s=config.backoff_max_s,
            sleep=sleep,
        )
        self._limiter = RateLimiter(max_calls=config.rest_calls_per_minute, per_seconds=60.0)
        self._sleep = sleep
        self._clock = clock
        self.calls: list[RestCallStat] = []

    @property
    def tokens(self) -> TokenManager:
        return self._tokens

    # -- history ----------------------------------------------------------- #

    def get_bars(
        self,
        vendor_symbol: str,
        interval: BarInterval,
        start: datetime,
        end: datetime,
        *,
        bidask: bool = False,
    ) -> pd.DataFrame:
        params: dict[str, object] = {
            "symbol": vendor_symbol,
            "from": _td_time(start),
            "to": _td_time(end),
            "response": "json",
            "interval": INTERVAL_TO_VENDOR[interval],
        }
        if bidask:
            params["bidask"] = 1
        body = self._get(self._config.history_url, "/getbars", params)
        return self._records(body, BAR_LAYOUT, endpoint="getbars")

    def get_last_n_bars(
        self, vendor_symbol: str, interval: BarInterval, count: int
    ) -> pd.DataFrame:
        params = {
            "symbol": vendor_symbol,
            "response": "json",
            "interval": INTERVAL_TO_VENDOR[interval],
            "nbars": int(count),
        }
        body = self._get(self._config.history_url, "/getlastnbars", params)
        return self._records(body, BAR_LAYOUT, endpoint="getlastnbars")

    def get_ticks(
        self,
        vendor_symbol: str,
        start: datetime,
        end: datetime,
        *,
        bidask: bool = True,
    ) -> pd.DataFrame:
        if not self._config.entitled_ticks:
            raise NotEntitledError("tick history is not enabled on this TrueData plan")
        params: dict[str, object] = {
            "symbol": vendor_symbol,
            "from": _td_time(start),
            "to": _td_time(end),
            "response": "json",
        }
        if bidask:
            params["bidask"] = 1
        layout = TICK_BIDASK_LAYOUT if bidask else TICK_LAYOUT
        body = self._get(self._config.history_url, "/getticks", params)
        return self._records(body, layout, endpoint="getticks")

    def get_last_n_ticks(self, vendor_symbol: str, count: int) -> pd.DataFrame:
        params = {"symbol": vendor_symbol, "response": "json", "nticks": int(count)}
        body = self._get(self._config.history_url, "/getlastnticks", params)
        return self._records(body, TICK_BIDASK_LAYOUT, endpoint="getlastnticks")

    # -- reference --------------------------------------------------------- #

    def get_all_symbols(self, vendor_segment: str) -> pd.DataFrame:
        """Download the symbol master.

        Trial and production accounts serve the catalog from ``api.truedata.in``
        with credentials in the query string and a CSV body. The history host
        answers other endpoints but returns 404 for ``getAllSymbols``.
        """
        params = {
            "segment": vendor_segment,
            "user": self._config.username,
            "password": self._config.password,
            "csv": "true",
            "allexpiry": "false",
            "csvheader": "true",
            "token": "true",
            "underlying": "true",
        }
        try:
            body = self._get_catalog("/getAllSymbols", params)
            return self._records(body, MASTER_LAYOUT, endpoint="getAllSymbols")
        except UpstreamError:
            legacy = {"segment": vendor_segment, "response": "json"}
            body = self._get(self._config.history_url, "/getAllSymbols", legacy)
            return self._records(body, MASTER_LAYOUT, endpoint="getAllSymbols")

    def get_symbol_expiry_list(self, symbol: str, vendor_segment: str = "fo") -> list[date]:
        params = {"symbol": symbol, "segment": vendor_segment, "response": "json"}
        body = self._get(self._config.history_url, "/getSymbolExpiryList", params)
        frame = self._records(body, ["expiry"], endpoint="getSymbolExpiryList")
        if frame.empty:
            return []
        column = "expiry" if "expiry" in frame.columns else frame.columns[0]
        parsed = pd.to_datetime(frame[column], errors="coerce", dayfirst=True)
        return sorted({d.date() for d in parsed.dropna()})

    def get_bhavcopy(self, vendor_segment: str, day: date) -> pd.DataFrame:
        params = {
            "segment": vendor_segment,
            "date": day.strftime("%y%m%d"),
            "response": "json",
        }
        body = self._get(self._config.history_url, "/getbhavcopy", params)
        return self._records(body, [], endpoint="getbhavcopy")

    def get_bhavcopy_status(self, vendor_segment: str) -> pd.DataFrame:
        params = {"segment": vendor_segment, "response": "json"}
        body = self._get(self._config.history_url, "/getbhavcopystatus", params)
        return self._records(body, [], endpoint="getbhavcopystatus")

    def get_corporate_actions(self, symbol: str) -> pd.DataFrame:
        params = {"symbol": symbol, "response": "json"}
        body = self._get(self._config.history_url, "/getCorpAction", params)
        return self._records(body, [], endpoint="getCorpAction")

    def get_corporate_actions_range(self, start: date, end: date) -> pd.DataFrame:
        params = {
            "from": start.strftime("%y%m%d"),
            "to": end.strftime("%y%m%d"),
            "response": "json",
        }
        body = self._get(self._config.history_url, "/getCorpActionRange", params)
        return self._records(body, [], endpoint="getCorpActionRange")

    def get_symbol_name_changes(self, start: date, end: date) -> pd.DataFrame:
        params = {
            "from": start.strftime("%y%m%d"),
            "to": end.strftime("%y%m%d"),
            "response": "json",
        }
        body = self._get(self._config.history_url, "/getSymbolNameChange", params)
        return self._records(body, [], endpoint="getSymbolNameChange")

    def get_index_components(self, index_name: str) -> pd.DataFrame:
        params = {"indexname": index_name, "response": "json"}
        body = self._get(self._config.history_url, "/getIndexComponents", params)
        return self._records(body, [], endpoint="getIndexComponents")

    def get_indices(self) -> pd.DataFrame:
        body = self._get(self._config.history_url, "/getIndices", {"response": "json"})
        return self._records(body, [], endpoint="getIndices")

    # -- options ----------------------------------------------------------- #

    def get_option_chain(self, underlying: str, expiry: date) -> pd.DataFrame:
        params = {
            "symbol": underlying,
            "expiry": expiry.strftime("%d-%m-%Y"),
            "response": "json",
        }
        body = self._get(self._config.analytics_url, "/api/getoptionchain", params)
        frame = self._records(body, CHAIN_LAYOUT, endpoint="getoptionchain")
        return unpack_option_chain(frame)

    def get_option_chain_with_greeks(self, underlying: str, expiry: date) -> pd.DataFrame:
        if not self._config.entitled_greeks:
            raise NotEntitledError("greeks are not enabled on this TrueData plan")
        params = {
            "symbol": underlying,
            "expiry": expiry.strftime("%d-%m-%Y"),
            "response": "json",
        }
        body = self._get(self._config.greeks_url, "/api/getOptionChainwithGreeks", params)
        frame = self._records(body, CHAIN_LAYOUT, endpoint="getOptionChainwithGreeks")
        return unpack_option_chain(frame)

    def get_ltp_with_greeks(
        self, underlying: str, expiry: date, strike: float, right: OptionRight
    ) -> pd.DataFrame:
        if not self._config.entitled_greeks:
            raise NotEntitledError("greeks are not enabled on this TrueData plan")
        params = {
            "symbol": underlying,
            "expiry": expiry.strftime("%d-%m-%Y"),
            "strike": _strike_param(strike),
            "series": right.value,
            "response": "json",
        }
        body = self._get(self._config.greeks_url, "/api/getLTPwithGreeks", params)
        return self._records(body, [], endpoint="getLTPwithGreeks")

    def get_tick_history_with_greeks(
        self, underlying: str, expiry: date, strike: float, right: OptionRight
    ) -> pd.DataFrame:
        if not self._config.entitled_greeks:
            raise NotEntitledError("greeks are not enabled on this TrueData plan")
        params = {
            "symbol": underlying,
            "expiry": expiry.strftime("%d-%m-%Y"),
            "strike": _strike_param(strike),
            "series": right.value,
            "response": "json",
        }
        body = self._get(self._config.greeks_url, "/api/getTickHistorywithGreeks", params)
        return self._records(body, [], endpoint="getTickHistorywithGreeks")

    def get_ltp_bulk(self, vendor_symbols: Sequence[str]) -> pd.DataFrame:
        params = {"symbol": ",".join(vendor_symbols), "response": "json"}
        body = self._get(self._config.history_url, "/getLTPBulk", params)
        return self._records(body, [], endpoint="getLTPBulk")

    # -- master ------------------------------------------------------------ #

    def load_symbology(
        self, symbology: TrueDataSymbology, vendor_segments: Sequence[str]
    ) -> dict[str, int]:
        """Download and index the symbol master for the given vendor segments."""
        loaded: dict[str, int] = {}
        for segment in vendor_segments:
            frame = self.get_all_symbols(segment)
            entries = master_entries(frame, symbology)
            symbology.load_master(entries)
            loaded[segment] = len(entries)
        return loaded

    # -- plumbing ---------------------------------------------------------- #

    def _get(self, base: str, path: str, params: Mapping[str, object]) -> str:
        url = f"{base}{path}"
        attempts = 0
        refreshed = False
        last_error: MarketDataError | None = None

        while attempts < max(1, self._retry.max_attempts):
            attempts += 1
            self._throttle()
            headers = {"Authorization": self._tokens.bearer()}
            response = self._transport.request(
                "GET",
                url,
                params=params,
                headers=headers,
                timeout=self._config.request_timeout_s,
            )
            try:
                classify_response(response, context=f"truedata {path.lstrip('/')}")
            except AuthError:
                if refreshed:
                    raise
                refreshed = True
                self._tokens.invalidate()
                continue
            except RateLimitError as exc:
                last_error = exc
                if attempts >= self._retry.max_attempts:
                    raise
                self._retry.wait(attempts, exc.retry_after)
                continue
            except UpstreamError as exc:
                last_error = exc
                if attempts >= self._retry.max_attempts:
                    raise
                self._retry.wait(attempts)
                continue

            self._record(path, response, attempts)
            return response.body

        raise UpstreamError(f"truedata {path}: exhausted retries ({last_error})")

    def _get_catalog(self, path: str, params: Mapping[str, object]) -> str:
        """Fetch a credential-in-query catalog endpoint (no bearer token)."""
        self._throttle()
        url = f"{self._config.symbols_url}{path}"
        response = self._transport.request(
            "GET",
            url,
            params=params,
            timeout=self._config.request_timeout_s,
        )
        if not response.ok:
            raise UpstreamError(
                f"truedata {path}: rejected ({response.status}) {response.body[:240]}"
            )
        self._record(path, response, attempts=1)
        return response.body

    def _throttle(self) -> None:
        """Pace outgoing calls; a backfill should wait, not fail."""
        deadline = self._clock() + 65.0
        while not self._limiter.allow():
            if self._clock() > deadline:
                raise RateLimitError("local rate limiter could not admit the request within 65s")
            self._sleep(0.05)

    def _record(self, path: str, response: HttpResponse, attempts: int) -> None:
        self.calls.append(
            RestCallStat(
                endpoint=path.lstrip("/"),
                status=response.status,
                elapsed_s=response.elapsed_s,
                rows=0,
                attempts=attempts,
            )
        )

    def _records(self, body: str, layout: Sequence[str], *, endpoint: str) -> pd.DataFrame:
        frame = parse_payload(body, layout, endpoint=endpoint)
        if self.calls:
            self.calls[-1].rows = len(frame)
        return frame


# --------------------------------------------------------------------------- #
# payload parsing
# --------------------------------------------------------------------------- #


def unpack_option_chain(frame: pd.DataFrame) -> pd.DataFrame:
    """Expand TrueData wide chain rows into one row per CE/PE leg."""
    if frame.empty:
        return frame

    if "right" in frame.columns:
        rights = frame["right"].astype(str).str.upper().str.strip()
        if rights.isin({OptionRight.CE.value, OptionRight.PE.value}).any():
            return frame

    if "strike" not in frame.columns or "ce_ltp" not in frame.columns:
        return frame

    expiry_col = frame["expiry"] if "expiry" in frame.columns else None
    ts_col = frame["ts"] if "ts" in frame.columns else pd.Timestamp.now(tz="Asia/Kolkata")
    legs: list[pd.DataFrame] = []
    for side, prefix in (("CE", "ce_"), ("PE", "pe_")):
        leg = pd.DataFrame(
            {
                "ts": ts_col,
                "expiry": expiry_col,
                "strike": frame["strike"],
                "right": side,
                "ltp": frame.get(f"{prefix}ltp"),
                "volume": frame.get(f"{prefix}volume", frame.get(f"{prefix}total_volume", 0)),
                "oi": frame.get(f"{prefix}oi", 0),
                "prev_oi": frame.get(f"{prefix}prev_oi", 0),
                "bid": frame.get(f"{prefix}bid"),
                "ask": frame.get(f"{prefix}ask"),
            }
        )
        legs.append(leg)
    return pd.concat(legs, ignore_index=True)


def parse_payload(body: str, layout: Sequence[str], *, endpoint: str) -> pd.DataFrame:
    """Decode a TrueData body into a frame with canonical column names.

    Accepts positional ``Records``, keyed records, a bare list, or CSV, because
    the shape varies by endpoint and by the ``response`` parameter.
    """
    text = (body or "").strip()
    if not text:
        raise NoDataError(f"{endpoint}: empty response body")

    if text[0] in "{[":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UpstreamError(f"{endpoint}: malformed JSON body") from exc
        return _frame_from_json(payload, layout, endpoint=endpoint)

    return _frame_from_csv(text, endpoint=endpoint)


def _frame_from_json(payload: object, layout: Sequence[str], *, endpoint: str) -> pd.DataFrame:
    if isinstance(payload, list):
        return _rename(pd.DataFrame(payload))

    if not isinstance(payload, dict):
        raise UpstreamError(f"{endpoint}: unexpected payload type {type(payload).__name__}")

    status = str(payload.get("status") or payload.get("Status") or "").strip()
    message = str(payload.get("message") or payload.get("Message") or "")
    _classify_status(status, message, endpoint=endpoint)

    records = None
    for key in ("Records", "records", "data", "Data", "result", "Result", "symbollist"):
        if key in payload:
            records = payload[key]
            break
    if records is None:
        scalar = {k: v for k, v in payload.items() if not isinstance(v, (list, dict))}
        if scalar:
            return _rename(pd.DataFrame([scalar]))
        raise UpstreamError(f"{endpoint}: no records in response (keys={sorted(payload)[:8]})")

    if records in ([], None, ""):
        raise NoDataError(f"{endpoint}: vendor returned zero records")

    if isinstance(records, str):
        _classify_status(status, records, endpoint=endpoint)
        raise NoDataError(f"{endpoint}: {records or status or 'no records'}")

    if isinstance(records, list) and records and isinstance(records[0], dict):
        return _rename(pd.DataFrame(records))

    if isinstance(records, list):
        rows = [row if isinstance(row, (list, tuple)) else [row] for row in records]
        width = max(len(row) for row in rows)
        if "optionchain" in endpoint.lower() and width >= len(CHAIN_WIDE_LAYOUT):
            names = _positional_names(CHAIN_WIDE_LAYOUT, width)
        else:
            names = _positional_names(layout, width)
        return pd.DataFrame(rows, columns=names)

    raise UpstreamError(f"{endpoint}: unsupported records type {type(records).__name__}")


def _frame_from_csv(text: str, *, endpoint: str) -> pd.DataFrame:
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise NoDataError(f"{endpoint}: empty CSV body")

    header = rows[0]
    looks_like_header = any(not _is_number(cell) for cell in header) and len(rows) > 1
    if looks_like_header:
        columns = [c.strip() for c in header]
        width = len(columns)
        normalized = [row + [""] * (width - len(row)) if len(row) < width else row[:width] for row in rows[1:]]
        frame = pd.DataFrame(normalized, columns=columns)
        return _rename(frame)

    _classify_status(" ".join(header), "", endpoint=endpoint)
    return pd.DataFrame(rows)


def _positional_names(layout: Sequence[str], width: int) -> list[str]:
    names = list(layout[:width])
    names.extend(f"field_{i}" for i in range(len(names), width))
    return names


def _rename(frame: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for column in frame.columns:
        key = str(column).strip().lower().replace(" ", "").replace("-", "")
        mapping[column] = FIELD_ALIASES.get(key, str(column).strip().lower())
    return frame.rename(columns=mapping)


def _classify_status(status: str, message: str, *, endpoint: str) -> None:
    blob = f"{status} {message}".strip().lower()
    if not blob:
        return
    if any(token in blob for token in _NOT_ENTITLED_TOKENS):
        raise NotEntitledError(f"{endpoint}: {status or message}")
    if any(token in blob for token in _NO_DATA_TOKENS):
        raise NoDataError(f"{endpoint}: {status or message}")
    if "success" in blob or "ok" in blob:
        return
    if "error" in blob or "fail" in blob or "invalid" in blob:
        raise MarketDataError(f"{endpoint}: {status or message}")


def master_entries(frame: pd.DataFrame, symbology: TrueDataSymbology) -> list[MasterEntry]:
    """Turn a symbol-master frame into indexable entries.

    Prefer explicit CSV columns from ``getAllSymbols`` (series, strike, expiry,
    underlying, token) when present — Trial220 FO symbols use forms like
    ``NIFTY26122930000CE`` that do not match the older month-name templates.
    Fall back to symbol-string parsing only when columns are missing.
    """
    if frame.empty:
        return []

    symbol_column = _pick_symbol_column(frame)
    entries: list[MasterEntry] = []
    unparsed = 0
    for row in frame.to_dict(orient="records"):
        vendor_symbol = str(row.get(symbol_column, "") or "").strip()
        if not vendor_symbol:
            unparsed += 1
            continue
        entry = _master_entry_from_row(row, vendor_symbol, symbology)
        if entry is None:
            unparsed += 1
            continue
        entries.append(entry)

    if entries and unparsed > len(frame) * 0.4:
        raise DataQualityError(
            f"symbol master: {unparsed}/{len(frame)} rows unparseable; "
            "verify the master layout with scripts/td_probe.py before backfilling"
        )
    return entries


def _master_entry_from_row(
    row: dict[str, object], vendor_symbol: str, symbology: TrueDataSymbology
) -> MasterEntry | None:
    series = str(row.get("right_or_series") or row.get("series") or "").upper().strip()
    underlying = str(row.get("underlying") or "").upper().strip()
    token = str(row.get("vendor_token") or row.get("symbolid") or "").strip()
    lot_size = _as_int(row.get("lot_size") or row.get("lotsize"))
    tick_size = _as_float(row.get("tick_size") or row.get("ticksize"))
    expiry = _parse_expiry_cell(row.get("expiry"))
    strike = _as_float(row.get("strike"))

    if series in {"CE", "PE"} and expiry is not None and strike is not None:
        root = underlying or symbology.from_vendor(vendor_symbol).symbol
        return MasterEntry(
            vendor_symbol=vendor_symbol.upper(),
            segment=Segment.OPTIONS,
            root=root.upper(),
            expiry=expiry,
            strike=strike,
            right=OptionRight(series),
            lot_size=lot_size,
            tick_size=tick_size,
            vendor_token=token,
        )

    if series in {"XX", "FUTIDX", "FUTSTK", "FUT", "FUTCOM", "FUTCUR"} or vendor_symbol.upper().endswith("FUT"):
        root = (underlying or vendor_symbol.replace("FUT", "").split("-")[0]).upper()
        csv_expiry = expiry
        try:
            contract = symbology.from_vendor(vendor_symbol)
            root = contract.symbol.upper()
            # Prefer explicit master expiry (29-09-2026) over templated day-01
            # from symbols like NIFTY26SEPFUT.
            expiry = csv_expiry or contract.expiry
        except Exception:
            expiry = csv_expiry
        if vendor_symbol.upper().endswith("-I") or vendor_symbol.upper().endswith("-II"):
            return MasterEntry(
                vendor_symbol=vendor_symbol.upper(),
                segment=Segment.FUTURES,
                root=root,
                continuation=ContinuationStyle.VENDOR_SPLICED,
                lot_size=lot_size,
                tick_size=tick_size,
                vendor_token=token,
            )
        return MasterEntry(
            vendor_symbol=vendor_symbol.upper(),
            segment=Segment.FUTURES,
            root=root,
            expiry=expiry,
            lot_size=lot_size,
            tick_size=tick_size,
            vendor_token=token,
        )

    if series in {"EQ", "BE", "BZ", "INDEX", "IND"} or not series:
        try:
            contract = symbology.from_vendor(vendor_symbol, segment_hint=Segment.EQUITY)
        except Exception:
            return None
        return MasterEntry(
            vendor_symbol=vendor_symbol.upper(),
            segment=contract.segment,
            root=contract.symbol.upper(),
            lot_size=lot_size,
            tick_size=tick_size,
            vendor_token=token,
        )

    try:
        contract = symbology.from_vendor(vendor_symbol)
    except Exception:
        return None
    return MasterEntry(
        vendor_symbol=vendor_symbol.upper(),
        segment=contract.segment,
        root=contract.symbol.upper(),
        expiry=contract.expiry,
        strike=contract.strike,
        right=contract.right,
        lot_size=lot_size,
        tick_size=tick_size,
        vendor_token=token,
        continuation=contract.continuation,
    )


def _parse_expiry_cell(value: object) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = pd.to_datetime(str(value), errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _pick_symbol_column(frame: pd.DataFrame) -> str:
    if "vendor_symbol" in frame.columns:
        return "vendor_symbol"
    for column in frame.columns:
        values = frame[column].astype(str).head(20)
        if values.str.match(r"^[A-Z0-9&\.\- ]{2,40}$").mean() > 0.8:
            return str(column)
    raise DataQualityError("symbol master: could not identify the symbol column")


def _as_int(value: object) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _is_number(value: str) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _td_time(stamp: datetime) -> str:
    return pd.Timestamp(stamp).strftime(TD_TIME_FORMAT)


def _strike_param(strike: float) -> str:
    return str(int(strike)) if float(strike).is_integer() else f"{strike:g}"
