"""Mutable desk state the Risk Kernel reads.

The one opinionated detail: `risk_basis_equity` is the minimum of current and
start-of-day equity. Losses therefore shrink permitted size and intraday gains
never inflate it, which makes catch-up scaling arithmetically impossible
rather than merely forbidden by policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from alpha_desk.core.types import Fill, Instrument, Side


@dataclass
class Position:
    symbol: str
    side: Side
    lots: int
    quantity: int
    avg_price: float
    correlation_cluster: str = "UNCLUSTERED"
    margin_pct: float = 100.0

    @property
    def notional_inr(self) -> float:
        return self.quantity * self.avg_price

    @property
    def margin_inr(self) -> float:
        return self.notional_inr * self.margin_pct / 100.0


@dataclass
class RiskState:
    start_of_day_equity: float
    current_equity: float
    peak_equity: float
    session_date: date
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl_today: float = 0.0
    trades_today: int = 0
    new_signals_today: int = 0
    data_feed_ok: bool = True
    broker_connected: bool = True
    last_heartbeat: datetime | None = None
    halts: set[str] = field(default_factory=set)
    halt_details: dict[str, str] = field(default_factory=dict)

    @classmethod
    def fresh(cls, equity: float, session_date: date | None = None) -> "RiskState":
        return cls(
            start_of_day_equity=equity,
            current_equity=equity,
            peak_equity=equity,
            session_date=session_date or datetime.now(timezone.utc).date(),
            last_heartbeat=datetime.now(timezone.utc),
        )

    # -- equity ------------------------------------------------------- #

    @property
    def risk_basis_equity(self) -> float:
        return min(self.current_equity, self.start_of_day_equity)

    @property
    def day_pnl(self) -> float:
        return self.current_equity - self.start_of_day_equity

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.current_equity) / self.peak_equity * 100.0)

    def mark_equity(self, equity: float) -> None:
        self.current_equity = equity
        self.peak_equity = max(self.peak_equity, equity)

    def apply_pnl(self, pnl: float) -> None:
        self.realized_pnl_today += pnl
        self.mark_equity(self.current_equity + pnl)

    def roll_session(self, new_date: date) -> None:
        self.session_date = new_date
        self.start_of_day_equity = self.current_equity
        self.realized_pnl_today = 0.0
        self.trades_today = 0
        self.new_signals_today = 0
        # Daily-loss halts expire with the session; drawdown halts do not.
        self.clear_halt("DAILY_LOSS_HALT")

    # -- positions ---------------------------------------------------- #

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    @property
    def gross_notional_inr(self) -> float:
        return sum(p.notional_inr for p in self.positions.values())

    @property
    def total_margin_inr(self) -> float:
        return sum(p.margin_inr for p in self.positions.values())

    def cluster_count(self, cluster: str) -> int:
        return sum(1 for p in self.positions.values() if p.correlation_cluster == cluster)

    def register_fill(self, fill: Fill, instrument: Instrument | None = None) -> None:
        """Update positions and mark equity from realized PnL + costs.

        Closing/reducing an opposite-side fill realizes price PnL. Every fill
        also deducts ``fill.cost_inr`` so daily-loss / drawdown halts can fire
        from the paper (and later live) book, not only from manual ``apply_pnl``.
        """
        existing = self.positions.get(fill.symbol)
        realized = 0.0

        if existing is None:
            self.positions[fill.symbol] = Position(
                symbol=fill.symbol,
                side=fill.side,
                lots=fill.lots,
                quantity=fill.quantity,
                avg_price=fill.price,
                correlation_cluster=(
                    instrument.correlation_cluster if instrument else "UNCLUSTERED"
                ),
                margin_pct=instrument.margin_pct if instrument else 100.0,
            )
        elif existing.side is fill.side:
            total_qty = existing.quantity + fill.quantity
            existing.avg_price = (
                existing.avg_price * existing.quantity + fill.price * fill.quantity
            ) / total_qty
            existing.quantity = total_qty
            existing.lots += fill.lots
        else:
            closed_qty = min(existing.quantity, fill.quantity)
            if existing.side is Side.BUY:
                realized = (fill.price - existing.avg_price) * closed_qty
            else:
                realized = (existing.avg_price - fill.price) * closed_qty
            existing.quantity -= fill.quantity
            existing.lots -= fill.lots
            if existing.quantity <= 0:
                del self.positions[fill.symbol]
            elif instrument is not None:
                existing.correlation_cluster = instrument.correlation_cluster
                existing.margin_pct = instrument.margin_pct

        # Costs always hit equity; realized price PnL on closes/reduces.
        self.apply_pnl(realized - float(fill.cost_inr or 0.0))
        self.trades_today += 1

    # -- halts -------------------------------------------------------- #

    @property
    def is_halted(self) -> bool:
        return bool(self.halts)

    def engage_halt(self, code: str, detail: str = "") -> None:
        self.halts.add(code)
        self.halt_details[code] = detail

    def clear_halt(self, code: str) -> None:
        self.halts.discard(code)
        self.halt_details.pop(code, None)

    def heartbeat(self, ts: datetime | None = None) -> None:
        self.last_heartbeat = ts or datetime.now(timezone.utc)

    def heartbeat_age_seconds(self, now: datetime) -> float:
        if self.last_heartbeat is None:
            return float("inf")
        return (now - self.last_heartbeat).total_seconds()
