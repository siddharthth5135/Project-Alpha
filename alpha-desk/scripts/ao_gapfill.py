"""Angel One gap-fill after live capture (REST candles).

Refuses unless ANGELONE_ALLOW_MARKETDATA=true and --i-allow-network.
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
from alpha_desk.marketdata.errors import ProviderConfigError  # noqa: E402
from alpha_desk.marketdata.registry import get_provider  # noqa: E402


def select(universe, scope: str) -> list:
    if scope == "futures":
        return [*universe.continuous_futures, *universe.dated_futures]
    if scope == "options":
        return universe.options
    if scope == "index":
        return universe.index_spots
    if scope == "equities":
        return universe.equities
    return universe.history_targets()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--i-allow-network", action="store_true")
    parser.add_argument("--provider", default="angelone")
    parser.add_argument(
        "--scope", choices=("futures", "options", "index", "equities", "all"), default="futures"
    )
    parser.add_argument(
        "--interval",
        default="1m",
        choices=[i.value for i in BarInterval if i is not BarInterval.TICK],
    )
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--underlyings", default="NIFTY,BANKNIFTY")
    parser.add_argument("--equities", default="RELIANCE,HDFCBANK,TCS")
    parser.add_argument("--strikes", type=int, default=3)
    parser.add_argument("--no-incremental", action="store_true")
    parser.add_argument("--data-root", type=Path, default=None)
    args = parser.parse_args()

    if not args.i_allow_network:
        print("REFUSING: pass --i-allow-network after explicit approval. No API call made.")
        return 2

    settings = load_settings()
    try:
        provider = get_provider(args.provider)
    except ProviderConfigError as exc:
        print(f"REFUSING: {exc}")
        return 2
    if hasattr(provider, "config") and not getattr(provider.config, "allow_marketdata", True):
        print("REFUSING: ANGELONE_ALLOW_MARKETDATA is not true. No API call made.")
        return 2

    interval = BarInterval(args.interval)
    end = pd.Timestamp.now(tz=IST)
    start = end - pd.Timedelta(days=args.days)
    store = MarketStore(args.data_root or settings.data_root)
    audit = AuditLog(settings.log_root / "audit")
    universe = build_trial_universe(
        provider,
        underlyings=tuple(s.strip().upper() for s in args.underlyings.split(",") if s.strip()),
        equities=tuple(s.strip().upper() for s in args.equities.split(",") if s.strip()),
        strikes_each_side=args.strikes,
        include_options=args.scope in {"options", "all"},
        today=date.today(),
        expired_behind=0,
    )
    contracts = select(universe, args.scope)
    service = IngestionService(provider, store, audit=audit)
    results = service.backfill_many(
        contracts,
        interval,
        start.to_pydatetime(),
        end.to_pydatetime(),
        incremental=not args.no_incremental,
    )
    print(json.dumps(summarise(results), indent=2, default=str))
    provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
