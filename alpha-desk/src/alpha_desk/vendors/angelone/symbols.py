"""Angel OpenAPI scrip-master → canonical Contract map."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from alpha_desk.marketdata.contracts import (
    Contract,
    Exchange,
    OptionRight,
    Segment,
)
from alpha_desk.marketdata.errors import SymbolResolutionError

# exch_seg → (Exchange, default Segment hint)
EXCH_SEG: dict[str, tuple[Exchange, str]] = {
    "NSE": (Exchange.NSE, "EQ"),
    "BSE": (Exchange.BSE, "EQ"),
    "NFO": (Exchange.NSE, "FO"),
    "BFO": (Exchange.BSE, "FO"),
    "MCX": (Exchange.MCX, "COM"),
    "CDS": (Exchange.NSE, "CDS"),
}

# SmartAPI WS exchangeType codes
EXCHANGE_TYPE: dict[str, int] = {
    "NSE": 1,
    "NFO": 2,
    "BSE": 3,
    "BFO": 4,
    "MCX": 5,
    "CDS": 13,
}

MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

# NIFTY29SEP26FUT / BANKNIFTY30SEP26CE / RELIANCE
FUT_RE = re.compile(
    r"^(?P<root>[A-Z0-9&-]+?)(?P<day>\d{2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})FUT$",
    re.I,
)
OPT_RE = re.compile(
    r"^(?P<root>[A-Z0-9&-]+?)(?P<day>\d{2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})"
    r"(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$",
    re.I,
)


@dataclass
class MasterEntry:
    token: str
    symbol: str
    name: str
    exch_seg: str
    instrumenttype: str
    expiry: date | None
    strike: float | None
    right: OptionRight | None
    lot_size: int | None
    tick_size: float | None
    contract: Contract


@dataclass
class AngelSymbology:
    by_token: dict[str, MasterEntry] = field(default_factory=dict)
    by_vendor_symbol: dict[str, MasterEntry] = field(default_factory=dict)
    by_contract_id: dict[str, MasterEntry] = field(default_factory=dict)

    def clear(self) -> None:
        self.by_token.clear()
        self.by_vendor_symbol.clear()
        self.by_contract_id.clear()

    def load_rows(self, rows: Iterable[dict]) -> int:
        count = 0
        for row in rows:
            try:
                entry = parse_scrip_row(row)
            except (ValueError, TypeError, KeyError):
                # One bad vendor row must not kill the whole master load.
                continue
            if entry is None:
                continue
            self.by_token[entry.token] = entry
            self.by_vendor_symbol[entry.symbol.upper()] = entry
            self.by_contract_id[entry.contract.contract_id] = entry
            count += 1
        return count

    def resolve(self, contract: Contract) -> Contract:
        if contract.vendor_token and contract.vendor_token in self.by_token:
            return self.by_token[contract.vendor_token].contract
        if contract.vendor_symbol:
            hit = self.by_vendor_symbol.get(contract.vendor_symbol.upper())
            if hit:
                return hit.contract
        hit = self.by_contract_id.get(contract.contract_id)
        if hit:
            return hit.contract
        # Best-effort match dated FUT/OPT by fields
        for entry in self.by_token.values():
            c = entry.contract
            if (
                c.symbol == contract.symbol
                and c.segment == contract.segment
                and c.expiry == contract.expiry
                and c.strike == contract.strike
                and c.right == contract.right
            ):
                return c
        raise SymbolResolutionError(
            f"Angel scrip master has no match for {contract.contract_id}"
        )

    def expiries(self, underlying: str, segment: Segment) -> list[date]:
        root = underlying.upper()
        found = {
            e.contract.expiry
            for e in self.by_token.values()
            if e.contract.symbol == root
            and e.contract.segment is segment
            and e.contract.expiry is not None
        }
        return sorted(d for d in found if d is not None)

    def futures_for(self, underlying: str) -> list[Contract]:
        root = underlying.upper()
        return sorted(
            (
                e.contract
                for e in self.by_token.values()
                if e.contract.symbol == root and e.contract.segment is Segment.FUTURES
            ),
            key=lambda c: c.expiry or date.min,
        )

    def strikes(self, underlying: str, expiry: date) -> list[float]:
        root = underlying.upper()
        found = {
            e.contract.strike
            for e in self.by_token.values()
            if e.contract.symbol == root
            and e.contract.segment is Segment.OPTIONS
            and e.contract.expiry == expiry
            and e.contract.strike is not None
        }
        return sorted(float(s) for s in found)

    def enrich(self, contract: Contract) -> Contract:
        """Attach vendor token/symbol from the master when possible."""
        try:
            return self.resolve(contract)
        except SymbolResolutionError:
            if contract.segment is Segment.EQUITY:
                hits = self.equities_named(contract.symbol)
                if hits:
                    return hits[0]
            if contract.segment is Segment.INDEX:
                spot = self.index_spot(contract.symbol)
                if spot:
                    return spot
            raise

    def equities_named(self, name: str) -> list[Contract]:
        key = name.upper()
        out: list[Contract] = []
        for e in self.by_token.values():
            if e.contract.segment is Segment.EQUITY and (
                e.contract.symbol == key or e.name.upper() == key or e.symbol.upper() == key
            ):
                out.append(e.contract)
        return out

    def index_spot(self, underlying: str) -> Contract | None:
        # Common Angel index tokens use symbol Nifty 50 / Nifty Bank; name field varies.
        aliases = {
            "NIFTY": ("NIFTY", "Nifty 50", "NIFTY 50"),
            "BANKNIFTY": ("BANKNIFTY", "Nifty Bank", "NIFTY BANK"),
        }
        wanted = {a.upper() for a in aliases.get(underlying.upper(), (underlying,))}
        for e in self.by_token.values():
            if e.exch_seg != "NSE":
                continue
            if e.instrumenttype.upper() in {"AMXIDX", "INDEX"} or "NIFTY" in e.name.upper():
                if e.symbol.upper() in wanted or e.name.upper() in wanted:
                    return e.contract
        return None


def parse_scrip_row(row: dict) -> MasterEntry | None:
    token = str(row.get("token") or "").strip()
    symbol = str(row.get("symbol") or "").strip()
    if not token or not symbol:
        return None
    exch_seg = str(row.get("exch_seg") or "").strip().upper()
    if exch_seg not in EXCH_SEG:
        return None
    exchange, _ = EXCH_SEG[exch_seg]
    instrument = str(row.get("instrumenttype") or "").strip().upper()
    name = str(row.get("name") or symbol).strip()
    lot = _int_or_none(row.get("lotsize"))
    tick = _float_or_none(row.get("tick_size"))
    expiry = _parse_expiry(row.get("expiry"))
    strike_raw = _float_or_none(row.get("strike"))
    # Angel stores strike * 100 for options in some dumps
    strike = None
    right = None
    segment: Segment

    if instrument in {"FUTIDX", "FUTSTK", "FUTCOM", "FUTCUR"}:
        segment = (
            Segment.COMMODITY
            if exch_seg == "MCX"
            else Segment.CURRENCY
            if exch_seg == "CDS"
            else Segment.FUTURES
        )
        root, exp = _parse_fut_symbol(symbol)
        if root:
            name = root
        if exp:
            expiry = exp
    elif instrument in {"OPTIDX", "OPTSTK", "OPTCOM", "OPTCUR"}:
        # Always OPTIONS (even CDS/MCX) — Contract forbids strike/right elsewhere.
        segment = Segment.OPTIONS
        root, exp, strike, right = _parse_opt_symbol(symbol)
        if root:
            name = root
        if exp:
            expiry = exp
        if strike is None and strike_raw is not None:
            strike = strike_raw / 100.0 if strike_raw > 10000 else strike_raw
    elif exch_seg in {"NSE", "BSE"} and instrument in {"", "EQ"}:
        # Indices often have empty instrumenttype + name containing Nifty
        if "NIFTY" in name.upper() and not symbol.replace("-", "").isalnum():
            segment = Segment.INDEX
        elif instrument == "" and name.upper() in {"NIFTY", "BANKNIFTY", "FINNIFTY"}:
            segment = Segment.INDEX
        else:
            segment = Segment.EQUITY
            name = symbol.split("-")[0] if "-" in symbol else symbol
    elif instrument in {"AMXIDX", "INDEX"}:
        segment = Segment.INDEX
    else:
        return None

    if segment in (Segment.FUTURES, Segment.OPTIONS) and expiry is None:
        return None
    if segment is Segment.OPTIONS and (strike is None or right is None):
        return None

    contract = Contract(
        symbol=name.upper().replace(" ", ""),
        segment=segment,
        exchange=exchange,
        expiry=expiry,
        strike=strike,
        right=right,
        lot_size=lot,
        tick_size=tick,
        vendor_symbol=symbol,
        vendor_token=token,
    )
    return MasterEntry(
        token=token,
        symbol=symbol,
        name=name,
        exch_seg=exch_seg,
        instrumenttype=instrument,
        expiry=expiry,
        strike=strike,
        right=right,
        lot_size=lot,
        tick_size=tick,
        contract=contract,
    )


def exchange_type_for_seg(exch_seg: str) -> int:
    return EXCHANGE_TYPE.get(exch_seg.upper(), 1)


def _parse_fut_symbol(symbol: str) -> tuple[str | None, date | None]:
    m = FUT_RE.match(symbol.upper())
    if not m:
        return None, None
    return m.group("root"), _dmy(m.group("day"), m.group("mon"), m.group("yy"))


def _parse_opt_symbol(
    symbol: str,
) -> tuple[str | None, date | None, float | None, OptionRight | None]:
    m = OPT_RE.match(symbol.upper())
    if not m:
        return None, None, None, None
    return (
        m.group("root"),
        _dmy(m.group("day"), m.group("mon"), m.group("yy")),
        float(m.group("strike")),
        OptionRight.CALL if m.group("right") == "CE" else OptionRight.PUT,
    )


def _dmy(day: str, mon: str, yy: str) -> date | None:
    month = MONTHS.get(mon.upper())
    if month is None:
        return None
    year = 2000 + int(yy)
    try:
        return date(year, month, int(day))
    except ValueError:
        return None


def _parse_expiry(value: object) -> date | None:
    if value is None or value == "" or value == " ":
        return None
    text = str(value).strip()
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d%b%y"):
        try:
            return datetime.strptime(text.upper(), fmt).date()
        except ValueError:
            continue
    return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
