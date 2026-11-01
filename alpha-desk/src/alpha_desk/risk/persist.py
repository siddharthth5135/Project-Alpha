"""Persist / restore RiskState across process restarts (paper desk)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from alpha_desk.core.types import Side
from alpha_desk.risk.state import Position, RiskState


def state_path(data_root: Path) -> Path:
    return Path(data_root) / "state" / "risk_state.json"


def save_risk_state(state: RiskState, data_root: Path) -> Path:
    path = state_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "start_of_day_equity": state.start_of_day_equity,
        "current_equity": state.current_equity,
        "peak_equity": state.peak_equity,
        "session_date": state.session_date.isoformat(),
        "realized_pnl_today": state.realized_pnl_today,
        "trades_today": state.trades_today,
        "new_signals_today": state.new_signals_today,
        "data_feed_ok": state.data_feed_ok,
        "broker_connected": state.broker_connected,
        "last_heartbeat": state.last_heartbeat.isoformat() if state.last_heartbeat else None,
        "halts": sorted(state.halts),
        "halt_details": dict(state.halt_details),
        "positions": {
            sym: {
                "symbol": p.symbol,
                "side": p.side.value,
                "lots": p.lots,
                "quantity": p.quantity,
                "avg_price": p.avg_price,
                "correlation_cluster": p.correlation_cluster,
                "margin_pct": p.margin_pct,
            }
            for sym, p in state.positions.items()
        },
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_risk_state(data_root: Path, *, fallback_equity: float) -> RiskState:
    path = state_path(data_root)
    if not path.exists():
        return RiskState.fresh(fallback_equity)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RiskState.fresh(fallback_equity)

    positions: dict[str, Position] = {}
    for sym, row in (raw.get("positions") or {}).items():
        positions[sym] = Position(
            symbol=row["symbol"],
            side=Side(row["side"]),
            lots=int(row["lots"]),
            quantity=int(row["quantity"]),
            avg_price=float(row["avg_price"]),
            correlation_cluster=str(row.get("correlation_cluster") or "UNCLUSTERED"),
            margin_pct=float(row.get("margin_pct") or 100.0),
        )

    hb = raw.get("last_heartbeat")
    state = RiskState(
        start_of_day_equity=float(raw.get("start_of_day_equity", fallback_equity)),
        current_equity=float(raw.get("current_equity", fallback_equity)),
        peak_equity=float(raw.get("peak_equity", fallback_equity)),
        session_date=date.fromisoformat(raw["session_date"])
        if raw.get("session_date")
        else date.today(),
        positions=positions,
        realized_pnl_today=float(raw.get("realized_pnl_today") or 0.0),
        trades_today=int(raw.get("trades_today") or 0),
        new_signals_today=int(raw.get("new_signals_today") or 0),
        data_feed_ok=bool(raw.get("data_feed_ok", True)),
        broker_connected=bool(raw.get("broker_connected", True)),
        last_heartbeat=datetime.fromisoformat(hb) if hb else None,
        halts=set(raw.get("halts") or []),
        halt_details=dict(raw.get("halt_details") or {}),
    )
    # New calendar day → roll session equity baseline
    today = date.today()
    if state.session_date != today:
        state.roll_session(today)
    return state
