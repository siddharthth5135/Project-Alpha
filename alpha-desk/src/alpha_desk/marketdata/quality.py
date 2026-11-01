"""Data-quality gates for ingested market data.

A backfill that "worked" is not the same as a backfill that is safe to train
on. Every frame gets a verdict: ACCEPT, ACCEPT_WITH_WARNINGS, or QUARANTINE.
Only ACCEPT* frames land in the promoted store, and the report is persisted next
to the data so a model card can point at the exact quality evidence for the
dataset it was trained on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import pandas as pd

from alpha_desk.marketdata.calendar import MarketCalendar
from alpha_desk.marketdata.contracts import BarInterval, Contract, Segment


class Verdict(str, Enum):
    ACCEPT = "accept"
    ACCEPT_WITH_WARNINGS = "accept_with_warnings"
    QUARANTINE = "quarantine"


@dataclass
class QualityThresholds:
    """Tolerances. Tight by default; loosened only per-dataset and in writing."""

    max_missing_bar_pct: float = 0.35
    max_missing_bar_pct_warn: float = 0.03
    max_duplicate_pct: float = 0.0
    max_rejected_pct: float = 0.35
    max_flat_bar_pct: float = 50.0
    max_zero_volume_pct: float = 15.0
    max_gap_run_bars: int = 10
    require_verified_calendar: bool = False


@dataclass
class QualityReport:
    dataset: str
    contract_id: str
    interval: str
    rows: int
    verdict: Verdict = Verdict.ACCEPT
    first_ts: pd.Timestamp | None = None
    last_ts: pd.Timestamp | None = None
    expected_rows: int | None = None
    missing_rows: int = 0
    missing_pct: float = 0.0
    longest_gap_run: int = 0
    duplicate_rows: int = 0
    out_of_order_rows: int = 0
    rejected_rows: int = 0
    zero_volume_pct: float = 0.0
    flat_bar_pct: float = 0.0
    unverified_days: list[str] = field(default_factory=list)
    missing_sessions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict is not Verdict.QUARANTINE

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "contract_id": self.contract_id,
            "interval": self.interval,
            "rows": self.rows,
            "verdict": self.verdict.value,
            "first_ts": None if self.first_ts is None else str(self.first_ts),
            "last_ts": None if self.last_ts is None else str(self.last_ts),
            "expected_rows": self.expected_rows,
            "missing_rows": self.missing_rows,
            "missing_pct": round(self.missing_pct, 4),
            "longest_gap_run": self.longest_gap_run,
            "duplicate_rows": self.duplicate_rows,
            "out_of_order_rows": self.out_of_order_rows,
            "rejected_rows": self.rejected_rows,
            "zero_volume_pct": round(self.zero_volume_pct, 3),
            "flat_bar_pct": round(self.flat_bar_pct, 3),
            "unverified_days": self.unverified_days,
            "missing_sessions": self.missing_sessions,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def assess_bars(
    frame: pd.DataFrame,
    *,
    contract: Contract,
    interval: BarInterval,
    calendar: MarketCalendar,
    rejected_rows: int = 0,
    thresholds: QualityThresholds | None = None,
    expected_start: pd.Timestamp | None = None,
    expected_end: pd.Timestamp | None = None,
) -> QualityReport:
    """Score a normalised bar frame against the exchange calendar."""
    limits = thresholds or QualityThresholds()
    report = QualityReport(
        dataset="bars",
        contract_id=contract.contract_id,
        interval=interval.value,
        rows=len(frame),
        rejected_rows=rejected_rows,
    )

    if frame.empty:
        report.errors.append("no rows after normalisation")
        report.verdict = Verdict.QUARANTINE
        return report

    ts = pd.to_datetime(frame["ts"])
    report.first_ts = ts.iloc[0]
    report.last_ts = ts.iloc[-1]
    report.duplicate_rows = int(ts.duplicated().sum())
    report.out_of_order_rows = int((ts.diff().dropna() <= pd.Timedelta(0)).sum())

    total = len(frame)
    if rejected_rows:
        rejected_pct = 100.0 * rejected_rows / (total + rejected_rows)
        if rejected_pct > limits.max_rejected_pct:
            report.errors.append(f"{rejected_pct:.2f}% of rows rejected by normalisation")
        else:
            report.warnings.append(f"{rejected_rows} rows rejected by normalisation")

    if "volume" in frame.columns:
        report.zero_volume_pct = 100.0 * float((frame["volume"].fillna(0) <= 0).mean())
    flat = (frame["high"] == frame["low"]) & (frame["open"] == frame["close"])
    report.flat_bar_pct = 100.0 * float(flat.mean())

    # Cash indices often publish EOD with zero volume; that is not a data defect.
    effective_limits = limits
    if contract.segment is Segment.INDEX and limits.max_zero_volume_pct < 100.0:
        effective_limits = QualityThresholds(
            max_missing_bar_pct=limits.max_missing_bar_pct,
            max_missing_bar_pct_warn=limits.max_missing_bar_pct_warn,
            max_duplicate_pct=limits.max_duplicate_pct,
            max_rejected_pct=limits.max_rejected_pct,
            max_flat_bar_pct=max(limits.max_flat_bar_pct, 100.0),
            max_zero_volume_pct=100.0,
            max_gap_run_bars=limits.max_gap_run_bars,
            require_verified_calendar=limits.require_verified_calendar,
        )

    segment = _gap_segment(contract)
    if interval is not BarInterval.TICK:
        window_start = expected_start or report.first_ts
        window_end = expected_end or report.last_ts
        expected = calendar.expected_bar_index(segment, interval, window_start, window_end)
        report.expected_rows = len(expected)
        if len(expected):
            present = pd.DatetimeIndex(ts.unique()).tz_convert(expected.tz)
            missing = expected.difference(present)
            report.missing_rows = len(missing)
            report.missing_pct = 100.0 * len(missing) / len(expected)
            report.longest_gap_run = _longest_run(expected, missing)
            report.missing_sessions = _missing_sessions(missing, present)
            report.unverified_days = [
                d.isoformat()
                for d in sorted({t.date() for t in missing})
                if not calendar.is_verified(d)
            ]

    _apply_verdict(report, effective_limits)
    return report


def assess_option_chain(
    frame: pd.DataFrame,
    *,
    underlying: str,
    expiry: date,
    rejected_rows: int = 0,
    min_strikes: int = 10,
) -> QualityReport:
    """Chains are judged on strike coverage and put/call symmetry."""
    report = QualityReport(
        dataset="option_chain",
        contract_id=f"{underlying}:{expiry.isoformat()}",
        interval="snapshot",
        rows=len(frame),
        rejected_rows=rejected_rows,
    )
    if frame.empty:
        report.errors.append("empty chain")
        report.verdict = Verdict.QUARANTINE
        return report

    strikes = frame["strike"].nunique()
    if strikes < min_strikes:
        report.errors.append(f"only {strikes} strikes returned (min {min_strikes})")

    by_right = frame.groupby("right")["strike"].nunique().to_dict()
    calls, puts = by_right.get("CE", 0), by_right.get("PE", 0)
    if calls != puts:
        report.warnings.append(f"asymmetric chain: {calls} call strikes vs {puts} put strikes")
    if int(frame["oi"].sum()) == 0:
        report.warnings.append("chain has zero total open interest")
    if frame.duplicated(subset=["expiry", "strike", "right"]).any():
        report.errors.append("duplicate strike/right rows in one snapshot")

    ts = pd.to_datetime(frame["ts"])
    report.first_ts, report.last_ts = ts.min(), ts.max()
    if (report.last_ts - report.first_ts) > pd.Timedelta(minutes=5):
        report.warnings.append("snapshot timestamps span more than 5 minutes")

    _apply_verdict(report, QualityThresholds())
    return report


def _apply_verdict(report: QualityReport, limits: QualityThresholds) -> None:
    if report.duplicate_rows > 0:
        report.errors.append(f"{report.duplicate_rows} duplicate timestamps survived normalisation")
    if report.out_of_order_rows > 0:
        report.errors.append(f"{report.out_of_order_rows} out-of-order rows")
    if report.missing_pct > limits.max_missing_bar_pct:
        report.errors.append(f"{report.missing_pct:.2f}% of expected bars missing")
    elif report.missing_pct > limits.max_missing_bar_pct_warn:
        report.warnings.append(f"{report.missing_pct:.2f}% of expected bars missing")
    if report.longest_gap_run > limits.max_gap_run_bars:
        report.errors.append(f"contiguous gap of {report.longest_gap_run} bars")
    if report.missing_sessions:
        # One-off adhoc closures (state elections, etc.) should not nuke a multi-year
        # EOD series when overall missing% is still within tolerance.
        message = f"whole sessions missing: {report.missing_sessions[:5]}"
        if report.dataset == "bars" and report.interval in {"1d", "eod"} and report.missing_pct <= limits.max_missing_bar_pct:
            report.warnings.append(message)
        else:
            report.errors.append(message)
    if report.flat_bar_pct > limits.max_flat_bar_pct:
        report.warnings.append(f"{report.flat_bar_pct:.1f}% of bars are flat (illiquid series?)")
    if report.zero_volume_pct > limits.max_zero_volume_pct:
        report.warnings.append(f"{report.zero_volume_pct:.1f}% of bars have zero volume")
    if report.unverified_days:
        message = f"gaps on dates outside the verified holiday calendar: {report.unverified_days[:5]}"
        if limits.require_verified_calendar:
            report.errors.append(message)
        else:
            report.warnings.append(message)

    if report.errors:
        report.verdict = Verdict.QUARANTINE
    elif report.warnings:
        report.verdict = Verdict.ACCEPT_WITH_WARNINGS
    else:
        report.verdict = Verdict.ACCEPT


def _gap_segment(contract: Contract) -> Segment:
    return contract.segment


def _longest_run(expected: pd.DatetimeIndex, missing: pd.DatetimeIndex) -> int:
    if len(missing) == 0:
        return 0
    positions = pd.Index(expected).get_indexer(missing)
    longest = run = 1
    for prev, cur in zip(positions, positions[1:]):
        run = run + 1 if cur == prev + 1 else 1
        longest = max(longest, run)
    return longest


def _missing_sessions(missing: pd.DatetimeIndex, present: pd.DatetimeIndex) -> list[str]:
    if len(missing) == 0:
        return []
    present_days = {t.date() for t in present}
    return sorted({t.date().isoformat() for t in missing if t.date() not in present_days})
