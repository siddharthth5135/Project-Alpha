"""Backfill historical market data into the warehouse.

Scope follows the trial priority: options first, then futures, then equities.
Runs are resumable, every write is quality-gated and audited, and a run that
cannot be served by the subscription fails loudly per contract instead of
silently storing a short series.

    python scripts/td_backfill.py --scope futures --interval 15m --days 180
    python scripts/td_backfill.py --scope options --interval 1m --days 30 --strikes 5
    python scripts/td_backfill.py --provider truedata_mock --scope all --days 30 --dry-run
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

from alpha_desk.audit.logger import AuditLog  # noqa: E402
from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.data.store import MarketStore  # noqa: E402
from alpha_desk.ingest.service import IngestionService, summarise  # noqa: E402
from alpha_desk.ingest.universe import build_trial_universe  # noqa: E402
from alpha_desk.marketdata.contracts import IST, BarInterval  # noqa: E402
from alpha_desk.marketdata.quality import QualityThresholds  # noqa: E402
from alpha_desk.marketdata.registry import get_provider  # noqa: E402

SCOPES = ("options", "futures", "equities", "index", "all")


def select(universe, scope: str) -> list:
    if scope == "options":
        return universe.options
    if scope == "futures":
        return [*universe.continuous_futures, *universe.dated_futures]
    if scope == "equities":
        return universe.equities
    if scope == "index":
        return universe.index_spots
    return universe.history_targets()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="truedata")
    parser.add_argument("--scope", choices=SCOPES, default="futures")
    parser.add_argument("--interval", default="15m", choices=[i.value for i in BarInterval])
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--start", default=None, help="ISO date; overrides --days")
    parser.add_argument("--end", default=None, help="ISO date; defaults to now")
    parser.add_argument("--underlyings", default="NIFTY,BANKNIFTY")
    parser.add_argument("--equities", default="RELIANCE,HDFCBANK,TCS")
    parser.add_argument("--strikes", type=int, default=10, help="strikes each side of ATM")
    parser.add_argument("--expiries-ahead", type=int, default=2)
    parser.add_argument("--max-contracts", type=int, default=0, help="0 = no cap")
    parser.add_argument("--no-incremental", action="store_true")
    parser.add_argument("--strict-calendar", action="store_true", help="quarantine unverified gaps")
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="resolve the universe only")
    args = parser.parse_args()

    interval = BarInterval(args.interval)
    end = pd.Timestamp(args.end, tz=IST) if args.end else pd.Timestamp.now(tz=IST)
    start = (
        pd.Timestamp(args.start, tz=IST)
        if args.start
        else end - pd.Timedelta(days=args.days)
    )

    settings = load_settings()
    root = args.data_root or settings.data_root
    store = MarketStore(root)
    audit = AuditLog(settings.log_root / "audit")

    provider = get_provider(args.provider)
    universe = build_trial_universe(
        provider,
        underlyings=tuple(s.strip().upper() for s in args.underlyings.split(",") if s.strip()),
        equities=tuple(s.strip().upper() for s in args.equities.split(",") if s.strip()),
        expiries_ahead=args.expiries_ahead,
        strikes_each_side=args.strikes,
        include_options=args.scope in {"options", "all"},
        today=date.today(),
    )
    print(f"universe: {json.dumps(universe.summary(), indent=2)}")

    contracts = select(universe, args.scope)
    if args.max_contracts:
        contracts = contracts[: args.max_contracts]

    print(
        f"\nbackfilling {len(contracts)} contracts, {interval.value}, "
        f"{start.date()} -> {end.date()} into {root}"
    )
    if args.dry_run:
        for contract in contracts[:40]:
            print(f"  {contract.contract_id}  <- {contract.vendor_symbol}")
        if len(contracts) > 40:
            print(f"  ... and {len(contracts) - 40} more")
        return 0

    service = IngestionService(
        provider,
        store,
        audit=audit,
        thresholds=QualityThresholds(require_verified_calendar=args.strict_calendar),
    )
    results = service.backfill_many(
        contracts,
        interval,
        start.to_pydatetime(),
        end.to_pydatetime(),
        incremental=not args.no_incremental,
    )

    for result in results:
        status = "ERROR" if result.error else result.report.verdict.value if result.report else "empty"
        print(f"  {status:<22} {result.contract_id:<44} rows={result.rows}")
        for warning in (result.report.warnings if result.report else []):
            print(f"      warn: {warning}")
        for error in (result.report.errors if result.report else []):
            print(f"      fail: {error}")

    summary = summarise(results)
    print("\nsummary: " + json.dumps(summary, indent=2))
    print(f"audit: {audit.path}")
    print(f"ingest log: {store.ingest_log_path}")

    return 1 if summary["errors"] or summary["quarantined"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
