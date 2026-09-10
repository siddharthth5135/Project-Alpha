"""Exchange session clock.

Gap detection is only as good as the calendar behind it. Rather than trusting a
hardcoded holiday list forever, the calendar accepts an *observed* set of
trading days (learned from ingested index EOD bars) that overrides the
configured list. Special sessions such as Muhurat take precedence over the
holiday list, because the exchange is in fact open for that window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from alpha_desk.marketdata.contracts import IST, BarInterval, BarTimestampConvention, Segment

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CALENDAR_PATH = REPO_ROOT / "configs" / "market_calendar.yaml"


@dataclass(frozen=True)
class SessionWindow:
    open: time
    close: time

    def bounds(self, day: date, tz: str = IST) -> tuple[pd.Timestamp, pd.Timestamp]:
        start = pd.Timestamp.combine(day, self.open).tz_localize(tz)
        end = pd.Timestamp.combine(day, self.close).tz_localize(tz)
        return start, end


@dataclass(frozen=True)
class MarketCalendar:
    timezone: str
    sessions: dict[Segment, SessionWindow]
    holidays: frozenset[date]
    special_sessions: dict[date, SessionWindow] = field(default_factory=dict)
    bar_convention: BarTimestampConvention = BarTimestampConvention.INTERVAL_START
    holidays_verified_through: date | None = None
    observed_trading_days: frozenset[date] | None = None

    def with_observed_days(self, days: frozenset[date]) -> MarketCalendar:
        """Return a calendar that trusts exchange-observed days over config."""
        return MarketCalendar(
            timezone=self.timezone,
            sessions=self.sessions,
            holidays=self.holidays,
            special_sessions=self.special_sessions,
            bar_convention=self.bar_convention,
            holidays_verified_through=self.holidays_verified_through,
            observed_trading_days=days or None,
        )

    def is_trading_day(self, day: date) -> bool:
        if day in self.special_sessions:
            return True
        if self.observed_trading_days is not None:
            return day in self.observed_trading_days
        if day.weekday() >= 5:
            return False
        return day not in self.holidays

    def is_verified(self, day: date) -> bool:
        """False when the holiday list may not cover this date yet."""
        if self.observed_trading_days is not None:
            return True
        if self.holidays_verified_through is None:
            return False
        return day <= self.holidays_verified_through

    def window(self, segment: Segment, day: date) -> SessionWindow:
        special = self.special_sessions.get(day)
        if special is not None:
            return special
        try:
            return self.sessions[segment]
        except KeyError as exc:  # pragma: no cover - guarded by config validation
            raise KeyError(f"no session window configured for segment {segment}") from exc

    def session_bounds(
        self, segment: Segment, day: date
    ) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        if not self.is_trading_day(day):
            return None
        return self.window(segment, day).bounds(day, self.timezone)

    def trading_days(self, start: date, end: date) -> list[date]:
        days = pd.date_range(start, end, freq="D")
        return [d.date() for d in days if self.is_trading_day(d.date())]

    def expected_bar_index(
        self,
        segment: Segment,
        interval: BarInterval,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> pd.DatetimeIndex:
        """Every bar timestamp the exchange should have produced in [start, end].

        Intraday bars are stamped at interval start, so the closing timestamp
        itself is not an expected bar: a 09:15-15:30 session yields 09:15
        through 15:29 at one-minute resolution.
        """
        if interval is BarInterval.TICK:
            raise ValueError("ticks have no expected index")

        start = _as_ist(start, self.timezone)
        end = _as_ist(end, self.timezone)
        stamps: list[pd.Timestamp] = []

        for day in self.trading_days(start.date(), end.date()):
            bounds = self.session_bounds(segment, day)
            if bounds is None:
                continue
            open_ts, close_ts = bounds
            if interval is BarInterval.EOD:
                # Vendors (TrueData, etc.) stamp EOD at calendar midnight IST for
                # the session date, not at the cash close clock.
                stamps.append(pd.Timestamp.combine(day, time(0, 0)).tz_localize(self.timezone))
                continue
            step = pd.Timedelta(minutes=interval.minutes)
            cursor = open_ts
            while cursor < close_ts:
                stamps.append(cursor)
                cursor = cursor + step

        index = pd.DatetimeIndex(stamps, tz=self.timezone)
        return index[(index >= start) & (index <= end)]

    def bars_per_session(self, segment: Segment, interval: BarInterval, day: date) -> int:
        bounds = self.session_bounds(segment, day)
        if bounds is None:
            return 0
        if interval is BarInterval.EOD:
            return 1
        open_ts, close_ts = bounds
        minutes = int((close_ts - open_ts).total_seconds() // 60)
        return minutes // interval.minutes


def _as_ist(ts: pd.Timestamp | str, tz: str) -> pd.Timestamp:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        return stamp.tz_localize(tz)
    return stamp.tz_convert(tz)


def _parse_time(value: str) -> time:
    hour, minute = (int(part) for part in str(value).split(":")[:2])
    return time(hour=hour, minute=minute)


def load_market_calendar(path: Path | None = None) -> MarketCalendar:
    return _load_market_calendar(path or DEFAULT_CALENDAR_PATH)


@lru_cache(maxsize=4)
def _load_market_calendar(path: Path) -> MarketCalendar:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    sessions: dict[Segment, SessionWindow] = {}
    for key, window in (raw.get("sessions") or {}).items():
        sessions[Segment(key)] = SessionWindow(
            open=_parse_time(window["open"]), close=_parse_time(window["close"])
        )
    if not sessions:
        raise ValueError(f"{path}: no sessions configured")

    holiday_block = raw.get("holidays") or {}
    holidays: set[date] = set()
    for _year, entries in (holiday_block.get("dates") or {}).items():
        for entry in entries or []:
            holidays.add(_coerce_date(entry))

    specials: dict[date, SessionWindow] = {}
    for key, window in (raw.get("special_sessions") or {}).items():
        specials[_coerce_date(key)] = SessionWindow(
            open=_parse_time(window["open"]), close=_parse_time(window["close"])
        )

    verified = holiday_block.get("verified_through")
    return MarketCalendar(
        timezone=str(raw.get("timezone", IST)),
        sessions=sessions,
        holidays=frozenset(holidays),
        special_sessions=specials,
        bar_convention=BarTimestampConvention(raw.get("bar_convention", "interval_start")),
        holidays_verified_through=_coerce_date(verified) if verified else None,
    )


def _coerce_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()
