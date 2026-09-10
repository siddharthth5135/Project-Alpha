"""Risk Kernel - deterministic, absolute veto.

No model, no LLM, no heuristics: a fixed list of checks over a draft order and
the current desk state. A rejection is a successful outcome whenever a limit
says so. Two structural guarantees are worth calling out:

* A draft that never passed the loss gate is rejected, so the gate cannot be
  bypassed by calling the kernel directly.
* Permitted size is derived from `risk_basis_equity` (min of current and
  start-of-day equity), so size cannot grow after losses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from alpha_desk.config.settings import AppSettings, RiskConfig
from alpha_desk.core.types import (
    DraftOrder,
    LossAction,
    Mode,
    RiskDecision,
    RiskReason,
    Side,
    Verdict,
)
from alpha_desk.loss.interface import expected_value_inr
from alpha_desk.risk.killswitch import KillSwitch
from alpha_desk.risk.state import RiskState


class ReasonCode:
    KILL_SWITCH_ENGAGED = "R001_KILL_SWITCH_ENGAGED"
    DESK_HALTED = "R002_DESK_HALTED"
    DAILY_LOSS_BREACH = "R003_DAILY_LOSS_BREACH"
    DRAWDOWN_BREACH = "R004_DRAWDOWN_BREACH"
    DATA_FEED_STALE = "R005_DATA_FEED_STALE"
    BROKER_DISCONNECTED = "R006_BROKER_DISCONNECTED"
    LIVE_WITHOUT_SIGNATURE = "R007_LIVE_WITHOUT_SIGNATURE"
    UNSIGNED_RISK_CONFIG = "R008_UNSIGNED_RISK_CONFIG"
    MISSING_LOSS_ADVICE = "R009_MISSING_LOSS_ADVICE"
    LOSS_GATE_BLOCKED = "R010_LOSS_GATE_BLOCKED"
    NO_STOP_LOSS = "R011_NO_STOP_LOSS"
    NON_POSITIVE_LOTS = "R012_NON_POSITIVE_LOTS"
    LOTS_OVER_ORDER_CAP = "R013_LOTS_OVER_ORDER_CAP"
    LOTS_OVER_INSTRUMENT_CAP = "R014_LOTS_OVER_INSTRUMENT_CAP"
    RISK_PER_TRADE_BREACH = "R015_RISK_PER_TRADE_BREACH"
    RUPEE_RISK_CAP_BREACH = "R016_RUPEE_RISK_CAP_BREACH"
    RR_BELOW_MINIMUM = "R017_RR_BELOW_MINIMUM"
    DAILY_BUDGET_EXHAUSTED = "R018_DAILY_BUDGET_EXHAUSTED"
    MAX_CONCURRENT_POSITIONS = "R019_MAX_CONCURRENT_POSITIONS"
    CLUSTER_CONCENTRATION = "R020_CLUSTER_CONCENTRATION"
    MARGIN_UTILISATION_BREACH = "R021_MARGIN_UTILISATION_BREACH"
    SINGLE_INSTRUMENT_MARGIN = "R022_SINGLE_INSTRUMENT_MARGIN"
    NO_NEW_RISK_WINDOW = "R023_NO_NEW_RISK_WINDOW"
    OUTSIDE_SESSION = "R024_OUTSIDE_SESSION"
    MAX_TRADES_PER_DAY = "R025_MAX_TRADES_PER_DAY"
    INVALID_STOP_SIDE = "R026_INVALID_STOP_SIDE"
    INVALID_PRICE = "R027_INVALID_PRICE"
    NEGATIVE_EXPECTED_VALUE = "R028_NEGATIVE_EXPECTED_VALUE"
    STRESS_MARGIN_NEW_RISK = "R029_STRESS_MARGIN_NEW_RISK"
    OVERNIGHT_FORBIDDEN = "R030_OVERNIGHT_FORBIDDEN"


HALT_DAILY_LOSS = "DAILY_LOSS_HALT"
HALT_DRAWDOWN = "DRAWDOWN_HALT"
HALT_FEED_LOSS = "FEED_LOSS_HALT"


@dataclass
class _Ctx:
    draft: DraftOrder
    state: RiskState
    now: datetime
    reasons: list[RiskReason]
    halts: list[str]

    def reject(self, code: str, detail: str) -> None:
        self.reasons.append(RiskReason(code=code, detail=detail))


class RiskKernel:
    def __init__(
        self,
        config: RiskConfig,
        kill_switch: KillSwitch,
        settings: AppSettings | None = None,
    ) -> None:
        self._cfg = config
        self._kill = kill_switch
        self._settings = settings
        self._tz = ZoneInfo(config.session.timezone)

    @property
    def config(self) -> RiskConfig:
        return self._cfg

    # ------------------------------------------------------------------ #
    # halt evaluation (runs on every tick, not only on orders)
    # ------------------------------------------------------------------ #

    def evaluate_halts(self, state: RiskState, now: datetime) -> tuple[str, ...]:
        triggered: list[str] = []

        loss_limit = self._cfg.max_daily_loss_inr(state.start_of_day_equity)
        if -state.day_pnl >= loss_limit and HALT_DAILY_LOSS not in state.halts:
            state.engage_halt(
                HALT_DAILY_LOSS,
                f"day pnl {state.day_pnl:.2f} breached limit -{loss_limit:.2f}",
            )
            triggered.append(HALT_DAILY_LOSS)

        if (
            state.drawdown_pct >= self._cfg.drawdown.max_drawdown_halt_pct
            and HALT_DRAWDOWN not in state.halts
        ):
            state.engage_halt(
                HALT_DRAWDOWN,
                f"drawdown {state.drawdown_pct:.2f}% breached "
                f"{self._cfg.drawdown.max_drawdown_halt_pct:.2f}%",
            )
            triggered.append(HALT_DRAWDOWN)

        feed_stale = (
            not state.data_feed_ok
            or state.heartbeat_age_seconds(now) > self._cfg.ops.max_heartbeat_age_seconds
        )
        if feed_stale and HALT_FEED_LOSS not in state.halts:
            state.engage_halt(HALT_FEED_LOSS, "market data heartbeat stale or feed down")
            triggered.append(HALT_FEED_LOSS)
        elif not feed_stale:
            state.clear_halt(HALT_FEED_LOSS)

        return tuple(triggered)

    # ------------------------------------------------------------------ #
    # order evaluation
    # ------------------------------------------------------------------ #

    def evaluate(self, draft: DraftOrder, state: RiskState, now: datetime) -> RiskDecision:
        halts = list(self.evaluate_halts(state, now))
        ctx = _Ctx(draft=draft, state=state, now=now, reasons=[], halts=halts)

        self._check_global_stops(ctx)
        self._check_mode(ctx)
        self._check_order_sanity(ctx)

        if draft.intent.reduce_only:
            # Exits are how we get out of trouble; only hard blockers apply.
            return self._decide(ctx)

        self._check_loss_gate(ctx)
        self._check_size_caps(ctx)
        self._check_quality(ctx)
        self._check_portfolio_caps(ctx)
        self._check_session(ctx)
        return self._decide(ctx)

    # -- checks -------------------------------------------------------- #

    def _check_global_stops(self, ctx: _Ctx) -> None:
        if self._kill.is_engaged():
            ctx.reject(
                ReasonCode.KILL_SWITCH_ENGAGED,
                f"kill switch engaged: {self._kill.reason()}",
            )

        if ctx.draft.intent.reduce_only:
            return

        for code, halt in (
            (ReasonCode.DAILY_LOSS_BREACH, HALT_DAILY_LOSS),
            (ReasonCode.DRAWDOWN_BREACH, HALT_DRAWDOWN),
            (ReasonCode.DATA_FEED_STALE, HALT_FEED_LOSS),
        ):
            if halt in ctx.state.halts:
                ctx.reject(code, ctx.state.halt_details.get(halt, halt))

        other_halts = ctx.state.halts - {HALT_DAILY_LOSS, HALT_DRAWDOWN, HALT_FEED_LOSS}
        if other_halts:
            ctx.reject(ReasonCode.DESK_HALTED, f"active halts: {sorted(other_halts)}")

        if not ctx.state.broker_connected and self._cfg.ops.flatten_on_broker_disconnect:
            ctx.reject(ReasonCode.BROKER_DISCONNECTED, "broker session is down")

    def _check_mode(self, ctx: _Ctx) -> None:
        settings = self._settings
        if settings is None or settings.mode is not Mode.LIVE:
            return
        if not settings.live_enabled:
            ctx.reject(
                ReasonCode.LIVE_WITHOUT_SIGNATURE,
                "live mode without HoQR GO signature",
            )
        if not self._cfg.is_signed:
            ctx.reject(
                ReasonCode.UNSIGNED_RISK_CONFIG,
                "risk_config.yaml has no HoQR signature",
            )

    def _check_order_sanity(self, ctx: _Ctx) -> None:
        d = ctx.draft
        if d.lots <= 0:
            ctx.reject(ReasonCode.NON_POSITIVE_LOTS, f"lots={d.lots}")
        if d.entry_price <= 0:
            ctx.reject(ReasonCode.INVALID_PRICE, f"entry_price={d.entry_price}")
        if d.intent.reduce_only:
            return
        if self._cfg.per_trade.require_stop_loss and d.stop_price <= 0:
            ctx.reject(ReasonCode.NO_STOP_LOSS, "stop price missing")
        elif d.stop_price > 0:
            wrong_side = (
                d.side is Side.BUY and d.stop_price >= d.entry_price
            ) or (d.side is Side.SELL and d.stop_price <= d.entry_price)
            if wrong_side:
                ctx.reject(
                    ReasonCode.INVALID_STOP_SIDE,
                    f"{d.side.value} stop {d.stop_price} on wrong side of entry {d.entry_price}",
                )

    def _check_loss_gate(self, ctx: _Ctx) -> None:
        advice = ctx.draft.loss_advice
        if advice is None:
            ctx.reject(
                ReasonCode.MISSING_LOSS_ADVICE,
                "draft reached the kernel without a loss-gate advisory",
            )
            return
        if advice.action in (LossAction.SKIP, LossAction.FLATTEN):
            ctx.reject(
                ReasonCode.LOSS_GATE_BLOCKED,
                f"loss gate says {advice.action.value} (p_hit_stop={advice.p_hit_stop:.3f})",
            )
        if advice.p_hit_stop > self._cfg.loss_gate.absolute_block_p_hit_stop:
            ctx.reject(
                ReasonCode.LOSS_GATE_BLOCKED,
                f"p_hit_stop {advice.p_hit_stop:.3f} above absolute backstop "
                f"{self._cfg.loss_gate.absolute_block_p_hit_stop}",
            )

        # Independent EV check. The gate already looks at this; the kernel does
        # not trust it to have done so.
        ev = expected_value_inr(ctx.draft, advice.p_hit_stop)
        if ev <= 0:
            ctx.reject(
                ReasonCode.NEGATIVE_EXPECTED_VALUE,
                f"expected value Rs{ev:.0f} at p_hit_stop {advice.p_hit_stop:.3f}",
            )

    def _check_size_caps(self, ctx: _Ctx) -> None:
        d, pt = ctx.draft, self._cfg.per_trade
        if d.lots > pt.max_lots_per_order:
            ctx.reject(
                ReasonCode.LOTS_OVER_ORDER_CAP,
                f"{d.lots} lots exceeds per-order cap {pt.max_lots_per_order}",
            )

        existing = ctx.state.positions.get(d.instrument.symbol)
        existing_lots = existing.lots if existing and existing.side is d.side else 0
        if existing_lots + d.lots > pt.max_lots_per_instrument:
            ctx.reject(
                ReasonCode.LOTS_OVER_INSTRUMENT_CAP,
                f"{existing_lots}+{d.lots} lots exceeds instrument cap "
                f"{pt.max_lots_per_instrument}",
            )

        basis = ctx.state.risk_basis_equity
        pct_cap = basis * pt.max_risk_per_trade_pct / 100.0
        if d.risk_inr > pct_cap:
            ctx.reject(
                ReasonCode.RISK_PER_TRADE_BREACH,
                f"risk Rs{d.risk_inr:.0f} exceeds {pt.max_risk_per_trade_pct}% of "
                f"Rs{basis:.0f} = Rs{pct_cap:.0f}",
            )
        if d.risk_inr > pt.max_rupee_risk_per_trade:
            ctx.reject(
                ReasonCode.RUPEE_RISK_CAP_BREACH,
                f"risk Rs{d.risk_inr:.0f} exceeds absolute cap "
                f"Rs{pt.max_rupee_risk_per_trade:.0f}",
            )

        remaining = self._cfg.max_daily_loss_inr(ctx.state.start_of_day_equity) + min(
            0.0, ctx.state.day_pnl
        )
        if d.risk_inr > max(0.0, remaining):
            ctx.reject(
                ReasonCode.DAILY_BUDGET_EXHAUSTED,
                f"risk Rs{d.risk_inr:.0f} exceeds remaining daily budget Rs{remaining:.0f}",
            )

    def _check_quality(self, ctx: _Ctx) -> None:
        qg = self._cfg.quality_gate
        rr = ctx.draft.rr_after_costs
        if rr < qg.min_rr_after_costs:
            ctx.reject(
                ReasonCode.RR_BELOW_MINIMUM,
                f"R:R after costs {rr:.2f} below minimum {qg.min_rr_after_costs:.2f}",
            )
        if ctx.state.trades_today >= qg.max_trades_per_day:
            ctx.reject(
                ReasonCode.MAX_TRADES_PER_DAY,
                f"{ctx.state.trades_today} trades today reached cap {qg.max_trades_per_day}",
            )

    def _check_portfolio_caps(self, ctx: _Ctx) -> None:
        pf, d, state = self._cfg.portfolio, ctx.draft, ctx.state
        symbol = d.instrument.symbol
        is_new_symbol = symbol not in state.positions

        if is_new_symbol and state.open_position_count >= pf.max_concurrent_positions:
            ctx.reject(
                ReasonCode.MAX_CONCURRENT_POSITIONS,
                f"{state.open_position_count} open positions reached cap "
                f"{pf.max_concurrent_positions}",
            )

        cluster = d.instrument.correlation_cluster
        if is_new_symbol and state.cluster_count(cluster) >= pf.max_correlated_cluster_positions:
            ctx.reject(
                ReasonCode.CLUSTER_CONCENTRATION,
                f"cluster {cluster} already holds {state.cluster_count(cluster)} positions",
            )

        equity = state.risk_basis_equity
        projected_margin = state.total_margin_inr + d.margin_inr
        margin_pct_used = projected_margin / max(equity, 1e-9) * 100.0

        # New risk may only use the operating band. Reduce-only / flatten may
        # touch the stress band so a SPAN spike does not block an exit.
        reduce_only = bool(getattr(d.intent, "reduce_only", False))
        operating_cap = pf.max_margin_utilisation_pct
        stress_cap = pf.stress_margin_utilisation_pct
        effective_cap = stress_cap if reduce_only else operating_cap

        if not reduce_only and margin_pct_used > operating_cap:
            ctx.reject(
                ReasonCode.STRESS_MARGIN_NEW_RISK
                if margin_pct_used <= stress_cap
                else ReasonCode.MARGIN_UTILISATION_BREACH,
                f"new-risk margin {margin_pct_used:.1f}% exceeds operating cap "
                f"{operating_cap}% (stress {stress_cap}% is not available for new risk)",
            )
        elif margin_pct_used > effective_cap:
            ctx.reject(
                ReasonCode.MARGIN_UTILISATION_BREACH,
                f"margin Rs{projected_margin:.0f} is {margin_pct_used:.1f}% of equity "
                f"Rs{equity:.0f}, cap is {effective_cap}%",
            )

        existing_margin = state.positions[symbol].margin_inr if not is_new_symbol else 0.0
        symbol_margin_pct = (existing_margin + d.margin_inr) / max(equity, 1e-9) * 100.0
        symbol_cap = stress_cap if reduce_only else pf.max_single_instrument_margin_pct
        if symbol_margin_pct > symbol_cap:
            ctx.reject(
                ReasonCode.SINGLE_INSTRUMENT_MARGIN,
                f"{symbol} margin would be {symbol_margin_pct:.1f}% of equity, cap is "
                f"{symbol_cap}%",
            )

    def _check_session(self, ctx: _Ctx) -> None:
        local = ctx.now.astimezone(self._tz) if ctx.now.tzinfo else ctx.now
        t = local.time()
        s = self._cfg.session
        if not s.allow_overnight and t >= s.force_flatten_at and not ctx.draft.intent.reduce_only:
            ctx.reject(
                ReasonCode.OVERNIGHT_FORBIDDEN,
                f"{t} is at/after force-flatten {s.force_flatten_at}; overnight is forbidden",
            )
        if t < s.market_open or t > s.market_close:
            ctx.reject(ReasonCode.OUTSIDE_SESSION, f"{t} is outside {s.market_open}-{s.market_close}")
        elif t >= s.no_new_risk_after:
            ctx.reject(
                ReasonCode.NO_NEW_RISK_WINDOW,
                f"{t} is past no-new-risk cutoff {s.no_new_risk_after}",
            )

    # -- verdict ------------------------------------------------------- #

    def _decide(self, ctx: _Ctx) -> RiskDecision:
        if ctx.reasons:
            return RiskDecision(
                verdict=Verdict.REJECTED,
                approved_lots=0,
                reasons=tuple(ctx.reasons),
                halts_triggered=tuple(ctx.halts),
            )
        return RiskDecision(
            verdict=Verdict.APPROVED,
            approved_lots=ctx.draft.lots,
            reasons=(),
            halts_triggered=tuple(ctx.halts),
        )
