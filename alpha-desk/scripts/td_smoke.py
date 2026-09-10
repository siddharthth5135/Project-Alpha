"""Credential-day smoke: auth, one history call, one chain call, one live tick.

Stops after a handful of network round-trips. Use this the moment TrueData
credentials land in `.env`. It writes nothing to the warehouse.

    python scripts/td_smoke.py
    python scripts/td_smoke.py --skip-live
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
from alpha_desk.vendors.truedata.provider import TrueDataProvider  # noqa: E402
from alpha_desk.vendors.truedata.stream import EventKind  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=8)
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--underlying", default="NIFTY")
    args = parser.parse_args()

    try:
        provider = TrueDataProvider.from_env()
    except ProviderConfigError as exc:
        print(f"REFUSING: {exc}")
        print("Put TRUEDATA_USER and TRUEDATA_PASSWORD in .env (Market Data API, not Velocity).")
        return 2

    print("account:", json.dumps(provider.config.redacted(), indent=2))

    try:
        provider.rest.tokens.access_token()
        print(f"auth   : ok, token expires in {provider.rest.tokens.token_expires_in:.0f}s")
    except MarketDataError as exc:
        print(f"auth   : FAILED ({type(exc).__name__}: {exc})")
        print("This login is almost certainly Velocity, not Market Data API.")
        return 1

    contract = provider.continuous_future(args.underlying)
    end = pd.Timestamp.now(tz=IST)
    start = end - pd.Timedelta(days=3)
    try:
        bars = provider.fetch_bars(contract, BarInterval.M15, start.to_pydatetime(), end.to_pydatetime())
        print(f"bars   : {len(bars)} rows for {contract.vendor_symbol} 15m last 3 days")
        if bars.empty:
            print("         empty is a plan/entitlement problem, not a plumbing problem.")
            return 1
    except MarketDataError as exc:
        print(f"bars   : FAILED ({type(exc).__name__}: {exc})")
        return 1

    try:
        expiries = provider.fetch_expiries(args.underlying, contract.segment)
        print(f"expiry : {expiries[:4]}")
        if expiries:
            chain = provider.fetch_option_chain(args.underlying, expiries[0], with_greeks=True)
            print(f"chain  : {len(chain)} legs with greeks for {expiries[0]}")
    except MarketDataError as exc:
        print(f"chain  : {type(exc).__name__}: {exc} (may be out of trial scope)")

    if args.skip_live:
        provider.close()
        print("\nREST smoke passed. Run td_probe.py next, then td_backfill.py.")
        return 0

    try:
        provider.subscribe([contract])
        deadline = time.monotonic() + args.seconds
        seen = 0
        for event in provider.stream():
            if event.kind in {EventKind.TICK, EventKind.BAR, EventKind.TOUCHLINE}:
                seen += 1
                print(f"live   : {event.kind.value} latency={event.latency_s}")
            if seen >= 3 or time.monotonic() > deadline:
                break
        print(f"live   : {seen} data events in {args.seconds}s")
        if seen == 0:
            print("         no ticks — check TRUEDATA_WS_PORT (assigned per account).")
            return 1
    except MarketDataError as exc:
        print(f"live   : FAILED ({type(exc).__name__}: {exc})")
        return 1
    finally:
        provider.close()

    print("\nsmoke passed. Next: python scripts/td_probe.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
