"""Ingest bars into the Parquet warehouse.

    python scripts/fetch_data.py --source synthetic --days 120
    python scripts/fetch_data.py --source yahoo --days 55

Yahoo is a research-only stopgap: intraday history is capped near 60 days and
index quotes are spot rather than futures. The script prints a data quality
report for each symbol and refuses to silently store a dirty frame.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import (  # noqa: E402
    load_instrument_master,
    load_settings,
    load_strategy_spec,
)
from alpha_desk.data.providers.synthetic import SyntheticProvider  # noqa: E402
from alpha_desk.data.providers.yahoo import YahooProvider  # noqa: E402
from alpha_desk.data.warehouse import ParquetWarehouse  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch bars into the Parquet warehouse")
    parser.add_argument("--source", choices=["synthetic", "yahoo"], default="synthetic")
    parser.add_argument("--days", type=int, default=55)
    parser.add_argument("--timeframe", default=None, help="defaults to the strategy spec bar")
    parser.add_argument("--symbols", nargs="*", default=None)
    args = parser.parse_args()

    settings = load_settings()
    spec = load_strategy_spec()
    master = load_instrument_master()

    timeframe = args.timeframe or spec.bars.primary_timeframe
    symbols = args.symbols or list(spec.universe.underlyings)
    end = date.today()
    start = end - timedelta(days=args.days)

    if args.source == "yahoo":
        provider = YahooProvider({s: master.spec(s).data_proxy for s in symbols})
        cap = provider.max_lookback_days(timeframe)
        if args.days > cap:
            print(f"note: Yahoo caps {timeframe} history near {cap} days; requested {args.days}")
    else:
        provider = SyntheticProvider()

    warehouse = ParquetWarehouse(settings.data_root)
    print(f"\nsource={provider.name} timeframe={timeframe} window={start}..{end}")
    print(f"warehouse={warehouse.root}\n")

    exit_code = 0
    for symbol in symbols:
        frame = provider.fetch(symbol, timeframe, str(start), str(end))
        if frame.empty:
            print(f"  {symbol}: no data returned")
            exit_code = 1
            continue
        report = warehouse.write_bars(provider.name, symbol, timeframe, frame)
        status = "clean" if report.is_clean else "ISSUES: " + "; ".join(report.issues)
        print(f"  {symbol}: {report.rows} bars {report.first_ts} .. {report.last_ts}  [{status}]")
        if not report.is_clean:
            exit_code = 1

    print("\nreminder: Yahoo/synthetic data is research plumbing, not promotion evidence.\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
