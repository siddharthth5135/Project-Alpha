"""Feature computations.

Every function here must be *causal*: the value at row t may use rows 0..t and
nothing after. Anything that needs a full-sample statistic (a z-score against
the whole history, a quantile over the entire series, a normalisation constant)
is leakage, and `tests/test_features.py` will catch it.

The subtle one is `vol_regime_bucket`. Bucketing volatility against
whole-sample terciles is the classic accident: it looks like a harmless label
and quietly tells the model what volatility was like in the future. Here the
bucket comes from a trailing rolling percentile instead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SESSION_OPEN_MINUTES = 9 * 60 + 15  # 09:15 IST
SESSION_MINUTES = 375  # 09:15 to 15:30

EMA_FAST = 12
EMA_SLOW = 26
EMA_SLOPE_BARS = 5
RSI_PERIOD = 14
VOL_WINDOW = 20
ATR_PERIOD = 14
VOLUME_WINDOW = 20
REGIME_WINDOW = 120


def _log_returns(close: pd.Series) -> pd.Series:
    return np.log(close).diff()


def r_1(bars: pd.DataFrame) -> pd.Series:
    return bars["close"].pct_change(1)


def r_5(bars: pd.DataFrame) -> pd.Series:
    return bars["close"].pct_change(5)


def r_20(bars: pd.DataFrame) -> pd.Series:
    return bars["close"].pct_change(20)


def ema_fast_slow_ratio(bars: pd.DataFrame) -> pd.Series:
    fast = bars["close"].ewm(span=EMA_FAST, adjust=False).mean()
    slow = bars["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return fast / slow - 1.0


def ema_slope(bars: pd.DataFrame) -> pd.Series:
    slow = bars["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return slow.diff(EMA_SLOPE_BARS) / slow


def rsi_14(bars: pd.DataFrame) -> pd.Series:
    delta = bars["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)


def realized_vol_20(bars: pd.DataFrame) -> pd.Series:
    return _log_returns(bars["close"]).rolling(VOL_WINDOW).std()


def _true_range(bars: pd.DataFrame) -> pd.Series:
    prev_close = bars["close"].shift(1)
    ranges = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr_14(bars: pd.DataFrame) -> pd.Series:
    """ATR in price points. Used for stop placement, where points are what matter."""
    return _true_range(bars).ewm(alpha=1.0 / ATR_PERIOD, adjust=False).mean()


def atr_14_pct(bars: pd.DataFrame) -> pd.Series:
    """ATR as a fraction of price, so NIFTY and BANKNIFTY are comparable.

    Models should use this rather than `atr_14`: pooling instruments on a
    points-based feature teaches the model which instrument it is looking at.
    """
    return atr_14(bars) / bars["close"]


def volume_zscore_20(bars: pd.DataFrame) -> pd.Series:
    volume = bars["volume"].astype(float)
    mean = volume.rolling(VOLUME_WINDOW).mean()
    std = volume.rolling(VOLUME_WINDOW).std()
    return ((volume - mean) / std.replace(0.0, np.nan)).fillna(0.0)


def time_of_day(bars: pd.DataFrame) -> pd.Series:
    """Fraction of the session elapsed, from the fixed 09:15 open.

    Deliberately derived from a constant rather than from the day's first bar:
    a data-derived session start is one refactor away from peeking.
    """
    minutes = bars["ts"].dt.hour * 60 + bars["ts"].dt.minute - SESSION_OPEN_MINUTES
    return (minutes / SESSION_MINUTES).clip(0.0, 1.0)


def day_of_week(bars: pd.DataFrame) -> pd.Series:
    return bars["ts"].dt.dayofweek.astype(float)


def vol_regime_bucket(bars: pd.DataFrame) -> pd.Series:
    """Trailing tercile of realised volatility: 0 low, 1 medium, 2 high.

    The percentile rank is computed over a rolling window, so the bucket at bar
    t only knows the volatility distribution up to t.
    """
    vol = realized_vol_20(bars)
    rank = vol.rolling(REGIME_WINDOW).apply(
        lambda window: (window[:-1] < window[-1]).mean() if len(window) > 1 else 0.5,
        raw=True,
    )
    buckets = pd.Series(np.nan, index=bars.index, dtype=float)
    buckets[rank <= 1 / 3] = 0.0
    buckets[(rank > 1 / 3) & (rank <= 2 / 3)] = 1.0
    buckets[rank > 2 / 3] = 2.0
    return buckets


def vwap_distance(bars: pd.DataFrame) -> pd.Series:
    """Distance from the running session VWAP, as a fraction.

    Yahoo reports zero volume on Indian indices, so the fallback is a running
    mean of typical price. That is a weaker feature, and it is one more reason
    the paid feed matters before promotion.
    """
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    session = bars["ts"].dt.date
    volume = bars["volume"].astype(float).fillna(0.0)

    cum_volume = volume.groupby(session).cumsum()
    cum_pv = (typical * volume).groupby(session).cumsum()
    vwap = cum_pv / cum_volume.replace(0.0, np.nan)

    running_mean = typical.groupby(session).expanding().mean().reset_index(level=0, drop=True)
    vwap = vwap.fillna(running_mean)
    return bars["close"] / vwap - 1.0
