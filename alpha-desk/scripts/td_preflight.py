"""Preflight: prove the market-data backend works before credentials exist.

Runs the entire path against the deterministic TrueData doubles: authenticate,
load the symbol master, resolve an option ladder, backfill bars, snapshot a
chain with greeks, capture a live session with a forced disconnect, gate
everything through the quality checks, persist it, and build a supervised
training table from what landed on disk.

If this passes, credential day is configuration only.

    python scripts/td_preflight.py
    python scripts/td_preflight.py --keep --data-root D:/tmp/preflight
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

from alpha_desk.audit.logger import AuditLog  # noqa: E402
from alpha_desk.data.store import Dataset, MarketStore  # noqa: E402
from alpha_desk.features.catalogue import build_feature_set_v0  # noqa: E402
from alpha_desk.ingest.ml_bridge import build_training_table  # noqa: E402
from alpha_desk.ingest.service import IngestionService, summarise  # noqa: E402
from alpha_desk.ingest.universe import build_trial_universe  # noqa: E402
from alpha_desk.labels.definitions import default_label_definition  # noqa: E402
from alpha_desk.marketdata.contracts import IST, BarInterval, Segment  # noqa: E402
from alpha_desk.marketdata.quality import Verdict  # noqa: E402
from alpha_desk.vendors.truedata.mock import (  # noqa: E402
    FakeWsTransport,
    bar_frame,
    build_mock_provider,
    heartbeat_frame,
    market_status_frame,
    touchline_frame,
    trade_frame,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def scripted_ws() -> FakeWsTransport:
    """A live session with a mid-stream drop, so recovery is exercised."""

    def on_subscribe(symbols: list[str]) -> list[str]:
        frames = [touchline_frame(symbols), market_status_frame("OPEN")]
        now = pd.Timestamp.now(tz=IST)
        for index in range(1, 25):
            stamp = (now - pd.Timedelta(seconds=25 - index)).strftime("%Y-%m-%dT%H:%M:%S")
            frames.append(trade_frame("1001", stamp, 24_000 + index * 0.5, sequence=index))
            if index % 5 == 0:
                frames.append(bar_frame("1001", stamp, 24_000 + index * 0.5))
            if index % 8 == 0:
                frames.append(heartbeat_frame())
        return frames

    return FakeWsTransport(on_subscribe=on_subscribe)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="keep the scratch warehouse")
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args()

    root = args.data_root or Path(tempfile.mkdtemp(prefix="alpha-preflight-"))
    store = MarketStore(root / "warehouse")
    audit = AuditLog(root / "audit", run_id="preflight")

    print(f"\nPreflight warehouse: {root}\n")

    print("1. vendor plumbing")
    ws = scripted_ws()
    provider = build_mock_provider(ws=ws, load_master=False)
    loaded = provider.ensure_master()
    check("symbol master loads", sum(loaded.values()) > 0, f"{loaded}")
    check(
        "auth token acquired and reused",
        provider.rest.tokens.token_expires_in is not None,
        f"expires_in={provider.rest.tokens.token_expires_in:.0f}s",
    )

    print("\n2. universe (options -> futures -> equities)")
    universe = build_trial_universe(
        provider, expiries_ahead=1, strikes_each_side=3, today=date(2025, 8, 14)
    )
    check("option ladder resolved", len(universe.options) > 0, f"{len(universe.options)} legs")
    check("continuous future resolved", len(universe.continuous_futures) == 2)
    check("equity leg resolved", len(universe.equities) == 3)
    watchlist = universe.live_watchlist(provider.config.max_live_symbols)
    check(
        "watchlist respects the licensed symbol cap",
        len(watchlist) <= provider.config.max_live_symbols,
        f"{len(watchlist)} of {provider.config.max_live_symbols}",
    )

    print("\n3. historical backfill and quality gate")
    service = IngestionService(provider, store, audit=audit)
    contract = provider.continuous_future("NIFTY")
    start = pd.Timestamp("2025-05-15 09:15", tz=IST).to_pydatetime()
    end = pd.Timestamp("2025-08-14 15:30", tz=IST).to_pydatetime()
    result = service.backfill_bars(contract, BarInterval.M15, start, end)
    check(
        "bars ingested and promoted",
        result.rows > 500 and result.report is not None and result.report.ok,
        f"{result.rows} rows, verdict={result.report.verdict.value if result.report else 'n/a'}",
    )
    check("bars are resumable", service.backfill_bars(contract, BarInterval.M15, start, end).rows == 0)

    equity_results = service.backfill_many(universe.equities, BarInterval.M15, start, end)
    check(
        "equity backfill",
        all(r.error is None for r in equity_results),
        json.dumps(summarise(equity_results)["errors"]),
    )

    print("\n4. options chain with greeks")
    expiry = universe.options[0].expiry if universe.options else date(2025, 8, 28)
    chain = service.snapshot_option_chain("NIFTY", expiry, with_greeks=True)
    check(
        "chain snapshot stored with greeks",
        chain.rows > 0 and chain.ok,
        f"{chain.rows} legs, verdict={chain.report.verdict.value if chain.report else 'n/a'}",
    )

    print("\n5. reference data")
    counts = service.sync_masters([Segment.FUTURES, Segment.OPTIONS, Segment.EQUITY])
    check("instrument master persisted", sum(counts.values()) > 0, f"{counts}")

    print("\n6. live capture, disconnect and recovery")
    provider.subscribe(watchlist[:1])
    ws.drop_after = 6
    session = service.record_live(watchlist[:1], max_events=20, flush_every=5)
    check("ticks captured", session.ticks > 0, f"{session.ticks} ticks")
    check("live bars captured", session.bars > 0, f"{session.bars} bars")
    check("disconnect observed and handled", session.disconnects >= 1)
    check("no unroutable messages", session.unrouted == 0, f"unrouted={session.unrouted}")
    latency = session.stream_stats.get("latency_p95_s")
    check("latency measured", latency is not None, f"p95={latency}")

    print("\n7. storage integrity")
    coverage = store.coverage(Dataset.BARS)
    check("coverage manifest written", not coverage.empty, f"{len(coverage)} partitions")
    log = store.ingest_log()
    check("every write is logged", len(log) >= len(coverage), f"{len(log)} log records")
    ok, message = audit.verify_chain()
    check("audit hash chain intact", ok, message)

    print("\n8. ML handoff")
    try:
        bundle = build_training_table(
            store,
            source="truedata",
            contract=contract,
            interval=BarInterval.M15,
            feature_set=build_feature_set_v0(),
            label_definition=default_label_definition(),
        )
        check(
            "supervised table built from stored bars",
            bundle.report.rows > 100,
            f"{bundle.report.rows} rows, feature_set={bundle.report.feature_set_hash[:12]}",
        )
        check(
            "training data is single-source and single-schema",
            bundle.provenance["source"] == ["truedata"],
            str(bundle.provenance.get("bar_convention")),
        )
    except Exception as exc:  # noqa: BLE001 - preflight reports, never raises
        check("supervised table built from stored bars", False, f"{type(exc).__name__}: {exc}")

    failures = [name for name, ok, _ in CHECKS if not ok]
    print("\n" + "=" * 72)
    print(f"preflight: {len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    if failures:
        print("failed: " + ", ".join(failures))
    else:
        print("backend is ready; supply TRUEDATA_USER / TRUEDATA_PASSWORD and run td_probe.py")
    print("=" * 72)

    if args.json:
        print(json.dumps([{"check": n, "ok": o, "detail": d} for n, o, d in CHECKS], indent=2))

    if not args.keep and args.data_root is None:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print(f"\nartifacts kept at {root}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
