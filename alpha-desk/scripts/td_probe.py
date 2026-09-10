"""Day-one TrueData probe: verify the account, measure what it actually gives us.

Read-only. Nothing is written to the warehouse. It answers the questions that
decide the rest of the trial:

* Do the credentials work, and on which websocket port?
* What does the symbol master really look like (so symbology is verified, not
  assumed)?
* How deep does history actually go per interval, versus what the plan claims?
* Are ticks, option chains and greeks entitled?
* What do live frames look like, and what is the observed latency?

Raw payloads are captured to the output directory so any wire-format difference
can be diffed against the layouts in the adapter instead of guessed at.

    python scripts/td_probe.py
    python scripts/td_probe.py --skip-live --out artifacts/truedata_probe
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

from alpha_desk.marketdata.contracts import IST, BarInterval, Contract, Segment  # noqa: E402
from alpha_desk.marketdata.errors import MarketDataError, NotEntitledError  # noqa: E402
from alpha_desk.vendors.truedata.provider import VENDOR_SEGMENTS, TrueDataProvider  # noqa: E402
from alpha_desk.vendors.truedata.stream import EventKind  # noqa: E402

DEPTH_PROBES_DAYS = (30, 90, 183, 365, 730, 1825, 3650)


def _print(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def probe_depth(provider: TrueDataProvider, contract: Contract, interval: BarInterval) -> dict:
    """Find the oldest date that still returns data, one cheap probe per step."""
    now = pd.Timestamp.now(tz=IST)
    deepest = None
    refused = None
    for days in DEPTH_PROBES_DAYS:
        anchor = now - pd.Timedelta(days=days)
        start = anchor.normalize() + pd.Timedelta(hours=9, minutes=15)
        end = anchor.normalize() + pd.Timedelta(hours=15, minutes=30)
        try:
            frame = provider.fetch_bars(contract, interval, start.to_pydatetime(), end.to_pydatetime())
        except NotEntitledError as exc:
            refused = f"{days}d: {exc}"
            break
        except MarketDataError as exc:
            refused = f"{days}d: {type(exc).__name__} {exc}"
            continue
        if frame.empty:
            continue
        deepest = days
    return {"deepest_days_with_data": deepest, "first_refusal": refused}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO / "artifacts" / "truedata_probe")
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--equity", default="RELIANCE")
    parser.add_argument("--seconds", type=int, default=30, help="live capture duration")
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--skip-depth", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    findings: dict[str, object] = {"probed_at": datetime.now().isoformat()}

    provider = TrueDataProvider.from_env()
    provider.config.raw_capture_dir = args.out
    _print("account")
    print(json.dumps(provider.config.redacted(), indent=2))
    findings["config"] = provider.config.redacted()

    _print("authentication")
    token_ttl = None
    try:
        provider.rest.tokens.access_token()
        token_ttl = provider.rest.tokens.token_expires_in
        print(f"  token acquired, expires in {token_ttl:.0f}s")
    except MarketDataError as exc:
        print(f"  FAILED: {exc}")
        findings["auth"] = str(exc)
        (args.out / "probe.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
        return 1
    findings["auth"] = {"ok": True, "expires_in_s": token_ttl}

    _print("symbol master")
    master: dict[str, object] = {}
    for segment in (Segment.INDEX, Segment.EQUITY, Segment.FUTURES):
        for code in VENDOR_SEGMENTS[segment]:
            try:
                frame = provider.rest.get_all_symbols(code)
            except MarketDataError as exc:
                master[code] = f"{type(exc).__name__}: {exc}"
                continue
            master[code] = {"rows": len(frame), "columns": [str(c) for c in frame.columns][:12]}
            (args.out / f"master_{code}.csv").write_text(
                frame.head(50).to_csv(index=False), encoding="utf-8"
            )
            print(f"  {code}: {len(frame)} rows, columns={list(frame.columns)[:8]}")
            break
    findings["symbol_master"] = master

    loaded = provider.ensure_master()
    print(f"  indexed into symbology: {loaded}")
    findings["symbology_loaded"] = loaded

    _print("expiries")
    expiries: list[date] = []
    try:
        expiries = provider.fetch_expiries(args.underlying, Segment.OPTIONS)
        print(f"  {args.underlying}: {[e.isoformat() for e in expiries[:8]]}")
    except MarketDataError as exc:
        print(f"  FAILED: {exc}")
    findings["expiries"] = [e.isoformat() for e in expiries[:12]]

    _print("recent history")
    continuous = provider.continuous_future(args.underlying)
    end = pd.Timestamp.now(tz=IST)
    start = end - pd.Timedelta(days=5)
    history: dict[str, object] = {}
    for interval in (BarInterval.M1, BarInterval.M15, BarInterval.EOD):
        try:
            outcome = provider.fetch_bars_detailed(
                continuous, interval, start.to_pydatetime(), end.to_pydatetime()
            )
        except MarketDataError as exc:
            history[interval.value] = f"{type(exc).__name__}: {exc}"
            print(f"  {interval.value}: {type(exc).__name__}: {exc}")
            continue
        frame = outcome.frame
        history[interval.value] = {
            "rows": len(frame),
            "first_ts": str(frame["ts"].iloc[0]) if len(frame) else None,
            "last_ts": str(frame["ts"].iloc[-1]) if len(frame) else None,
            "requests": outcome.requests,
            "notes": outcome.notes,
        }
        print(f"  {interval.value}: {len(frame)} rows, last={history[interval.value]['last_ts']}")
        if len(frame):
            frame.head(5).to_csv(args.out / f"bars_{interval.value}.csv", index=False)
    findings["recent_history"] = history

    if not args.skip_depth:
        _print("history depth (per interval, one probe per step)")
        depth = {}
        for interval in (BarInterval.M1, BarInterval.EOD):
            depth[interval.value] = probe_depth(provider, continuous, interval)
            print(f"  {interval.value}: {depth[interval.value]}")
        findings["history_depth"] = depth

    _print("ticks")
    try:
        ticks = provider.fetch_ticks(
            continuous,
            (end - pd.Timedelta(days=1)).normalize().to_pydatetime(),
            (end - pd.Timedelta(days=1)).normalize().to_pydatetime() + pd.Timedelta(hours=10),
        )
        findings["ticks"] = {"rows": len(ticks)}
        print(f"  {len(ticks)} tick rows")
        if len(ticks):
            ticks.head(5).to_csv(args.out / "ticks.csv", index=False)
    except MarketDataError as exc:
        findings["ticks"] = f"{type(exc).__name__}: {exc}"
        print(f"  {type(exc).__name__}: {exc}")

    _print("option chain")
    chain_findings: dict[str, object] = {}
    if expiries:
        for with_greeks in (False, True):
            key = "with_greeks" if with_greeks else "plain"
            try:
                outcome = provider.fetch_option_chain_detailed(
                    args.underlying, expiries[0], with_greeks=with_greeks
                )
                frame = outcome.frame
                chain_findings[key] = {
                    "rows": len(frame),
                    "strikes": int(frame["strike"].nunique()) if len(frame) else 0,
                    "has_iv": bool(len(frame) and frame["iv"].gt(0).any()),
                    "has_oi": bool(len(frame) and frame["oi"].gt(0).any()),
                }
                print(f"  {key}: {chain_findings[key]}")
                if len(frame):
                    frame.head(10).to_csv(args.out / f"chain_{key}.csv", index=False)
            except MarketDataError as exc:
                chain_findings[key] = f"{type(exc).__name__}: {exc}"
                print(f"  {key}: {type(exc).__name__}: {exc}")
    findings["option_chain"] = chain_findings

    _print("equity reference")
    reference: dict[str, object] = {}
    for name, call in (
        ("corporate_actions", lambda: provider.corporate_actions(args.equity)),
        ("index_constituents", lambda: provider.index_constituents("NIFTY 50")),
    ):
        try:
            frame = call()
            reference[name] = {"rows": len(frame), "columns": [str(c) for c in frame.columns][:10]}
            print(f"  {name}: {len(frame)} rows")
        except MarketDataError as exc:
            reference[name] = f"{type(exc).__name__}: {exc}"
            print(f"  {name}: {type(exc).__name__}: {exc}")
    findings["reference"] = reference

    if not args.skip_live:
        _print(f"live feed ({args.seconds}s)")
        watchlist = [provider.spot_contract(args.underlying), continuous]
        try:
            provider.subscribe(watchlist)
            deadline = time.monotonic() + args.seconds
            kinds: dict[str, int] = {}
            for event in provider.stream():
                kinds[event.kind.value] = kinds.get(event.kind.value, 0) + 1
                if event.kind is EventKind.TICK:
                    print(
                        f"  tick {event.payload.contract_id} ltp={event.payload.ltp} "
                        f"latency={event.latency_s:.2f}s"
                    )
                if time.monotonic() > deadline:
                    break
            stats = provider.stream_client.stats.as_dict()
            findings["live"] = {"kinds": kinds, "stats": stats}
            print(f"  message kinds: {kinds}")
            print(f"  stats: {json.dumps(stats)}")
        except MarketDataError as exc:
            findings["live"] = f"{type(exc).__name__}: {exc}"
            print(f"  {type(exc).__name__}: {exc}")
        finally:
            provider.close()

    findings["rest_calls"] = [
        {"endpoint": c.endpoint, "status": c.status, "rows": c.rows, "elapsed_s": round(c.elapsed_s, 3)}
        for c in provider.rest.calls
    ]
    (args.out / "probe.json").write_text(
        json.dumps(findings, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nwrote {args.out / 'probe.json'} (raw payload samples alongside it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
