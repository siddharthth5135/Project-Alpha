"""Fill same-session (or recent) bar gaps from TrueData REST after live capture.

Use after market hours or after a WS drop. Reuses IngestionService backfill
(incremental by default) so paid-day ops need no new modules.

    python scripts/td_gapfill.py --scope futures --interval 1m --days 2
    python scripts/td_gapfill.py --scope futures --interval 15m --days 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

from alpha_desk.audit.logger import AuditEvent, AuditLog  # noqa: E402
from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.data.store import MarketStore  # noqa: E402
from alpha_desk.ingest.service import IngestionService, summarise  # noqa: E402
from alpha_desk.ingest.universe import build_trial_universe  # noqa: E402
from alpha_desk.marketdata.contracts import IST, BarInterval  # noqa: E402
from alpha_desk.marketdata.registry import get_provider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="truedata")
    parser.add_argument("--scope", choices=("futures", "options", "index", "equities", "all"), default="futures")
    parser.add_argument("--interval", default="1m", choices=[i.value for i in BarInterval if i is not BarInterval.TICK])
    parser.add_argument("--days", type=int, default=2, help="lookback calendar days (clamped by plan)")
    parser.add_argument("--underlyings", default="NIFTY,BANKNIFTY")
    parser.add_argument("--strikes", type=int, default=3)
    parser.add_argument("--no-incremental", action="store_true")
    parser.add_argument("--data-root", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings()
    store = MarketStore(args.data_root or settings.data_root)
    audit = AuditLog(settings.log_root / "audit")
    provider = get_provider(args.provider)

    universe = build_trial_universe(
        provider,
        underlyings=tuple(s.strip().upper() for s in args.underlyings.split(",") if s.strip()),
        expiries_ahead=1,
        strikes_each_side=args.strikes,
        include_options=args.scope in {"options", "all"},
        today=date.today(),
    )
    if args.scope == "futures":
        contracts = [*universe.continuous_futures, *universe.dated_futures]
    elif args.scope == "options":
        contracts = universe.options
    elif args.scope == "index":
        contracts = universe.index_spots
    elif args.scope == "equities":
        contracts = universe.equities
    else:
        contracts = universe.history_targets()

    end = pd.Timestamp.now(tz=IST)
    start = end - pd.Timedelta(days=args.days)
    interval = BarInterval(args.interval)
    service = IngestionService(provider, store, audit=audit)
    results = service.backfill_many(
        contracts,
        interval,
        start.to_pydatetime(),
        end.to_pydatetime(),
        incremental=not args.no_incremental,
    )
    summary = summarise(results)
    print(json.dumps(summary, indent=2, default=str))
    audit.write(
        AuditEvent.MARKET_DATA,
        stage="td_gapfill",
        summary=summary,
        interval=interval.value,
        days=args.days,
    )
    # Partial quarantine is exit 1; hard failure only if every contract errored with 0 rows
    if summary.get("errors") and summary.get("rows", 0) == 0:
        return 2
    if summary.get("quarantined"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
