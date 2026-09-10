"""India (NSE) transaction cost model.

Costs are computed per executed leg and summed into a round turn. Nothing in
the desk is allowed to evaluate an edge without running through here: a
backtest without costs is a marketing document, not evidence.

Fee components, all from `configs/costs_india.yaml`:
    brokerage, STT, exchange transaction charges, SEBI turnover fee, NSE IPFT,
    stamp duty (buy leg only), GST on the statutory-fee bundle, DP charges
    (equity delivery sell), and a pessimistic slippage allowance.
"""

from __future__ import annotations

from alpha_desk.config.settings import CostConfig
from alpha_desk.core.types import CostEstimate, Instrument, Product, Side

# Products whose charges are levied on option premium rather than contract value.
_PREMIUM_KEYS = {
    Product.OPTIONS: {
        "exchange_txn": "options_on_premium",
        "ipft": "options_on_premium",
        "stt_sell": "sell_pct_on_premium",
    }
}


class UnverifiedCostsError(RuntimeError):
    """Raised when verified costs are required but the schedule is unconfirmed."""


class CostModel:
    def __init__(self, config: CostConfig) -> None:
        self._cfg = config

    @property
    def config(self) -> CostConfig:
        return self._cfg

    @property
    def verified(self) -> bool:
        return self._cfg.verified_against_contract_note

    def require_verified(self) -> None:
        """Call before any promote decision that will eventually risk capital."""
        if not self.verified:
            raise UnverifiedCostsError(
                "cost schedule is not reconciled against a real contract note "
                f"(rates_as_of={self._cfg.rates_as_of}). Promotion is blocked."
            )

    # ------------------------------------------------------------------ #
    # single leg
    # ------------------------------------------------------------------ #

    def leg_cost(
        self,
        *,
        product: Product,
        side: Side,
        price: float,
        quantity: int,
        slippage_profile: str = "index_futures",
        tick_size: float = 0.05,
    ) -> CostEstimate:
        if price <= 0 or quantity <= 0:
            raise ValueError("price and quantity must be positive")

        turnover = price * quantity
        key = product.value
        premium_keys = _PREMIUM_KEYS.get(product, {})

        brokerage = self._brokerage(key, turnover)
        stt = self._stt(key, side, turnover, premium_keys)
        exchange_txn = turnover * self._pct(
            self._cfg.exchange_txn_pct, premium_keys.get("exchange_txn", key)
        )
        ipft = turnover * self._pct(self._cfg.ipft_pct, premium_keys.get("ipft", key))
        sebi = turnover * self._cfg.sebi_turnover_pct / 100.0
        stamp = (
            turnover * self._pct(self._cfg.stamp_duty_buy_pct, key) if side is Side.BUY else 0.0
        )
        gst = (brokerage + exchange_txn + sebi + ipft) * self._cfg.gst_pct / 100.0
        dp = (
            self._cfg.dp_charges.get("equity_delivery_sell_inr", 0.0)
            if product is Product.EQUITY_DELIVERY and side is Side.SELL
            else 0.0
        )
        slippage = self._slippage(slippage_profile, price, quantity, tick_size)

        return CostEstimate(
            brokerage=brokerage,
            stt=stt,
            exchange_txn=exchange_txn,
            sebi=sebi,
            ipft=ipft,
            stamp_duty=stamp,
            gst=gst,
            dp_charges=dp,
            slippage=slippage,
        )

    # ------------------------------------------------------------------ #
    # round turn
    # ------------------------------------------------------------------ #

    def round_turn(
        self,
        *,
        instrument: Instrument,
        entry_side: Side,
        entry_price: float,
        exit_price: float,
        quantity: int,
    ) -> CostEstimate:
        entry = self.leg_cost(
            product=instrument.product,
            side=entry_side,
            price=entry_price,
            quantity=quantity,
            slippage_profile=instrument.slippage_profile,
            tick_size=instrument.tick_size,
        )
        exit_leg = self.leg_cost(
            product=instrument.product,
            side=entry_side.opposite,
            price=exit_price,
            quantity=quantity,
            slippage_profile=instrument.slippage_profile,
            tick_size=instrument.tick_size,
        )
        return entry + exit_leg

    def round_turn_inr(
        self,
        *,
        instrument: Instrument,
        entry_side: Side,
        entry_price: float,
        exit_price: float | None = None,
        quantity: int,
    ) -> float:
        """Convenience wrapper used by the sizer; assumes a flat exit if unknown."""
        return self.round_turn(
            instrument=instrument,
            entry_side=entry_side,
            entry_price=entry_price,
            exit_price=exit_price if exit_price is not None else entry_price,
            quantity=quantity,
        ).total

    def cost_per_unit(self, *, instrument: Instrument, price: float, quantity: int) -> float:
        """Round-turn cost expressed in price points, useful for R:R maths."""
        total = self.round_turn_inr(
            instrument=instrument,
            entry_side=Side.BUY,
            entry_price=price,
            quantity=quantity,
        )
        return total / quantity

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _brokerage(self, product_key: str, turnover: float) -> float:
        leg = self._cfg.brokerage[product_key]
        if leg.mode == "flat":
            charge = leg.flat_inr
        elif leg.mode == "pct":
            charge = turnover * leg.pct / 100.0
        else:
            charge = min(leg.flat_inr, turnover * leg.pct / 100.0)
        return max(charge, leg.min_inr)

    def _stt(
        self, product_key: str, side: Side, turnover: float, premium_keys: dict[str, str]
    ) -> float:
        schedule = self._cfg.stt.get(product_key)
        if schedule is None:
            raise KeyError(f"no STT schedule for {product_key}")
        if side is Side.BUY:
            return turnover * schedule.get("buy_pct", 0.0) / 100.0
        sell_key = premium_keys.get("stt_sell", "sell_pct")
        return turnover * schedule.get(sell_key, schedule.get("sell_pct", 0.0)) / 100.0

    def _slippage(
        self, profile_name: str, price: float, quantity: int, tick_size: float
    ) -> float:
        profile = self._cfg.slippage.profile(profile_name)
        tick_component = profile.ticks * tick_size * quantity
        bps_component = price * quantity * profile.bps / 10_000.0
        return tick_component + bps_component

    @staticmethod
    def _pct(table: dict[str, float], key: str) -> float:
        if key not in table:
            raise KeyError(f"missing rate for {key!r}")
        return table[key] / 100.0
