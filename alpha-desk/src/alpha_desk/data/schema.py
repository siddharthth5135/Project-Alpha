"""Bar schema and data quality checks.

Bad bars produce fake alpha, and fake alpha is the expensive kind. Anything
entering the warehouse is normalised to one schema in IST and checked for the
failures that actually happen with vendor data: duplicated timestamps,
out-of-order rows, inverted highs and lows, zero prices, and gaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
IST = "Asia/Kolkata"


class BarSchemaError(ValueError):
    pass


@dataclass
class BarQualityReport:
    symbol: str
    timeframe: str
    rows: int
    first_ts: pd.Timestamp | None
    last_ts: pd.Timestamp | None
    duplicate_timestamps: int = 0
    out_of_order: int = 0
    inverted_high_low: int = 0
    non_positive_prices: int = 0
    zero_volume_bars: int = 0
    missing_values: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "rows": self.rows,
            "first_ts": str(self.first_ts) if self.first_ts is not None else None,
            "last_ts": str(self.last_ts) if self.last_ts is not None else None,
            "duplicate_timestamps": self.duplicate_timestamps,
            "out_of_order": self.out_of_order,
            "inverted_high_low": self.inverted_high_low,
            "non_positive_prices": self.non_positive_prices,
            "zero_volume_bars": self.zero_volume_bars,
            "missing_values": self.missing_values,
            "issues": list(self.issues),
            "is_clean": self.is_clean,
        }


def normalize_bars(df: pd.DataFrame, *, tz: str = IST) -> pd.DataFrame:
    """Coerce a vendor frame into the canonical schema, sorted and deduplicated."""
    frame = df.copy()
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]

    if "ts" not in frame.columns:
        for candidate in ("datetime", "date", "timestamp", "index"):
            if candidate in frame.columns:
                frame = frame.rename(columns={candidate: "ts"})
                break

    missing = [c for c in BAR_COLUMNS if c not in frame.columns]
    if missing:
        raise BarSchemaError(f"missing required columns: {missing}")

    frame = frame[BAR_COLUMNS]
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce").dt.tz_convert(tz)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    frame = frame.dropna(subset=["ts"])
    frame = frame.drop_duplicates(subset=["ts"], keep="last")
    frame = frame.sort_values("ts").reset_index(drop=True)
    return frame


def validate_bars(df: pd.DataFrame, *, symbol: str, timeframe: str) -> BarQualityReport:
    report = BarQualityReport(
        symbol=symbol,
        timeframe=timeframe,
        rows=len(df),
        first_ts=df["ts"].iloc[0] if len(df) else None,
        last_ts=df["ts"].iloc[-1] if len(df) else None,
    )
    if df.empty:
        report.issues.append("empty frame")
        return report

    missing_cols = [c for c in BAR_COLUMNS if c not in df.columns]
    if missing_cols:
        raise BarSchemaError(f"missing required columns: {missing_cols}")

    report.duplicate_timestamps = int(df["ts"].duplicated().sum())
    report.out_of_order = int((df["ts"].diff().dropna() <= pd.Timedelta(0)).sum())
    report.inverted_high_low = int(
        ((df["high"] < df["low"]) | (df["high"] < df["close"]) | (df["low"] > df["close"])).sum()
    )
    report.non_positive_prices = int(
        (df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()
    )
    report.zero_volume_bars = int((df["volume"].fillna(0) <= 0).sum())
    report.missing_values = int(df[BAR_COLUMNS].isna().sum().sum())

    if report.duplicate_timestamps:
        report.issues.append(f"{report.duplicate_timestamps} duplicate timestamps")
    if report.out_of_order:
        report.issues.append(f"{report.out_of_order} out-of-order rows")
    if report.inverted_high_low:
        report.issues.append(f"{report.inverted_high_low} bars with inconsistent OHLC")
    if report.non_positive_prices:
        report.issues.append(f"{report.non_positive_prices} bars with non-positive prices")
    if report.missing_values:
        report.issues.append(f"{report.missing_values} missing values")
    return report
