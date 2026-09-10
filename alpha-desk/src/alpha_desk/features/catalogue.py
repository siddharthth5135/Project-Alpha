"""The frozen lean feature set v0.

Lookbacks are declared, not guessed: they set the warmup window that gets
dropped, and the walk-forward embargo must be at least as large. For EWM
features the declared lookback is roughly four spans, past which the weight on
older bars is negligible.

Adding a feature means a new version and a new trial. Never edit this list in
place to make a backtest look better.
"""

from __future__ import annotations

from alpha_desk.features import builders as b
from alpha_desk.features.registry import FeatureSet, FeatureSpec

SPECS_V0 = (
    FeatureSpec("r_1", 1, b.r_1, "1-bar return"),
    FeatureSpec("r_5", 5, b.r_5, "5-bar return"),
    FeatureSpec("r_20", 20, b.r_20, "20-bar return"),
    FeatureSpec(
        "ema_fast_slow_ratio", 4 * b.EMA_SLOW, b.ema_fast_slow_ratio, "EMA12/EMA26 - 1"
    ),
    FeatureSpec(
        "ema_slope",
        4 * b.EMA_SLOW + b.EMA_SLOPE_BARS,
        b.ema_slope,
        "5-bar change in EMA26, scaled by level",
    ),
    FeatureSpec("rsi_14", b.RSI_PERIOD + 1, b.rsi_14, "Wilder RSI, 14 bars"),
    FeatureSpec(
        "realized_vol_20", b.VOL_WINDOW + 1, b.realized_vol_20, "stdev of 20 log returns"
    ),
    FeatureSpec("atr_14", b.ATR_PERIOD + 1, b.atr_14, "ATR in points, for stop sizing"),
    FeatureSpec(
        "atr_14_pct", b.ATR_PERIOD + 1, b.atr_14_pct, "ATR as a fraction of price"
    ),
    FeatureSpec(
        "volume_zscore_20", b.VOLUME_WINDOW, b.volume_zscore_20, "volume z-score, 20 bars"
    ),
    FeatureSpec("time_of_day", 0, b.time_of_day, "fraction of the IST session elapsed"),
    FeatureSpec("day_of_week", 0, b.day_of_week, "0=Monday"),
    FeatureSpec(
        "vol_regime_bucket",
        b.REGIME_WINDOW + b.VOL_WINDOW + 1,
        b.vol_regime_bucket,
        "trailing tercile of realised vol: 0 low, 1 mid, 2 high",
    ),
    FeatureSpec("vwap_distance", 1, b.vwap_distance, "close vs running session VWAP"),
)


def build_feature_set_v0() -> FeatureSet:
    return FeatureSet(name="lean", version="v0", specs=SPECS_V0)


FEATURE_SET_V0 = build_feature_set_v0()
