"""Angel One market-data smoke (refuses network unless explicitly enabled).

Does NOT place orders. Does NOT call SmartAPI unless:
  ANGELONE_ALLOW_MARKETDATA=true
and you pass --i-allow-network.

    python scripts/ao_smoke.py
    python scripts/ao_smoke.py --i-allow-network --skip-live
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

from alpha_desk.marketdata.contracts import IST, BarInterval  # noqa: E402
from alpha_desk.marketdata.errors import MarketDataError, ProviderConfigError  # noqa: E402
from alpha_desk.marketdata.events import EventKind  # noqa: E402
from alpha_desk.vendors.angelone.provider import AngelOneProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--i-allow-network",
        action="store_true",
        help="required together with ANGELONE_ALLOW_MARKETDATA=true",
    )
    parser.add_argument("--seconds", type=int, default=8)
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--underlying", default="NIFTY")
    args = parser.parse_args()

    if not args.i_allow_network:
        print("REFUSING: pass --i-allow-network only after you explicitly approve a session.")
        print("Also set ANGELONE_ALLOW_MARKETDATA=true in local .env (never commit).")
        print("No Angel API call was made.")
        return 2

    try:
        provider = AngelOneProvider.from_env()
    except ProviderConfigError as exc:
        print(f"REFUSING: {exc}")
        return 2

    if not provider.config.allow_marketdata:
        print("REFUSING: ANGELONE_ALLOW_MARKETDATA is not true.")
        print("No Angel API call was made.")
        return 2

    print("account:", json.dumps(provider.config.redacted(), indent=2))

    try:
        provider.rest.session.login()
        print("auth   : ok")
    except MarketDataError as exc:
        print(f"auth   : FAILED ({type(exc).__name__}: {exc})")
        return 1

    try:
        counts = provider.ensure_master()
        print(f"master : {counts}")
        contract = provider.continuous_future(args.underlying)
        end = pd.Timestamp.now(tz=IST)
        start = end - pd.Timedelta(days=3)
        bars = provider.fetch_bars(
            contract, BarInterval.M15, start.to_pydatetime(), end.to_pydatetime()
        )
        print(f"bars   : {len(bars)} rows for {contract.vendor_symbol} 15m")
        if bars.empty:
            return 1
    except MarketDataError as exc:
        print(f"bars   : FAILED ({type(exc).__name__}: {exc})")
        return 1

    if args.skip_live:
        provider.close()
        print("\nREST smoke passed.")
        return 0

    try:
        provider.subscribe([contract])
        deadline = time.monotonic() + args.seconds
        seen = 0
        for event in provider.stream():
            if event.kind is EventKind.TICK:
                seen += 1
                print(f"live   : tick latency={event.latency_s}")
            if seen >= 3 or time.monotonic() > deadline:
                break
        print(f"live   : {seen} ticks in {args.seconds}s")
        if seen == 0:
            return 1
    except MarketDataError as exc:
        print(f"live   : FAILED ({type(exc).__name__}: {exc})")
        return 1
    finally:
        provider.close()

    print("\nsmoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
