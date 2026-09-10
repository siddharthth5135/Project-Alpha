"""Summarise the TrueData trial into a decision document.

Reads only what was actually stored — the ingest log, partition manifests and
the captured data — and answers the questions that decide whether to buy:
coverage, quality verdicts, observed latency, reconnect behaviour, and whether
history agrees with the live feed for the same minute.

    python scripts/td_trial_report.py
    python scripts/td_trial_report.py --out docs/TRUEDATA_TRIAL_REPORT.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

from alpha_desk.audit.logger import AuditLog  # noqa: E402
from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.data.store import Dataset, MarketStore  # noqa: E402

LIVE_SUFFIX = "_live"


def coverage_section(store: MarketStore) -> tuple[str, dict[str, int]]:
    lines: list[str] = []
    totals: dict[str, int] = {}
    for dataset in Dataset:
        frame = store.coverage(dataset)
        if frame.empty:
            continue
        totals[dataset.value] = int(frame["rows"].sum())
        lines.append(f"\n### {dataset.value}\n")
        lines.append("| partition | files | rows | first | last | verdicts |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in frame.sort_values("partition").itertuples():
            lines.append(
                f"| `{row.partition}` | {row.files} | {row.rows:,} | {row.first_ts} | "
                f"{row.last_ts} | {', '.join(row.verdicts)} |"
            )
    return "\n".join(lines) if lines else "\nNothing stored yet.\n", totals


def quality_section(store: MarketStore) -> str:
    log = store.ingest_log()
    if not log:
        return "\nNo ingest records.\n"

    verdicts = Counter(str(record.get("verdict")) for record in log)
    lines = [f"\nWrites: {len(log)}. Verdicts: {dict(verdicts)}\n"]

    problems: list[str] = []
    for record in log:
        quality = record.get("quality") or {}
        for error in quality.get("errors", []) or []:
            problems.append(f"- **{quality.get('contract_id')}** ({quality.get('interval')}): {error}")
        for warning in quality.get("warnings", []) or []:
            problems.append(f"- {quality.get('contract_id')} ({quality.get('interval')}): {warning}")

    if problems:
        lines.append("\n#### Quality findings\n")
        lines.extend(sorted(set(problems))[:60])
    else:
        lines.append("\nNo quality errors or warnings recorded.\n")

    missing = [
        (record.get("quality") or {}).get("missing_pct", 0.0)
        for record in log
        if record.get("quality")
    ]
    if missing:
        series = pd.Series(missing, dtype=float)
        lines.append(
            f"\nMissing-bar percentage across partitions: median {series.median():.3f}%, "
            f"worst {series.max():.3f}%\n"
        )
    return "\n".join(lines)


def live_section(audit: AuditLog) -> str:
    records = [
        record
        for record in audit.read_all()
        if record.get("event") == "MARKET_DATA" and record["payload"].get("stage") == "record_live"
    ]
    if not records:
        return "\nNo live capture sessions recorded.\n"

    lines = ["\n| session | ticks | bars | greeks | disconnects | p50 s | p95 s | max s | gaps | unrouted |"]
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for index, record in enumerate(records, start=1):
        payload = record["payload"]
        stream = payload.get("stream") or {}
        lines.append(
            f"| {index} | {payload.get('ticks')} | {payload.get('bars')} | {payload.get('greeks')} | "
            f"{payload.get('disconnects')} | {stream.get('latency_p50_s')} | "
            f"{stream.get('latency_p95_s')} | {stream.get('latency_max_s')} | "
            f"{stream.get('sequence_gaps')} | {json.dumps(stream.get('unrouted') or {})} |"
        )
    return "\n".join(lines)


def parity_section(store: MarketStore, interval: str = "1m") -> str:
    base = store.root / Dataset.BARS.value
    if not base.exists():
        return "\nNo bars stored.\n"

    sources = [path.name for path in base.iterdir() if path.is_dir()]
    live_sources = [s for s in sources if s.endswith(LIVE_SUFFIX)]
    if not live_sources:
        return "\nNo live bars captured, so historical/live parity was not measured.\n"

    lines = ["\n| contract | matched minutes | max |close diff| | mean |close diff| |"]
    lines.append("| --- | --- | --- | --- |")
    measured = False
    for live_source in live_sources:
        hist_source = live_source[: -len(LIVE_SUFFIX)]
        for contract_dir in sorted((base / live_source).iterdir()):
            key = contract_dir.name
            live = store.read_bars(source=live_source, contract=key, interval=interval)
            hist = store.read_bars(source=hist_source, contract=key, interval=interval)
            if live.empty or hist.empty:
                continue
            merged = live[["ts", "close"]].merge(
                hist[["ts", "close"]], on="ts", suffixes=("_live", "_hist")
            )
            if merged.empty:
                continue
            measured = True
            diff = (merged["close_live"] - merged["close_hist"]).abs()
            lines.append(
                f"| `{key}` | {len(merged)} | {diff.max():.4f} | {diff.mean():.4f} |"
            )
    if not measured:
        return (
            "\nLive and historical bars exist but share no timestamps yet; "
            "re-run the 1m backfill after a live session to measure parity.\n"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "TRUEDATA_TRIAL_REPORT.md")
    args = parser.parse_args()

    settings = load_settings()
    store = MarketStore(args.data_root or settings.data_root)
    audit = AuditLog(settings.log_root / "audit")
    chain_ok, chain_message = audit.verify_chain()

    coverage, totals = coverage_section(store)
    document = f"""# TrueData trial report

Generated: {pd.Timestamp.now(tz="Asia/Kolkata").isoformat()}
Warehouse: `{store.root}`
Audit chain: {"intact" if chain_ok else f"BROKEN - {chain_message}"}

Rows stored by dataset: {json.dumps(totals)}

## 1. Coverage
{coverage}

## 2. Data quality
{quality_section(store)}

## 3. Live feed stability and latency
{live_section(audit)}

## 4. Historical vs live parity (1m close)
{parity_section(store)}

## 5. Decision checklist

| Question | Evidence to check above |
| --- | --- |
| Is history deep enough for training? | Section 1 first/last per partition |
| Is history clean enough to promote? | Section 2 verdicts and missing-bar percentages |
| Is the live feed stable? | Section 3 disconnects, sequence gaps, unrouted keys |
| Is latency acceptable for 15m decisions? | Section 3 p95 latency |
| Do history and live agree? | Section 4 close differences |
| Are options, futures and equities all covered? | Section 1 partitions per segment |
"""

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(document, encoding="utf-8")
    print(document)
    print(f"\nwrote {args.out}")
    return 0 if chain_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
