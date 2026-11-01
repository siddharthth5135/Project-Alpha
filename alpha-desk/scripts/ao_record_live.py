"""Record Angel One live quotes into the warehouse (market-data only).

Refuses unless ANGELONE_ALLOW_MARKETDATA=true and --i-allow-network.
Does not place orders.

    python scripts/ao_record_live.py --i-allow-network --minutes 15
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.audit.logger import AuditLog  # noqa: E402
from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.data.store import MarketStore  # noqa: E402
from alpha_desk.ingest.service import IngestionService  # noqa: E402
from alpha_desk.ingest.universe import build_trial_universe  # noqa: E402
from alpha_desk.marketdata.errors import ProviderConfigError  # noqa: E402
from alpha_desk.marketdata.registry import get_provider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--i-allow-network", action="store_true")
    parser.add_argument("--provider", default="angelone")
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--max-events", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--underlyings", default="NIFTY,BANKNIFTY")
    parser.add_argument("--equities", default="RELIANCE,HDFCBANK,TCS")
    parser.add_argument("--commodities", default="", help="comma names when ready (store-only)")
    parser.add_argument("--strikes", type=int, default=3)
    parser.add_argument("--include-options", action="store_true")
    parser.add_argument("--flush-every", type=int, default=100)
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

    root = args.data_root or settings.data_root
    store = MarketStore(root)
    audit = AuditLog(settings.log_root / "audit")

    universe = build_trial_universe(
        provider,
        underlyings=tuple(s.strip().upper() for s in args.underlyings.split(",") if s.strip()),
        equities=tuple(s.strip().upper() for s in args.equities.split(",") if s.strip()),
        expiries_ahead=1,
        strikes_each_side=args.strikes,
        include_options=args.include_options,
        today=date.today(),
    )
    cap = args.limit or provider.config.max_live_symbols
    watchlist = universe.live_watchlist(cap)

    print(f"universe: {json.dumps(universe.summary(), indent=2)}")
    print(f"subscribing {len(watchlist)} symbols (cap {cap})")

    service = IngestionService(provider, store, audit=audit)
    try:
        session = service.record_live(
            watchlist,
            max_events=args.max_events or None,
            max_seconds=args.minutes * 60 if args.minutes else None,
            flush_every=args.flush_every,
        )
    finally:
        provider.close()

    print("\nsession: " + json.dumps(session.as_dict(), indent=2, default=str))
    return 0 if session.ticks or session.bars else 1


if __name__ == "__main__":
    raise SystemExit(main())
