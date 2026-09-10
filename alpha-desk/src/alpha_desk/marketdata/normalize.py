"""Vendor payload -> canonical frame.

Two rules drive everything here. First, nothing is dropped silently: rows that
fail an invariant are moved to a ``rejected`` frame with a reason so they can be
quarantined and audited instead of vanishing. Second, provenance is attached at
normalisation time, not at write time, so a frame can never reach storage
without knowing where it came from and under which schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from alpha_desk.marketdata.contracts import (
    BAR_COLUMNS_EXT,
    CHAIN_COLUMNS,
    IST,
    MASTER_COLUMNS,
    PROVENANCE_COLUMNS,
    TICK_COLUMNS,
    BarInterval,
    Contract,
    OptionRight,
    Provenance,
)
from alpha_desk.marketdata.errors import DataQualityError


@dataclass
class NormalizedFrame:
    """Accepted rows plus everything that failed, with reasons."""

    rows: pd.DataFrame
    rejected: pd.DataFrame
    notes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> int:
        return len(self.rows)

    @property
    def dropped(self) -> int:
        return len(self.rejected)

    def raise_if_all_rejected(self, context: str) -> None:
        if self.dropped and self.accepted == 0:
            reasons = sorted(set(self.rejected["reject_reason"]))
            raise DataQualityError(f"{context}: every row rejected ({reasons})")


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def attach_provenance(frame: pd.DataFrame, provenance: Provenance) -> pd.DataFrame:
    out = frame.copy()
    meta = provenance.as_dict()
    if meta["ingested_at"] is None:
        meta["ingested_at"] = now_utc()
    for column in PROVENANCE_COLUMNS:
        out[column] = meta[column]
    return out


def to_ist(series: pd.Series, source_tz: str = IST) -> pd.Series:
    """Parse timestamps into tz-aware IST.

    TrueData returns naive IST strings. Naive input is localised, aware input is
    converted, and a mix of both is handled row-wise rather than raising, which
    is what ``pd.to_datetime`` does when a vendor changes format mid-file.
    """
    parsed = pd.to_datetime(series, errors="coerce", format="mixed", utc=False)
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_convert(IST)
    if parsed.isna().all():
        return parsed
    try:
        return parsed.dt.tz_localize(source_tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert(
            IST
        )
    except (TypeError, AttributeError):
        return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert(IST)


def _numeric(frame: pd.DataFrame, columns: list[str], default: float | None = None) -> None:
    for column in columns:
        if column not in frame.columns:
            frame[column] = default
        frame[column] = pd.to_numeric(frame[column], errors="coerce")


def _split(frame: pd.DataFrame, bad: pd.Series, reason: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    bad = bad.fillna(True)
    rejected = frame[bad].copy()
    if not rejected.empty:
        rejected["reject_reason"] = reason
    return frame[~bad].copy(), rejected


def _merge_rejects(rejects: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in rejects if not frame.empty]
    if not usable:
        return pd.DataFrame()
    return pd.concat(usable, ignore_index=True)


def normalize_bars(
    raw: pd.DataFrame,
    *,
    contract: Contract,
    interval: BarInterval,
    provenance: Provenance,
) -> NormalizedFrame:
    """Canonical OHLCV(+OI) frame for one contract and interval."""
    frame = raw.copy()
    rejects: list[pd.DataFrame] = []
    notes: list[str] = []

    if frame.empty:
        empty = pd.DataFrame(columns=[*BAR_COLUMNS_EXT, "contract_id", *PROVENANCE_COLUMNS])
        return NormalizedFrame(rows=empty, rejected=pd.DataFrame(), notes=["empty payload"])

    if "ts" not in frame.columns:
        raise DataQualityError(f"{contract.contract_id}: payload has no ts column")

    frame["ts"] = to_ist(frame["ts"])
    _numeric(frame, ["open", "high", "low", "close"])
    _numeric(frame, ["volume", "oi"], default=0.0)

    frame, bad = _split(frame, frame["ts"].isna(), "unparseable timestamp")
    rejects.append(bad)

    frame, bad = _split(
        frame,
        frame[["open", "high", "low", "close"]].isna().any(axis=1),
        "missing ohlc",
    )
    rejects.append(bad)

    frame, bad = _split(
        frame,
        (frame[["open", "high", "low", "close"]] <= 0).any(axis=1),
        "non-positive price",
    )
    rejects.append(bad)

    frame, bad = _split(
        frame,
        (frame["high"] < frame["low"])
        | (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1)),
        "inconsistent ohlc",
    )
    rejects.append(bad)

    frame, bad = _split(frame, frame["volume"] < 0, "negative volume")
    rejects.append(bad)

    duplicates = int(frame["ts"].duplicated().sum())
    if duplicates:
        notes.append(f"{duplicates} duplicate timestamps collapsed (last wins)")
        frame = frame.drop_duplicates(subset=["ts"], keep="last")

    frame = frame.sort_values("ts").reset_index(drop=True)
    frame["volume"] = frame["volume"].fillna(0).astype("int64")
    frame["oi"] = frame["oi"].fillna(0).astype("int64")
    frame["contract_id"] = contract.contract_id
    frame["interval"] = interval.value

    ordered = [*BAR_COLUMNS_EXT, "contract_id", "interval"]
    frame = attach_provenance(frame[ordered], provenance)

    return NormalizedFrame(rows=frame, rejected=_merge_rejects(rejects), notes=notes)


def normalize_ticks(
    raw: pd.DataFrame,
    *,
    contract: Contract,
    provenance: Provenance,
) -> NormalizedFrame:
    frame = raw.copy()
    if frame.empty:
        empty = pd.DataFrame(columns=[*TICK_COLUMNS, "contract_id", *PROVENANCE_COLUMNS])
        return NormalizedFrame(rows=empty, rejected=pd.DataFrame(), notes=["empty payload"])

    frame["ts"] = to_ist(frame["ts"])
    _numeric(frame, ["ltp", "atp", "bid", "ask"], default=0.0)
    _numeric(frame, ["ltq", "volume", "oi", "bid_qty", "ask_qty", "sequence"], default=0.0)

    rejects: list[pd.DataFrame] = []
    frame, bad = _split(frame, frame["ts"].isna(), "unparseable timestamp")
    rejects.append(bad)
    frame, bad = _split(frame, frame["ltp"].isna() | (frame["ltp"] <= 0), "non-positive ltp")
    rejects.append(bad)
    frame, bad = _split(
        frame,
        (frame["bid"] > 0) & (frame["ask"] > 0) & (frame["bid"] > frame["ask"]),
        "crossed book",
    )
    rejects.append(bad)

    frame = frame.sort_values(["ts", "sequence"]).reset_index(drop=True)
    for column in ["ltq", "volume", "oi", "bid_qty", "ask_qty", "sequence"]:
        frame[column] = frame[column].fillna(0).astype("int64")
    frame["contract_id"] = contract.contract_id

    ordered = [*TICK_COLUMNS, "contract_id"]
    frame = attach_provenance(frame[ordered], provenance)
    return NormalizedFrame(rows=frame, rejected=_merge_rejects(rejects))


def normalize_option_chain(
    raw: pd.DataFrame,
    *,
    provenance: Provenance,
) -> NormalizedFrame:
    """Canonical chain snapshot. One row per strike and right."""
    frame = raw.copy()
    if frame.empty:
        empty = pd.DataFrame(columns=[*CHAIN_COLUMNS, *PROVENANCE_COLUMNS])
        return NormalizedFrame(rows=empty, rejected=pd.DataFrame(), notes=["empty payload"])

    frame["ts"] = to_ist(frame["ts"])
    frame["expiry"] = pd.to_datetime(frame["expiry"], errors="coerce").dt.date
    frame["right"] = frame["right"].astype(str).str.upper().str.strip()
    _numeric(frame, ["strike", "ltp", "bid", "ask", "iv", "delta", "gamma", "theta", "vega", "rho"])
    _numeric(frame, ["volume", "oi", "prev_oi"], default=0.0)

    rejects: list[pd.DataFrame] = []
    frame, bad = _split(frame, frame["ts"].isna(), "unparseable timestamp")
    rejects.append(bad)
    frame, bad = _split(frame, frame["expiry"].isna(), "unparseable expiry")
    rejects.append(bad)
    frame, bad = _split(
        frame,
        ~frame["right"].isin({r.value for r in OptionRight}),
        "unknown option right",
    )
    rejects.append(bad)
    frame, bad = _split(frame, frame["strike"].isna() | (frame["strike"] <= 0), "invalid strike")
    rejects.append(bad)
    frame, bad = _split(
        frame,
        (frame["iv"].notna()) & ((frame["iv"] < 0) | (frame["iv"] > 500)),
        "implausible iv",
    )
    rejects.append(bad)
    frame, bad = _split(
        frame,
        (frame["delta"].notna()) & (frame["delta"].abs() > 1.001),
        "implausible delta",
    )
    rejects.append(bad)

    for column in ["volume", "oi", "prev_oi"]:
        frame[column] = frame[column].fillna(0).astype("int64")
    frame = frame.sort_values(["expiry", "strike", "right"]).reset_index(drop=True)

    frame = attach_provenance(frame[CHAIN_COLUMNS], provenance)
    return NormalizedFrame(rows=frame, rejected=_merge_rejects(rejects))


def normalize_symbol_master(
    contracts: list[Contract],
    *,
    provenance: Provenance,
    as_of: datetime | None = None,
) -> pd.DataFrame:
    """Flatten resolved contracts into the instrument-master table."""
    stamp = as_of or now_utc()
    rows = [
        {
            "as_of": stamp,
            "contract_id": c.contract_id,
            "symbol": c.symbol,
            "segment": c.segment.value,
            "exchange": c.exchange.value,
            "expiry": c.expiry,
            "strike": c.strike,
            "right": c.right.value if c.right else None,
            "lot_size": c.lot_size,
            "tick_size": c.tick_size,
            "vendor_symbol": c.vendor_symbol,
            "vendor_token": c.vendor_token,
        }
        for c in contracts
    ]
    frame = pd.DataFrame(rows, columns=MASTER_COLUMNS)
    return attach_provenance(frame, provenance)
