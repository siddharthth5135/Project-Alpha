"""Deterministic synthetic bars.

Used by tests and by anyone building pipeline plumbing without a network. The
series is a seeded random walk on an NSE session clock, so runs are
reproducible and every test sees the same data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from alpha_desk.data.schema import normalize_bars

_SESSION_START = "09:15"
_SESSION_END = "15:30"
_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1h": 60, "1d": 1440}


class SyntheticProvider:
    name = "synthetic"
    research_only = True

    def __init__(self, seed: int = 42, start_price: float = 24_000.0, vol_bps: float = 12.0):
        self._seed = seed
        self._start_price = start_price
        self._vol_bps = vol_bps

    def fetch(self, symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        step = _MINUTES.get(timeframe)
        if step is None:
            raise ValueError(f"unsupported timeframe {timeframe!r}")

        rng = np.random.default_rng(abs(hash((symbol, self._seed))) % (2**32))
        sessions = pd.bdate_range(start=start, end=end, tz="Asia/Kolkata")
        stamps: list[pd.Timestamp] = []
        for day in sessions:
            if timeframe == "1d":
                stamps.append(day.normalize() + pd.Timedelta(hours=15, minutes=30))
                continue
            open_ts = day.normalize() + pd.Timedelta(_SESSION_START + ":00")
            close_ts = day.normalize() + pd.Timedelta(_SESSION_END + ":00")
            stamps.extend(pd.date_range(open_ts, close_ts, freq=f"{step}min"))

        if not stamps:
            return normalize_bars(
                pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
            )

        n = len(stamps)
        shocks = rng.normal(0.0, self._vol_bps / 10_000.0, size=n)
        close = self._start_price * np.exp(np.cumsum(shocks))
        open_ = np.concatenate([[self._start_price], close[:-1]])
        spread = np.abs(rng.normal(0.0, self._vol_bps / 10_000.0, size=n)) * close
        high = np.maximum(open_, close) + spread
        low = np.minimum(open_, close) - spread
        volume = rng.integers(50_000, 250_000, size=n)

        return normalize_bars(
            pd.DataFrame(
                {
                    "ts": stamps,
                    "open": np.round(open_, 2),
                    "high": np.round(high, 2),
                    "low": np.round(low, 2),
                    "close": np.round(close, 2),
                    "volume": volume,
                }
            )
        )
