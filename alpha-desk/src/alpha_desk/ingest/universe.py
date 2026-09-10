"""Trial universe construction.

The seven-day trial has to cover options first, futures second and equities
third, while respecting the licensed concurrent-symbol count. Encoding that as a
priority-ordered universe (rather than a hand-maintained symbol list) means the
watchlist is derived from the live symbol master every morning, so an expiry
roll or a new strike listing needs no edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from alpha_desk.marketdata.contracts import Contract, Segment
from alpha_desk.marketdata.errors import MarketDataError

DEFAULT_UNDERLYINGS = ("NIFTY", "BANKNIFTY")

# Liquid, index-heavy names: enough to exercise the equity path and corporate
# actions without burning the symbol quota.
DEFAULT_EQUITIES = ("RELIANCE", "HDFCBANK", "TCS")


@dataclass
class TrialUniverse:
    index_spots: list[Contract] = field(default_factory=list)
    continuous_futures: list[Contract] = field(default_factory=list)
    dated_futures: list[Contract] = field(default_factory=list)
    options: list[Contract] = field(default_factory=list)
    equities: list[Contract] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    as_of: date | None = None

    def history_targets(self) -> list[Contract]:
        """Everything worth backfilling, in the directive's priority order."""
        return [
            *self.options,
            *self.continuous_futures,
            *self.dated_futures,
            *self.index_spots,
            *self.equities,
        ]

    def live_watchlist(self, limit: int) -> list[Contract]:
        """Priority-truncated subscription list: options, then futures, then cash."""
        ordered = [
            *self.options,
            *self.continuous_futures,
            *self.dated_futures,
            *self.index_spots,
            *self.equities,
        ]
        seen: set[str] = set()
        unique: list[Contract] = []
        for contract in ordered:
            key = contract.vendor_symbol or contract.contract_id
            if key in seen:
                continue
            seen.add(key)
            unique.append(contract)
        live = [
            c
            for c in unique
            if c.expiry is None or self.as_of is None or c.expiry >= self.as_of
        ]
        return live[: max(0, limit)]

    def summary(self) -> dict[str, object]:
        return {
            "index_spots": len(self.index_spots),
            "continuous_futures": len(self.continuous_futures),
            "dated_futures": len(self.dated_futures),
            "options": len(self.options),
            "equities": len(self.equities),
            "notes": self.notes,
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


def build_trial_universe(
    provider,
    *,
    underlyings: tuple[str, ...] = DEFAULT_UNDERLYINGS,
    equities: tuple[str, ...] = DEFAULT_EQUITIES,
    expiries_ahead: int = 2,
    strikes_each_side: int = 10,
    include_options: bool = True,
    today: date | None = None,
    expired_behind: int = 1,
) -> TrialUniverse:
    """Build the trial universe.

    ``expired_behind`` keeps recently expired contracts in the *history*
    list so the trial can evaluate expired-contract coverage. The live
    watchlist still drops anything with ``expiry < today``.
    """
    cutoff = today or date.today()
    universe = TrialUniverse(as_of=cutoff)
    provider.ensure_master([Segment.INDEX, Segment.EQUITY, Segment.FUTURES, Segment.OPTIONS])

    for root in underlyings:
        spot = provider.spot_contract(root)
        universe.index_spots.append(spot)

        try:
            universe.continuous_futures.append(provider.continuous_future(root))
        except MarketDataError as exc:
            universe.notes.append(f"{root}: no continuous future ({exc})")

        expiries = _pick_expiries(
            provider.fetch_expiries(root, Segment.FUTURES),
            cutoff=cutoff,
            ahead=expiries_ahead,
            behind=expired_behind,
        )
        for expiry in expiries:
            universe.dated_futures.append(
                provider.symbology.enrich(
                    Contract(symbol=root, segment=Segment.FUTURES, expiry=expiry)
                )
            )

        if not include_options:
            continue

        option_expiries = _pick_expiries(
            provider.fetch_expiries(root, Segment.OPTIONS),
            cutoff=cutoff,
            ahead=expiries_ahead,
            behind=expired_behind,
        )
        if not option_expiries:
            universe.notes.append(f"{root}: no listed option expiries around {cutoff}")
            continue

        try:
            spot_price = provider.last_close(spot)
        except MarketDataError as exc:
            universe.notes.append(f"{root}: cannot read spot to anchor strikes ({exc})")
            continue

        for expiry in option_expiries:
            try:
                universe.options.extend(
                    provider.option_universe(
                        root,
                        expiry,
                        spot=spot_price,
                        strikes_each_side=strikes_each_side,
                    )
                )
            except MarketDataError as exc:
                # A root whose strikes are absent from the master is a coverage
                # gap to report, not a reason to abandon the whole universe.
                universe.notes.append(f"{root} {expiry}: no option ladder ({exc})")

    for symbol in equities:
        universe.equities.append(
            provider.symbology.enrich(Contract(symbol=symbol, segment=Segment.EQUITY))
        )

    return universe


def _pick_expiries(
    expiries: list[date], *, cutoff: date, ahead: int, behind: int
) -> list[date]:
    """Upcoming expiries plus a short tail of recently expired ones."""
    ordered = sorted(set(expiries))
    future = [e for e in ordered if e >= cutoff][:ahead]
    past = [e for e in ordered if e < cutoff][-behind:] if behind else []
    return past + future
