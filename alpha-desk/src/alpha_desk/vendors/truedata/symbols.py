"""TrueData symbology.

Constructing vendor symbols by string templating is the single most common way
to silently backfill the wrong contract, so the symbol master is the source of
truth here: resolution looks up ``(root, expiry, strike, right)`` in the
downloaded master first and only falls back to templating when the master has
not been loaded. Both the compact form (``NIFTY25AUG2824000CE``) and the long
form (``OPTIDX_NIFTY_28AUG2025_CE_24000``) are parsed, and templates live in a
dataclass so a vendor format change is a config edit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from alpha_desk.marketdata.contracts import (
    ContinuationStyle,
    Contract,
    Exchange,
    OptionRight,
    Segment,
)
from alpha_desk.marketdata.errors import SymbolResolutionError

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_INDEX = {name: i + 1 for i, name in enumerate(MONTHS)}

_ROOT = r"[A-Z0-9&\-\.]+?"
_MON = "|".join(MONTHS)

COMPACT_OPTION = re.compile(
    rf"^(?P<root>{_ROOT})(?P<yy>\d{{2}})(?P<mon>{_MON})(?P<dd>\d{{2}})"
    rf"(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$"
)
# Trial220 FO master uses numeric expiry in symbol, e.g. NIFTY26122930000CE.
NUMERIC_OPTION = re.compile(
    rf"^(?P<root>{_ROOT})(?P<yymmdd>\d{{6}})(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$"
)
NUMERIC_FUTURE = re.compile(rf"^(?P<root>{_ROOT})(?P<yymmdd>\d{{6}})FUT$")
COMPACT_FUTURE = re.compile(
    rf"^(?P<root>{_ROOT})(?P<yy>\d{{2}})(?P<mon>{_MON})(?P<dd>\d{{2}})FUT$"
)
MONTHLY_FUTURE = re.compile(rf"^(?P<root>{_ROOT})(?P<yy>\d{{2}})(?P<mon>{_MON})FUT$")
CONTINUOUS = re.compile(r"^(?P<root>.+?)-(?P<rank>I{1,3})$")
LONG_FORM = re.compile(
    rf"^(?P<kind>FUTIDX|FUTSTK|FUTCUR|FUTCOM|OPTIDX|OPTSTK|OPTCUR|OPTCOM)_"
    rf"(?P<root>[A-Z0-9&\-\.]+)_(?P<dd>\d{{2}})(?P<mon>{_MON})(?P<yyyy>\d{{4}})_"
    rf"(?P<right>CE|PE|XX)_(?P<strike>\d+(?:\.\d+)?)$"
)

# TrueData publishes index cash under spaced names; everything else that looks
# like a plain ticker is treated as equity unless the master says otherwise.
KNOWN_INDEX_NAMES = {
    "NIFTY 50",
    "NIFTY BANK",
    "NIFTY FIN SERVICE",
    "NIFTY MID SELECT",
    "NIFTY NEXT 50",
    "NIFTY MIDCAP 100",
    "NIFTY SMLCAP 100",
    "NIFTY IT",
    "NIFTY AUTO",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY PHARMA",
    "NIFTY PSU BANK",
    "NIFTY REALTY",
    "NIFTY ENERGY",
    "NIFTY INFRA",
    "INDIA VIX",
}

# Canonical desk names for the derivative roots we actually trade, mapped to the
# index cash symbol TrueData serves.
UNDERLYING_TO_INDEX = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MID SELECT",
    "NIFTYNXT50": "NIFTY NEXT 50",
}

CONTINUATION_RANK = {
    ContinuationStyle.VENDOR_SPLICED: "I",
    ContinuationStyle.VENDOR_ADJUSTED: "I",
}


@dataclass(frozen=True)
class SymbolFormat:
    """Vendor string templates. Overridable without touching the adapter."""

    future: str = "{root}{yy}{mon}{dd}FUT"
    option: str = "{root}{yy}{mon}{dd}{strike}{right}"
    continuous: str = "{root}-{rank}"

    def render_future(self, root: str, expiry: date) -> str:
        return self.future.format(root=root, **_date_parts(expiry))

    def render_option(self, root: str, expiry: date, strike: float, right: OptionRight) -> str:
        return self.option.format(
            root=root, strike=_strike_text(strike), right=right.value, **_date_parts(expiry)
        )

    def render_continuous(self, root: str, rank: str = "I") -> str:
        return self.continuous.format(root=root, rank=rank)


@dataclass
class MasterEntry:
    vendor_symbol: str
    segment: Segment
    root: str
    expiry: date | None = None
    strike: float | None = None
    right: OptionRight | None = None
    lot_size: int | None = None
    tick_size: float | None = None
    vendor_token: str = ""
    continuation: ContinuationStyle = ContinuationStyle.NONE


@dataclass
class TrueDataSymbology:
    """Bidirectional mapping between canonical contracts and vendor strings."""

    fmt: SymbolFormat = field(default_factory=SymbolFormat)
    _by_key: dict[tuple, MasterEntry] = field(default_factory=dict, repr=False)
    _by_vendor: dict[str, MasterEntry] = field(default_factory=dict, repr=False)

    # -- master ------------------------------------------------------------ #

    def load_master(self, entries: list[MasterEntry]) -> int:
        for entry in entries:
            self._by_vendor[entry.vendor_symbol.upper()] = entry
            self._by_key[
                self._key(
                    entry.segment,
                    entry.root,
                    entry.expiry,
                    entry.strike,
                    entry.right,
                    entry.continuation,
                )
            ] = entry
        return len(entries)

    @property
    def master_size(self) -> int:
        return len(self._by_vendor)

    def expiries(self, root: str, segment: Segment) -> list[date]:
        found = {
            entry.expiry
            for entry in self._by_vendor.values()
            if entry.segment is segment and entry.root == root.upper() and entry.expiry is not None
        }
        return sorted(found)

    def strikes(self, root: str, expiry: date) -> list[float]:
        found = {
            entry.strike
            for entry in self._by_vendor.values()
            if entry.segment is Segment.OPTIONS
            and entry.root == root.upper()
            and entry.expiry == expiry
            and entry.strike is not None
        }
        return sorted(found)

    # -- canonical -> vendor ----------------------------------------------- #

    def to_vendor(self, contract: Contract) -> str:
        if contract.vendor_symbol:
            return contract.vendor_symbol

        root = contract.symbol.upper()
        entry = self._by_key.get(
            self._key(
                contract.segment,
                root,
                contract.expiry,
                contract.strike,
                contract.right,
                contract.continuation,
            )
        )
        if entry is not None:
            return entry.vendor_symbol

        if contract.segment is Segment.INDEX:
            return UNDERLYING_TO_INDEX.get(root, contract.symbol.upper())
        if contract.segment is Segment.EQUITY:
            return root
        if contract.segment is Segment.FUTURES:
            if contract.is_continuous:
                return self.fmt.render_continuous(root, CONTINUATION_RANK.get(contract.continuation, "I"))
            if contract.expiry is None:
                raise SymbolResolutionError(f"{root}: futures contract without expiry")
            return self.fmt.render_future(root, contract.expiry)
        if contract.segment is Segment.OPTIONS:
            if contract.expiry is None or contract.strike is None or contract.right is None:
                raise SymbolResolutionError(f"{root}: option needs expiry, strike and right")
            return self.fmt.render_option(root, contract.expiry, contract.strike, contract.right)
        raise SymbolResolutionError(f"cannot express segment {contract.segment} in TrueData symbology")

    def enrich(self, contract: Contract) -> Contract:
        """Attach the vendor symbol plus lot/tick metadata from the master."""
        vendor_symbol = self.to_vendor(contract)
        entry = self._by_vendor.get(vendor_symbol.upper())
        return Contract(
            symbol=contract.symbol,
            segment=contract.segment,
            exchange=contract.exchange,
            expiry=contract.expiry,
            strike=contract.strike,
            right=contract.right,
            lot_size=contract.lot_size or (entry.lot_size if entry else None),
            tick_size=contract.tick_size or (entry.tick_size if entry else None),
            continuation=contract.continuation,
            vendor_symbol=vendor_symbol,
            vendor_token=contract.vendor_token or (entry.vendor_token if entry else ""),
        )

    # -- vendor -> canonical ----------------------------------------------- #

    def from_vendor(self, vendor_symbol: str, *, segment_hint: Segment | None = None) -> Contract:
        raw = vendor_symbol.strip().upper()
        if not raw:
            raise SymbolResolutionError("empty vendor symbol")

        entry = self._by_vendor.get(raw)
        if entry is not None:
            return self._from_entry(entry)

        long_match = LONG_FORM.match(raw)
        if long_match:
            return self._from_long(long_match, raw)

        option = COMPACT_OPTION.match(raw)
        if option:
            return Contract(
                symbol=option["root"],
                segment=Segment.OPTIONS,
                expiry=_compact_date(option["yy"], option["mon"], option["dd"]),
                strike=float(option["strike"]),
                right=OptionRight(option["right"]),
                vendor_symbol=raw,
            )

        numeric_option = NUMERIC_OPTION.match(raw)
        if numeric_option:
            yymmdd = numeric_option["yymmdd"]
            return Contract(
                symbol=numeric_option["root"],
                segment=Segment.OPTIONS,
                expiry=_numeric_date(yymmdd),
                strike=float(numeric_option["strike"]),
                right=OptionRight(numeric_option["right"]),
                vendor_symbol=raw,
            )

        future = COMPACT_FUTURE.match(raw)
        if future:
            return Contract(
                symbol=future["root"],
                segment=Segment.FUTURES,
                expiry=_compact_date(future["yy"], future["mon"], future["dd"]),
                vendor_symbol=raw,
            )

        numeric_future = NUMERIC_FUTURE.match(raw)
        if numeric_future:
            return Contract(
                symbol=numeric_future["root"],
                segment=Segment.FUTURES,
                expiry=_numeric_date(numeric_future["yymmdd"]),
                vendor_symbol=raw,
            )

        monthly_future = MONTHLY_FUTURE.match(raw)
        if monthly_future:
            return Contract(
                symbol=monthly_future["root"],
                segment=Segment.FUTURES,
                expiry=_compact_date(monthly_future["yy"], monthly_future["mon"], "01"),
                vendor_symbol=raw,
            )

        continuous = CONTINUOUS.match(raw)
        if continuous and continuous["root"] not in KNOWN_INDEX_NAMES:
            return Contract(
                symbol=continuous["root"],
                segment=Segment.FUTURES,
                continuation=ContinuationStyle.VENDOR_SPLICED,
                vendor_symbol=raw,
            )

        if raw in KNOWN_INDEX_NAMES or segment_hint is Segment.INDEX:
            canonical = _index_to_canonical(raw)
            return Contract(symbol=canonical, segment=Segment.INDEX, vendor_symbol=raw)

        return Contract(symbol=raw, segment=segment_hint or Segment.EQUITY, vendor_symbol=raw)

    def _from_entry(self, entry: MasterEntry) -> Contract:
        return Contract(
            symbol=entry.root,
            segment=entry.segment,
            expiry=entry.expiry,
            strike=entry.strike,
            right=entry.right,
            lot_size=entry.lot_size,
            tick_size=entry.tick_size,
            continuation=entry.continuation,
            vendor_symbol=entry.vendor_symbol,
            vendor_token=entry.vendor_token,
        )

    def _from_long(self, match: re.Match[str], raw: str) -> Contract:
        expiry = date(int(match["yyyy"]), MONTH_INDEX[match["mon"]], int(match["dd"]))
        if match["kind"].startswith("OPT"):
            return Contract(
                symbol=match["root"],
                segment=Segment.OPTIONS,
                expiry=expiry,
                strike=float(match["strike"]),
                right=OptionRight(match["right"]),
                vendor_symbol=raw,
            )
        return Contract(
            symbol=match["root"], segment=Segment.FUTURES, expiry=expiry, vendor_symbol=raw
        )

    @staticmethod
    def _key(
        segment: Segment,
        root: str,
        expiry: date | None,
        strike: float | None,
        right: OptionRight | None,
        continuation: ContinuationStyle = ContinuationStyle.NONE,
    ) -> tuple:
        return (
            segment,
            root.upper(),
            expiry,
            None if strike is None else round(float(strike), 4),
            right,
            continuation,
        )


def index_symbol(underlying: str) -> str:
    """TrueData cash-index symbol for a derivative root (``NIFTY`` -> ``NIFTY 50``)."""
    return UNDERLYING_TO_INDEX.get(underlying.upper(), underlying.upper())


def _index_to_canonical(vendor: str) -> str:
    for canonical, vendor_name in UNDERLYING_TO_INDEX.items():
        if vendor_name == vendor:
            return canonical
    return vendor.replace(" ", "")


def _date_parts(expiry: date) -> dict[str, str]:
    return {
        "yy": f"{expiry.year % 100:02d}",
        "yyyy": f"{expiry.year:04d}",
        "mon": MONTHS[expiry.month - 1],
        "dd": f"{expiry.day:02d}",
    }


def _strike_text(strike: float) -> str:
    if float(strike).is_integer():
        return str(int(strike))
    return f"{strike:g}"


def _compact_date(yy: str, mon: str, dd: str) -> date:
    return date(2000 + int(yy), MONTH_INDEX[mon], int(dd))


def _numeric_date(yymmdd: str) -> date:
    """Parse YYMMDD embedded in symbols like ``NIFTY26122930000CE``."""
    return date(2000 + int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
