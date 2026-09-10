"""Build Instrument objects from the instrument master.

Live and paper modes must resolve lot sizes from the broker master. The static
YAML is a research convenience and says so loudly; asking for a symbol that is
not there raises instead of defaulting.
"""

from __future__ import annotations

from alpha_desk.config.settings import InstrumentMaster
from alpha_desk.core.types import Instrument, Product


def build_instrument(master: InstrumentMaster, symbol: str) -> Instrument:
    spec = master.spec(symbol)
    return Instrument(
        symbol=symbol,
        product=Product(spec.kind.lower()),
        lot_size=spec.lot_size,
        tick_size=spec.tick_size,
        exchange=spec.exchange,
        margin_pct=spec.margin_pct,
        slippage_profile=spec.slippage_profile,
        correlation_cluster=spec.correlation_cluster,
    )


def build_all(master: InstrumentMaster) -> dict[str, Instrument]:
    return {symbol: build_instrument(master, symbol) for symbol in master.instruments}
