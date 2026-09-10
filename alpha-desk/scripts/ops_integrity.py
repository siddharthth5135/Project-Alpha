"""Daily integrity check for the market-data store.

Fails loud if promoted bars are stale or missing for the contracts we care about.
Designed for Task Scheduler (exit code != 0 → FAIL heartbeat).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.data.store import MarketStore  # noqa: E402
from alpha_desk.marketdata.contracts import IST, BarInterval, contract_from_id  # noqa: E402

DEFAULT_CONTRACTS = (
    "NSE:FUT:NIFTY:2026-09-29",
    "NSE:FUT:NIFTY:CONT-vendor_spliced",
    "NSE:FUT:BANKNIFTY:CONT-vendor_spliced",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--max-age-hours", type=float, default=36.0)
    parser.add_argument(
        "--contracts",
        default=",".join(DEFAULT_CONTRACTS),
        help="comma-separated contract_ids",
    )
    parser.add_argument("--data-root", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings()
    store = MarketStore(args.data_root or settings.data_root)
    interval = BarInterval(args.interval)
    now = datetime.now(ZoneInfo(IST))
    report: dict[str, object] = {"checked_at": now.isoformat(), "interval": interval.value, "contracts": {}}
    failures: list[str] = []

    for raw in [c.strip() for c in args.contracts.split(",") if c.strip()]:
        contract = contract_from_id(raw)
        try:
            frame = store.read_bars(
                source="truedata",
                contract=contract,
                interval=interval.value,
            )
        except Exception as exc:  # noqa: BLE001 - integrity must never crash silent
            report["contracts"][raw] = {"error": str(exc)}
            failures.append(f"{raw}: read error ({exc})")
            continue
        if frame is None or frame.empty:
            report["contracts"][raw] = {"rows": 0}
            failures.append(f"{raw}: no promoted bars")
            continue
        last = frame["ts"].max()
        if getattr(last, "tzinfo", None) is None:
            last = last.tz_localize(IST)
        age_h = (now - last.to_pydatetime()).total_seconds() / 3600.0
        entry = {
            "rows": int(len(frame)),
            "last_ts": str(last),
            "age_hours": round(age_h, 2),
        }
        report["contracts"][raw] = entry
        # On weekends / holidays age can exceed session gap; still flag extreme staleness.
        if age_h > args.max_age_hours and now.weekday() < 5:
            failures.append(f"{raw}: last bar {age_h:.1f}h old (limit {args.max_age_hours}h)")

    out_dir = settings.log_root / "ops"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"integrity_{now.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if failures:
        print("INTEGRITY FAIL:")
        for line in failures:
            print(f"  - {line}")
        return 2
    print("INTEGRITY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
