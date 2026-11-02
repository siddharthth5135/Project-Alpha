"""Parquet bar warehouse.

Layout:  <data_root>/bars/<source>/<symbol>/<timeframe>/<YYYY-MM>.parquet

The source is part of the path on purpose. Synthetic bars, Yahoo bars and
broker bars must never merge into one series: a backtest that silently blends
fabricated data with real data is worse than no backtest.

Monthly files keep the file count sane for 15-minute bars while staying small
enough to rewrite atomically. Writes merge with existing data and deduplicate
on timestamp, so re-running an ingest is idempotent.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from alpha_desk.data.schema import BAR_COLUMNS, BarQualityReport, normalize_bars, validate_bars


class ParquetWarehouse:
    def __init__(self, data_root: Path) -> None:
        self._root = Path(data_root)
        self._bars = self._root / "bars"
        self._bars.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def partition_dir(self, source: str, symbol: str, timeframe: str) -> Path:
        return self._bars / source / symbol / timeframe

    def write_bars(
        self, source: str, symbol: str, timeframe: str, df: pd.DataFrame
    ) -> BarQualityReport:
        frame = normalize_bars(df)
        report = validate_bars(frame, symbol=symbol, timeframe=timeframe)
        if frame.empty:
            return report

        target = self.partition_dir(source, symbol, timeframe)
        target.mkdir(parents=True, exist_ok=True)

        for period, chunk in frame.groupby(frame["ts"].dt.strftime("%Y-%m")):
            path = target / f"{period}.parquet"
            if path.exists():
                merged = pd.concat([pd.read_parquet(path), chunk], ignore_index=True)
                merged = (
                    merged.drop_duplicates(subset=["ts"], keep="last")
                    .sort_values("ts")
                    .reset_index(drop=True)
                )
            else:
                merged = chunk.reset_index(drop=True)
            tmp = path.with_suffix(".parquet.tmp")
            merged[BAR_COLUMNS].to_parquet(tmp, index=False)
            tmp.replace(path)
        return report

    def read_bars(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        start: pd.Timestamp | str | None = None,
        end: pd.Timestamp | str | None = None,
    ) -> pd.DataFrame:
        target = self.partition_dir(source, symbol, timeframe)
        files = sorted(target.glob("*.parquet"))
        if not files:
            return pd.DataFrame(columns=BAR_COLUMNS)

        frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        frame = frame.drop_duplicates(subset=["ts"], keep="last").sort_values("ts")
        if start is not None:
            frame = frame[frame["ts"] >= pd.Timestamp(start).tz_convert(frame["ts"].dt.tz)]
        if end is not None:
            frame = frame[frame["ts"] <= pd.Timestamp(end).tz_convert(frame["ts"].dt.tz)]
        return frame.reset_index(drop=True)

    def available(self) -> dict[str, dict[str, list[str]]]:
        """{source: {symbol: [timeframes]}} for everything on disk."""
        out: dict[str, dict[str, list[str]]] = {}
        for source_dir in sorted(p for p in self._bars.iterdir() if p.is_dir()):
            symbols: dict[str, list[str]] = {}
            for symbol_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
                timeframes = sorted(p.name for p in symbol_dir.iterdir() if p.is_dir())
                if timeframes:
                    symbols[symbol_dir.name] = timeframes
            if symbols:
                out[source_dir.name] = symbols
        return out
