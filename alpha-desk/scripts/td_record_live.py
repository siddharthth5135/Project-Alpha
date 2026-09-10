"""Record the live feed into the warehouse.

Subscribes the priority watchlist (options, then futures, then cash), persists
every tick, one-minute bar and greek to versioned parquet, and prints the
stability evidence the trial needs: latency percentiles, reconnect count,
sequence gaps and unrouted message keys.

Safe to stop with Ctrl-C; buffers are flushed on the way out.

    python scripts/td_record_live.py --minutes 15
    python scripts/td_record_live.py --provider truedata_mock --max-events 50
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
from alpha_desk.marketdata.registry import get_provider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="truedata")
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--max-events", type=int, default=0, help="0 = unbounded")
    parser.add_argument("--limit", type=int, default=0, help="cap subscribed symbols (0 = plan cap)")
    parser.add_argument("--underlyings", default="NIFTY,BANKNIFTY")
    parser.add_argument("--equities", default="RELIANCE")
    parser.add_argument("--strikes", type=int, default=5)
    parser.add_argument("--flush-every", type=int, default=500)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--capture-raw", action="store_true", help="also append raw ws frames")
    args = parser.parse_args()

    settings = load_settings()
    root = args.data_root or settings.data_root
    store = MarketStore(root)
    audit = AuditLog(settings.log_root / "audit")

    provider = get_provider(args.provider)
    if args.capture_raw:
        provider.config.raw_capture_dir = settings.log_root / "truedata_raw"

    universe = build_trial_universe(
        provider,
        underlyings=tuple(s.strip().upper() for s in args.underlyings.split(",") if s.strip()),
        equities=tuple(s.strip().upper() for s in args.equities.split(",") if s.strip()),
        expiries_ahead=1,
        strikes_each_side=args.strikes,
        today=date.today(),
    )
    cap = args.limit or provider.config.max_live_symbols
    watchlist = universe.live_watchlist(cap)

    print(f"universe: {json.dumps(universe.summary(), indent=2)}")
    print(f"subscribing {len(watchlist)} symbols (cap {cap})")
    for contract in watchlist[:20]:
        print(f"  {contract.vendor_symbol}")
    if len(watchlist) > 20:
        print(f"  ... and {len(watchlist) - 20} more")

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
    stats = session.stream_stats
    print(
        "\nlatency p50/p95/max (s): "
        f"{stats.get('latency_p50_s')} / {stats.get('latency_p95_s')} / {stats.get('latency_max_s')}"
    )
    if stats.get("unrouted"):
        print(f"UNROUTED MESSAGE KEYS (adapter layout needs review): {stats['unrouted']}")
    if stats.get("sequence_gaps"):
        print(f"sequence gaps observed: {stats['sequence_gaps']}")
    if stats.get("reconnects"):
        print(f"reconnects: {stats['reconnects']} (stale timeouts: {stats.get('stale_timeouts')})")

    return 0 if session.ticks or session.bars else 1


if __name__ == "__main__":
    raise SystemExit(main())
