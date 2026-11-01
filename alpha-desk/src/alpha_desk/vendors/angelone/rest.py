"""Angel One REST: scrip master, candles, market quotes."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import requests

from alpha_desk.marketdata.contracts import IST, BarInterval, Contract, Segment
from alpha_desk.marketdata.errors import NoDataError, TransportError, UpstreamError
from alpha_desk.vendors.angelone.config import AngelOneConfig
from alpha_desk.vendors.angelone.session import AngelSession
from alpha_desk.vendors.angelone.symbols import AngelSymbology

CANDLE_PATH = "/rest/secure/angelbroking/historical/v1/getCandleData"
QUOTE_PATH = "/rest/secure/angelbroking/market/v1/quote"

INTERVAL_TO_ANGEL: dict[BarInterval, str] = {
    BarInterval.M1: "ONE_MINUTE",
    BarInterval.M3: "THREE_MINUTE",
    BarInterval.M5: "FIVE_MINUTE",
    BarInterval.M10: "TEN_MINUTE",
    BarInterval.M15: "FIFTEEN_MINUTE",
    BarInterval.M30: "THIRTY_MINUTE",
    BarInterval.M60: "ONE_HOUR",
    BarInterval.EOD: "ONE_DAY",
}

# exch_seg for candle API
SEG_TO_EXCHANGE: dict[str, str] = {
    "NSE": "NSE",
    "BSE": "BSE",
    "NFO": "NFO",
    "BFO": "BFO",
    "MCX": "MCX",
    "CDS": "CDS",
}


class AngelRestClient:
    def __init__(self, config: AngelOneConfig, session: AngelSession | None = None):
        self._config = config
        self._session = session or AngelSession(config)

    @property
    def session(self) -> AngelSession:
        return self._session

    def load_scrip_master(self, symbology: AngelSymbology) -> int:
        self._config.require_network_allowed()
        try:
            resp = requests.get(
                self._config.scrip_master_url, timeout=self._config.request_timeout_s
            )
            resp.raise_for_status()
            rows = resp.json()
        except requests.RequestException as exc:
            raise TransportError(f"scrip master download failed: {exc}") from exc
        except ValueError as exc:
            raise UpstreamError("scrip master was not JSON") from exc
        if not isinstance(rows, list):
            raise UpstreamError("scrip master payload is not a list")
        symbology.clear()
        return symbology.load_rows(rows)

    def get_bars(
        self,
        contract: Contract,
        interval: BarInterval,
        start: datetime,
        end: datetime,
        *,
        exch_seg: str,
    ) -> pd.DataFrame:
        if interval not in INTERVAL_TO_ANGEL:
            raise UpstreamError(f"Angel does not support interval {interval.value}")
        if not contract.vendor_token:
            raise UpstreamError(f"{contract.contract_id}: missing vendor_token")

        payload = {
            "exchange": SEG_TO_EXCHANGE.get(exch_seg, exch_seg),
            "symboltoken": str(contract.vendor_token),
            "interval": INTERVAL_TO_ANGEL[interval],
            "fromdate": _fmt(start),
            "todate": _fmt(end),
        }
        body = self._session.post_json(CANDLE_PATH, payload)
        data = body.get("data")
        if not data:
            raise NoDataError(f"no candles for {contract.vendor_symbol} {interval.value}")
        frame = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        frame["oi"] = 0.0
        return frame

    def get_quotes(self, tokens_by_exchange: dict[str, list[str]]) -> list[dict]:
        """Batch LTP/quote. ``tokens_by_exchange`` keys are NSE/NFO/…"""
        if not tokens_by_exchange:
            return []
        payload = {"mode": "FULL", "exchangeTokens": tokens_by_exchange}
        body = self._session.post_json(QUOTE_PATH, payload)
        data = body.get("data") or {}
        fetched = data.get("fetched") or data.get("unfetched") or []
        if isinstance(data, list):
            return data
        if isinstance(fetched, list):
            return fetched
        return []


def exch_seg_for_contract(contract: Contract, symbology: AngelSymbology) -> str:
    if contract.vendor_token and contract.vendor_token in symbology.by_token:
        return symbology.by_token[contract.vendor_token].exch_seg
    if contract.segment in (Segment.FUTURES, Segment.OPTIONS):
        return "NFO" if contract.exchange.value == "NSE" else "BFO"
    if contract.segment is Segment.COMMODITY:
        return "MCX"
    if contract.segment is Segment.CURRENCY:
        return "CDS"
    return "NSE" if contract.exchange.value == "NSE" else "BSE"


def _fmt(ts: datetime) -> str:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    else:
        stamp = stamp.tz_convert(IST)
    return stamp.strftime("%Y-%m-%d %H:%M")
