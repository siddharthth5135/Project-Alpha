"""Exit 0 if NSE is open today, 1 if holiday/weekend.

    python scripts/ops_is_trading_day.py
    python scripts/ops_is_trading_day.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.marketdata.calendar import load_market_calendar  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    cal = load_market_calendar()
    today = date.today()
    open_today = cal.is_trading_day(today)
    payload = {
        "date": today.isoformat(),
        "is_trading_day": open_today,
        "weekday": today.strftime("%A"),
        "verified": cal.is_verified(today),
    }
    if args.json:
        print(json.dumps(payload))
    else:
        print("open" if open_today else "holiday")
    return 0 if open_today else 1


if __name__ == "__main__":
    raise SystemExit(main())
