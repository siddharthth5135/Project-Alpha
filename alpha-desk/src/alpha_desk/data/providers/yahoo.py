"""Yahoo Finance provider - interim research feed only.

Known limits, recorded here so nobody forgets them at promote time:

* Intraday history is short (roughly 60 days at 15 minutes), which is not
  enough for a walk-forward with embargo.
* Index quotes are spot, not futures. Basis, roll and expiry behaviour are
  absent, so F&O cost and slippage assumptions cannot be validated here.
* No corporate actions detail, no options chain, no FII/DII or delivery data.
* Volume on Indian indices is unreliable or zero.

It is fine for building and testing pipelines. It is not evidence of an edge,
and the backtest report stamps results sourced from it as UNVERIFIED-DATA.
"""

from __future__ import annotations

import pandas as pd

from alpha_desk.data.schema import normalize_bars

_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "60m",
    "1h": "60m",
    "1d": "1d",
}

# Yahoo caps intraday lookback by interval.
_MAX_LOOKBACK_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "1d": 20_000}


class YahooProvider:
    name = "yahoo"
    research_only = True

    def __init__(self, proxy_map: dict[str, str] | None = None) -> None:
        self._proxy = proxy_map or {}

    def ticker_for(self, symbol: str) -> str:
        return self._proxy.get(symbol, symbol)

    def max_lookback_days(self, timeframe: str) -> int:
        return _MAX_LOOKBACK_DAYS[self._interval(timeframe)]

    def fetch(self, symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        try:
            import yfinance
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise RuntimeError("yfinance is required for the Yahoo provider") from exc

        interval = self._interval(timeframe)
        raw = yfinance.download(
            self.ticker_for(symbol),
            start=start,
            end=end,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if raw is None or raw.empty:
            return normalize_bars(
                pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
            )

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        frame = raw.reset_index()
        frame = frame.rename(columns={frame.columns[0]: "ts"})
        return normalize_bars(frame)

    @staticmethod
    def _interval(timeframe: str) -> str:
        try:
            return _INTERVAL_MAP[timeframe]
        except KeyError as exc:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; supported: {sorted(_INTERVAL_MAP)}"
            ) from exc
