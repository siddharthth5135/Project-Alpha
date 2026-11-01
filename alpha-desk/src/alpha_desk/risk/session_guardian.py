"""Session actuators: force-flatten clock + feed-loss flatten.

Risk kernel already *rejects* new risk at force-flatten / feed-stale. This
module *acts*: it calls ``OrderPath.flatten`` so open paper risk is closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from alpha_desk.config.settings import RiskConfig
from alpha_desk.core.types import Fill, Mode, Side
from alpha_desk.pipeline.order_path import OrderPath
from alpha_desk.risk.kernel import HALT_FEED_LOSS
from alpha_desk.risk.persist import save_risk_state
from alpha_desk.risk.state import RiskState


@dataclass
class SessionActuation:
    flattened: bool = False
    reason: str = ""
    fills: list[Fill] | None = None
    feed_halted: bool = False


class SessionGuardian:
    def __init__(self, risk_config: RiskConfig, data_root) -> None:
        self._cfg = risk_config
        self._data_root = data_root

    def tick(
        self,
        *,
        now: datetime,
        state: RiskState,
        order_path: OrderPath,
        feed_ok: bool | None = None,
    ) -> SessionActuation:
        result = SessionActuation()
        if feed_ok is not None:
            state.data_feed_ok = feed_ok
            if feed_ok:
                state.heartbeat(now)
            else:
                state.engage_halt(HALT_FEED_LOSS, "session guardian: feed marked down")
                result.feed_halted = True

        # Feed-loss flatten (ops config is mandatory true)
        if (
            self._cfg.ops.flatten_on_data_feed_loss
            and (not state.data_feed_ok or HALT_FEED_LOSS in state.halts)
            and state.open_position_count > 0
        ):
            fills = self._flatten(order_path, state, "FEED_LOSS_FLATTEN", now)
            result.flattened = True
            result.reason = "FEED_LOSS_FLATTEN"
            result.fills = fills
            save_risk_state(state, self._data_root)
            return result

        # Clock force-flatten (no overnight)
        local = now
        if local.tzinfo is not None:
            t = local.timetz().replace(tzinfo=None)
        else:
            t = local.time()
        if (
            not self._cfg.session.allow_overnight
            and t >= self._cfg.session.force_flatten_at
            and state.open_position_count > 0
        ):
            fills = self._flatten(order_path, state, "FORCE_FLATTEN_CLOCK", now)
            result.flattened = True
            result.reason = "FORCE_FLATTEN_CLOCK"
            result.fills = fills
            save_risk_state(state, self._data_root)
            return result

        save_risk_state(state, self._data_root)
        return result

    def _flatten(
        self, order_path: OrderPath, state: RiskState, reason: str, now: datetime
    ) -> list[Fill]:
        fills = order_path.flatten(reason, state)
        if state.open_position_count == 0:
            return fills
        # Broker book empty but RiskState still open (restart desync) — close state.
        synth: list[Fill] = []
        for pos in list(state.positions.values()):
            exit_side = Side.SELL if pos.side is Side.BUY else Side.BUY
            fill = Fill(
                order_id=f"STATE-FLAT-{pos.symbol}",
                draft_id="session_guardian",
                ts=now,
                symbol=pos.symbol,
                side=exit_side,
                lots=pos.lots,
                quantity=pos.quantity,
                price=pos.avg_price,
                cost_inr=0.0,
                mode=Mode.PAPER,
            )
            state.register_fill(fill)
            synth.append(fill)
        return [*fills, *synth]
