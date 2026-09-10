"""Learn observed NSE trading days from stored index EOD bars.

The configured holiday list is incomplete by construction (lunar holidays,
adhoc closures). After a backfill of NIFTY 50 daily bars, this writes the
exchange-observed set so gap detection stops inventing false positives.

    python scripts/calendar_learn.py --source truedata
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.data.store import Dataset, MarketStore  # noqa: E402
from alpha_desk.marketdata.calendar import load_market_calendar  # noqa: E402
from alpha_desk.marketdata.contracts import BarInterval, Contract, Segment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="truedata")
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--out", type=Path, default=REPO / "configs" / "observed_trading_days.json")
    args = parser.parse_args()

    settings = load_settings()
    store = MarketStore(settings.data_root)
    contract = Contract(symbol=args.symbol, segment=Segment.INDEX)
    bars = store.read_bars(
        source=args.source, contract=contract, interval=BarInterval.EOD.value
    )
    if bars.empty:
        print(f"no EOD bars for {contract.contract_id} from {args.source}")
        print("backfill index daily first: python scripts/td_backfill.py --scope index --interval 1d")
        return 1

    days = sorted({pd.Timestamp(ts).date().isoformat() for ts in bars["ts"]})
    payload = {
        "source": args.source,
        "contract_id": contract.contract_id,
        "rows": len(bars),
        "first": days[0],
        "last": days[-1],
        "trading_days": days,
    }
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    calendar = load_market_calendar()
    configured = {d.isoformat() for d in calendar.holidays}
    observed = set(days)
    print(f"wrote {len(days)} observed days to {args.out}")
    print(f"configured holidays that still traded: {sorted(configured & observed)[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
