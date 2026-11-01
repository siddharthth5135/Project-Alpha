"""Sync TrueData reference datasets into the warehouse.

Covers endpoints that are not bar/tick streams but that we still need on paid
day without writing new adapters: corporate actions, symbol name changes,
index list/constituents, and bhavcopy (optional date).

    python scripts/td_reference_sync.py
    python scripts/td_reference_sync.py --bhavcopy-segment fo --bhavcopy-days 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

from alpha_desk.audit.logger import AuditEvent, AuditLog  # noqa: E402
from alpha_desk.config.settings import load_settings  # noqa: E402
from alpha_desk.data.store import Dataset, MarketStore  # noqa: E402
from alpha_desk.marketdata.quality import QualityReport, Verdict  # noqa: E402
from alpha_desk.marketdata.registry import get_provider  # noqa: E402
from alpha_desk.vendors.truedata.provider import TrueDataProvider  # noqa: E402


def _accept(rows: int) -> QualityReport:
    report = QualityReport(dataset="reference", contract_id="ref", interval="ref", rows=rows)
    report.verdict = Verdict.ACCEPT
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="truedata")
    parser.add_argument("--equities", default="RELIANCE,HDFCBANK,TCS")
    parser.add_argument("--indices", default="NIFTY 50,NIFTY BANK")
    parser.add_argument("--corp-lookback-days", type=int, default=90)
    parser.add_argument("--bhavcopy-segment", default="", help="e.g. fo or eq; empty = skip")
    parser.add_argument("--bhavcopy-days", type=int, default=3)
    parser.add_argument("--data-root", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings()
    store = MarketStore(args.data_root or settings.data_root)
    audit = AuditLog(settings.log_root / "audit")
    provider = get_provider(args.provider)
    if not isinstance(provider, TrueDataProvider):
        print(f"reference sync expects TrueDataProvider, got {type(provider)}")
        return 2

    rest = provider._rest
    source = "truedata"
    summary: dict[str, object] = {}

    # -- indices master ---------------------------------------------------- #
    try:
        indices = rest.get_indices()
        if not indices.empty:
            store.write_table(
                indices,
                dataset=Dataset.MASTERS,
                parts=(source, "indices"),
                stem=date.today().isoformat(),
                report=_accept(len(indices)),
            )
        summary["indices"] = len(indices)
    except Exception as exc:  # noqa: BLE001
        summary["indices"] = f"error: {exc}"

    for name in [s.strip() for s in args.indices.split(",") if s.strip()]:
        try:
            frame = rest.get_index_components(name)
            if not frame.empty:
                store.write_table(
                    frame,
                    dataset=Dataset.MASTERS,
                    parts=(source, "constituents", name.replace(" ", "_")),
                    stem=date.today().isoformat(),
                    report=_accept(len(frame)),
                )
            summary[f"constituents:{name}"] = len(frame)
        except Exception as exc:  # noqa: BLE001
            summary[f"constituents:{name}"] = f"error: {exc}"

    # -- corporate actions ------------------------------------------------- #
    end = date.today()
    start = end - timedelta(days=max(1, args.corp_lookback_days))
    try:
        corp_range = rest.get_corporate_actions_range(start, end)
        if not corp_range.empty:
            store.write_table(
                corp_range,
                dataset=Dataset.CORPORATE_ACTIONS,
                parts=(source, "range"),
                stem=f"{start.isoformat()}_{end.isoformat()}",
                report=_accept(len(corp_range)),
            )
        summary["corp_actions_range"] = len(corp_range)
    except Exception as exc:  # noqa: BLE001
        summary["corp_actions_range"] = f"error: {exc}"

    for symbol in [s.strip().upper() for s in args.equities.split(",") if s.strip()]:
        try:
            frame = rest.get_corporate_actions(symbol)
            if not frame.empty:
                store.write_table(
                    frame,
                    dataset=Dataset.CORPORATE_ACTIONS,
                    parts=(source, "symbol", symbol),
                    stem=date.today().isoformat(),
                    report=_accept(len(frame)),
                )
            summary[f"corp:{symbol}"] = len(frame)
        except Exception as exc:  # noqa: BLE001
            summary[f"corp:{symbol}"] = f"error: {exc}"

    # -- symbol name changes ----------------------------------------------- #
    try:
        names = rest.get_symbol_name_changes(start, end)
        if not names.empty:
            store.write_table(
                names,
                dataset=Dataset.MASTERS,
                parts=(source, "name_changes"),
                stem=f"{start.isoformat()}_{end.isoformat()}",
                report=_accept(len(names)),
            )
        summary["name_changes"] = len(names)
    except Exception as exc:  # noqa: BLE001
        summary["name_changes"] = f"error: {exc}"

    # -- bhavcopy (optional) ----------------------------------------------- #
    if args.bhavcopy_segment.strip():
        seg = args.bhavcopy_segment.strip().lower()
        counts = []
        for offset in range(args.bhavcopy_days):
            day = end - timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            try:
                frame = rest.get_bhavcopy(seg, day)
                if frame.empty:
                    counts.append({day.isoformat(): 0})
                    continue
                store.write_table(
                    frame,
                    dataset=Dataset.BHAVCOPY,
                    parts=(source, seg, day.isoformat()),
                    stem="bhav",
                    report=_accept(len(frame)),
                )
                counts.append({day.isoformat(): len(frame)})
            except Exception as exc:  # noqa: BLE001
                counts.append({day.isoformat(): f"error: {exc}"})
        summary["bhavcopy"] = counts

    audit.write(AuditEvent.MARKET_DATA, stage="td_reference_sync", summary=summary, provider=args.provider)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
