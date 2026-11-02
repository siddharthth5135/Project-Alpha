"""Continuous futures series from individual contracts.

Two things make this dangerous, and both are handled explicitly.

**Roll timing.** The series must switch to the next contract on a rule that was
knowable at the time. `days_to_expiry` rolls a fixed number of sessions before
expiry, which is deterministic from the contract calendar. A volume-based rule
is also supported, and it compares volumes only up to the current bar.

**Back-adjustment repaints history.** Splicing contracts leaves a price gap at
each roll. Difference-adjusting removes the gap, but it rewrites every earlier
price, so a series built today does not match one built last month. That is
fine for charting and fatal for a naive point-in-time claim about price levels.

The rule this desk follows: **features are built from returns, and the adjusted
return series is invariant to later rolls.** `build_continuous` therefore emits
an `adj_close` for level display plus a `ret` column that is stable, and
`assert_returns_are_roll_invariant` proves the stability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from alpha_desk.data.schema import BAR_COLUMNS

CONTRACT_COLUMNS = [*BAR_COLUMNS, "contract", "expiry"]


@dataclass(frozen=True)
class RollRule:
    """How to choose the front contract."""

    method: str = "days_to_expiry"  # or "volume"
    days_before_expiry: int = 2

    def __post_init__(self) -> None:
        if self.method not in {"days_to_expiry", "volume"}:
            raise ValueError(f"unknown roll method {self.method!r}")
        if self.days_before_expiry < 0:
            raise ValueError("days_before_expiry cannot be negative")


class ContinuousSeriesError(ValueError):
    pass


def _validate(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in CONTRACT_COLUMNS if c not in frame.columns]
    if missing:
        raise ContinuousSeriesError(f"contract bars missing columns: {missing}")
    out = frame.copy()
    out["expiry"] = pd.to_datetime(out["expiry"]).dt.date
    return out.sort_values(["ts", "expiry"]).reset_index(drop=True)


def select_front_contract(bars: pd.DataFrame, rule: RollRule) -> pd.DataFrame:
    """Tag each timestamp with the contract that is front-month at that moment."""
    frame = _validate(bars)
    chosen: list[pd.Series] = []

    for ts, group in frame.groupby("ts", sort=True):
        session = ts.date()
        eligible = group[
            group["expiry"].map(lambda e: (e - session).days) > rule.days_before_expiry
        ]
        if eligible.empty:
            # Every listed contract is inside the roll window; take the furthest
            # expiry rather than inventing data.
            eligible = group[group["expiry"] == group["expiry"].max()]

        if rule.method == "volume":
            # Compares only volumes at or before this bar, so it stays causal.
            pick = eligible.loc[eligible["volume"].idxmax()]
        else:
            pick = eligible.loc[eligible["expiry"].idxmin()]
        chosen.append(pick)

    out = pd.DataFrame(chosen).reset_index(drop=True)
    return out.sort_values("ts").reset_index(drop=True)


def build_continuous(
    bars: pd.DataFrame,
    rule: RollRule | None = None,
    adjust: str = "difference",
) -> pd.DataFrame:
    """Stitch per-contract bars into one series.

    Returns columns: ts, open, high, low, close, volume, contract, expiry,
    is_roll, ret, adj_close.

    * `ret` is the within-contract return. On a roll bar it is the next
      contract's own return, never the artificial gap between contracts, so no
      fabricated jump enters the model.
    * `adj_close` is difference-adjusted for display. Treat its absolute level
      as unusable for point-in-time claims.
    """
    if adjust not in {"difference", "none"}:
        raise ValueError(f"unknown adjustment {adjust!r}")

    front = select_front_contract(bars, rule or RollRule())
    if front.empty:
        return pd.DataFrame(columns=[*CONTRACT_COLUMNS, "is_roll", "ret", "adj_close"])

    front["is_roll"] = front["contract"] != front["contract"].shift(1)
    front.loc[front.index[0], "is_roll"] = False

    # Within-contract return: pct_change of close, invalidated at roll bars.
    ret = front["close"].pct_change()
    ret[front["is_roll"].to_numpy()] = np.nan
    front["ret"] = ret

    # Fill roll-bar returns from the incoming contract's own history when it is
    # available in the raw frame; otherwise leave NaN and let the caller drop it.
    raw = _validate(bars)
    for idx in front.index[front["is_roll"].to_numpy()]:
        row = front.loc[idx]
        history = raw[(raw["contract"] == row["contract"]) & (raw["ts"] <= row["ts"])]
        if len(history) >= 2:
            prev_close = history.iloc[-2]["close"]
            front.at[idx, "ret"] = row["close"] / prev_close - 1.0

    front["adj_close"] = _adjust_close(front) if adjust == "difference" else front["close"]
    return front.reset_index(drop=True)


def _adjust_close(front: pd.DataFrame) -> pd.Series:
    """Difference back-adjustment: shift older history so roll gaps disappear."""
    adjusted = front["close"].astype(float).to_numpy().copy()
    roll_positions = np.flatnonzero(front["is_roll"].to_numpy())
    for pos in roll_positions[::-1]:
        if pos == 0:
            continue
        gap = adjusted[pos] - adjusted[pos - 1]
        adjusted[:pos] += gap
    return pd.Series(adjusted, index=front.index)


def assert_returns_are_roll_invariant(bars: pd.DataFrame, rule: RollRule | None = None) -> None:
    """Prove that extending the data with later contracts does not change earlier
    returns. Levels do change under back-adjustment; returns must not."""
    frame = _validate(bars)
    expiries = sorted(frame["expiry"].unique())
    if len(expiries) < 2:
        raise ContinuousSeriesError("need at least two contracts to test roll invariance")

    partial = build_continuous(frame[frame["expiry"] < expiries[-1]], rule)
    full = build_continuous(frame, rule)

    overlap = full[full["ts"].isin(partial["ts"])]
    left = partial.set_index("ts")["ret"].dropna()
    right = overlap.set_index("ts")["ret"].reindex(left.index)

    mismatched = (left - right).abs() > 1e-12
    if mismatched.any():
        first = mismatched.idxmax()
        raise ContinuousSeriesError(
            f"returns changed when later contracts were added, first at {first}"
        )


def synthetic_contracts(
    *,
    start: str,
    periods: int,
    expiries: list[date],
    seed: int = 11,
    start_price: float = 24_000.0,
    basis_per_contract: float = 40.0,
    freq: str = "15min",
) -> pd.DataFrame:
    """Deterministic multi-contract bars for tests and roll drills.

    Timestamps follow the NSE session clock, so `periods` bars span a realistic
    number of calendar days and actually reach an expiry. Each further-dated
    contract trades at a fixed premium, which creates the price gap a real roll
    produces, and near contracts carry an order of magnitude more volume.
    """
    rng = np.random.default_rng(seed)
    stamps = _session_stamps(start, periods, freq)
    spot = start_price * np.exp(np.cumsum(rng.normal(0, 0.0004, size=periods)))

    tiers = len(expiries)
    rows = []
    for tier, expiry in enumerate(sorted(expiries)):
        price = spot + tier * basis_per_contract
        rows.append(
            pd.DataFrame(
                {
                    "ts": stamps,
                    "open": np.round(price, 2),
                    "high": np.round(price * 1.001, 2),
                    "low": np.round(price * 0.999, 2),
                    "close": np.round(price, 2),
                    "volume": rng.integers(10_000, 60_000, size=periods)
                    * 10 ** (tiers - tier - 1),
                    "contract": f"NIFTY{expiry:%y%b}".upper(),
                    "expiry": expiry,
                }
            )
        )
    out = pd.concat(rows, ignore_index=True)
    return out[out["ts"].dt.date <= out["expiry"]].reset_index(drop=True)


def _session_stamps(start: str, periods: int, freq: str) -> pd.DatetimeIndex:
    step = pd.Timedelta(freq)
    per_session = int(pd.Timedelta(hours=6, minutes=15) / step) + 1
    sessions_needed = periods // per_session + 2
    sessions = pd.bdate_range(start=pd.Timestamp(start).date(), periods=sessions_needed)

    stamps: list[pd.Timestamp] = []
    for day in sessions:
        open_ts = pd.Timestamp(day).tz_localize("Asia/Kolkata") + pd.Timedelta(
            hours=9, minutes=15
        )
        close_ts = pd.Timestamp(day).tz_localize("Asia/Kolkata") + pd.Timedelta(
            hours=15, minutes=30
        )
        stamps.extend(pd.date_range(open_ts, close_ts, freq=freq))
        if len(stamps) >= periods:
            break
    return pd.DatetimeIndex(stamps[:periods])
