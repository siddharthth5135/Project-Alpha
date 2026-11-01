"""Copy MarketStore bars into ParquetWarehouse layout for paper path.

Paper uses warehouse paths bars/<source>/<symbol>/<tf>/.
Vendor capture lands in MarketStore (bars/truedata/NSE__FUT__...).

    python scripts/sync_store_to_warehouse.py --source truedata --contract-id NSE:FUT:NIFTY:CONT-vendor_spliced --symbol NIFTY
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.data.store import MarketStore  # noqa: E402
from alpha_desk.data.warehouse import ParquetWarehouse  # noqa: E402
from alpha_desk.ingest.ml_bridge import load_promoted_bars  # noqa: E402
from alpha_desk.marketdata.contracts import BarInterval, contract_from_id  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default="truedata")
    p.add_argument("--contract-id", default="NSE:FUT:NIFTY:CONT-vendor_spliced")
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--interval", default="15m")
    args = p.parse_args()

    settings = load_settings()
    store = MarketStore(settings.data_root)
    warehouse = ParquetWarehouse(settings.data_root)
    contract = contract_from_id(args.contract_id)
    interval = BarInterval(args.interval)

    try:
        frame, _prov = load_promoted_bars(
            store, source=args.source, contract=contract, interval=interval
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: cannot load store bars: {exc}")
        return 1

    if frame is None or frame.empty:
        print(f"FAIL: no bars in store for {args.source} {args.contract_id} {args.interval}")
        return 1

    # Warehouse expects ts, open, high, low, close, volume (+ optional oi)
    cols = [c for c in ["ts", "open", "high", "low", "close", "volume", "oi"] if c in frame.columns]
    out = frame[cols].copy()
    report = warehouse.write_bars(args.source, args.symbol, args.interval, out)
    print(
        f"OK: wrote {len(out)} bars -> bars/{args.source}/{args.symbol}/{args.interval}/ "
        f"quality={report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
