"""Kill-switch drill instrumentation (playbook step P1.4.4).

    python scripts/kill_switch_drill.py

HoQR runs this before Gate G4. The script:
  1. Engages the kill switch on a flat book and proves new risk is refused
  2. Clears briefly, opens a paper position, re-engages, flattens, and times it
  3. Confirms drawdown+kill still refuse new risk
  4. Clears the kill switch with a written reason

Never places Angel live orders.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.core.types import Intent, Side  # noqa: E402
from alpha_desk.desk import build_desk  # noqa: E402
from alpha_desk.pipeline.order_path import PathStatus  # noqa: E402
from alpha_desk.risk.kernel import ReasonCode  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def _intent(desk, now: datetime, *, intent_id: str | None = None) -> Intent:
    nifty = desk.instruments["NIFTY"]
    kwargs = {}
    if intent_id:
        kwargs["intent_id"] = intent_id
    return Intent(
        ts=now,
        instrument=nifty,
        side=Side.BUY,
        ref_price=24_000.0,
        stop_price=23_960.0,
        target_price=24_160.0,
        strategy_id=desk.strategy_spec.strategy_id,
        alpha_score=1.5,
        **kwargs,
    )


def _kill_blocked(result) -> bool:
    return (
        result.status is PathStatus.REJECTED_BY_RISK
        and result.decision is not None
        and ReasonCode.KILL_SWITCH_ENGAGED in result.decision.reason_codes
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Kill-switch drill (paper only)")
    parser.add_argument("--engaged-by", default="HoQR")
    parser.add_argument(
        "--clear-reason",
        default="monthly kill-switch drill complete",
        help="written reason required to clear the switch",
    )
    parser.add_argument(
        "--leave-engaged",
        action="store_true",
        help="do not clear at the end (manual clear later)",
    )
    args = parser.parse_args()

    desk = build_desk()
    if desk.settings.mode.value == "live":
        print("REFUSING: ALPHA_MODE=live — drill is paper-only.")
        return 2

    # Start from a known-clear switch so a prior failed drill cannot poison this run.
    if desk.kill_switch.is_engaged():
        desk.kill_switch.clear(
            cleared_by=args.engaged_by,
            written_reason="auto-clear stale kill before P1.4.4 drill",
        )

    now = datetime(2026, 7, 24, 11, 0, tzinfo=IST)
    desk.state.heartbeat(now)
    failures = 0

    print("\nProject Alpha — Kill switch drill")
    print("=" * 70)
    print(f"  mode          : {desk.settings.mode.value}")
    print(f"  kill file     : {desk.kill_switch.path}")
    print(f"  audit dir     : {desk.audit.path.parent}")

    # --- Step 1: engage on a flat book, prove new risk is refused ---
    t0 = time.perf_counter()
    desk.kill_switch.engage("P1.4.4 kill switch drill", engaged_by=args.engaged_by)
    engaged_ms = (time.perf_counter() - t0) * 1000
    print(f"\n[1] engaged         : {desk.kill_switch.is_engaged()}  ({engaged_ms:.1f} ms)")
    print(f"    reason          : {desk.kill_switch.reason()}")

    blocked = desk.order_path.submit(_intent(desk, now, intent_id="drill-blocked"), desk.state, now)
    ok_block = _kill_blocked(blocked)
    print(f"[2] new risk blocked: {ok_block}  status={blocked.status.value}  detail={blocked.detail!r}")
    if not ok_block:
        print("FAIL: kill switch did not block new risk on a flat book.")
        failures += 1

    # --- Step 3: open a position (switch briefly clear), re-engage, flatten + time ---
    desk.kill_switch.clear(cleared_by=args.engaged_by, written_reason="brief clear to open drill position")
    filled = desk.order_path.submit(_intent(desk, now, intent_id="drill-baseline"), desk.state, now)
    print(f"[3] baseline entry : {filled.status.value}  detail={filled.detail!r}")
    if filled.status is not PathStatus.FILLED:
        print("WARN: could not open a baseline paper position; flatten timing will be a no-op")

    desk.kill_switch.engage("P1.4.4 kill switch drill (flatten phase)", engaged_by=args.engaged_by)
    t1 = time.perf_counter()
    closed = desk.order_path.flatten("kill switch drill", desk.state)
    flatten_ms = (time.perf_counter() - t1) * 1000
    open_left = len(getattr(desk.broker, "open_orders", {}) or {})
    print(f"[4] flatten         : closed={len(closed)}  open_left={open_left}  ({flatten_ms:.1f} ms)")

    # --- Step 5: DD + kill still refuse ---
    desk.state.peak_equity = desk.state.current_equity * 1.25
    dd = desk.order_path.submit(_intent(desk, now, intent_id="drill-dd"), desk.state, now)
    ok_dd = dd.status is PathStatus.REJECTED_BY_RISK
    print(f"[5] DD+kill refuse  : {ok_dd}  status={dd.status.value}  detail={dd.detail!r}")
    if not ok_dd:
        failures += 1

    # --- Step 6: clear ---
    if args.leave_engaged:
        print("[6] left engaged    : clear manually when ready")
    else:
        desk.kill_switch.clear(cleared_by=args.engaged_by, written_reason=args.clear_reason)
        print(f"[6] cleared         : engaged={desk.kill_switch.is_engaged()}")
        print(f"    clear reason    : {args.clear_reason!r}")

    events = {r["event"] for r in desk.audit.read_all()}
    need = {"RISK_DECISION", "FLATTEN"}
    print(f"\nAudit events present: {sorted(need & events)} (need {sorted(need)})")
    print(f"Flatten wall-clock  : {flatten_ms:.1f} ms  (record this for the drill log)")
    print("\nHoQR: sign Gate G4 only after you have timed a real flatten under load.")
    print("=" * 70)
    if failures:
        print(f"RESULT: {failures} check(s) FAILED.\n")
        return 1
    print("RESULT: drill checks PASS.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
