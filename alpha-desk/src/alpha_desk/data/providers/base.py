from __future__ import annotations

from typing import Protocol

import pandas as pd


class BarProvider(Protocol):
    """Any source of OHLCV bars, normalised to the canonical schema."""

    name: str

    def fetch(self, symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        """Return bars for [start, end] with columns ts/open/high/low/close/volume."""
