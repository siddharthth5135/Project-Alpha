"""Angel One SmartAPI market-data settings.

Credentials live only in ``.env`` (never committed). Network calls are refused
until ``ANGELONE_ALLOW_MARKETDATA=true`` — market data stays OFF by default so
keys cannot be exercised by accident. Order placement remains a separate
``AngelOneBroker`` path and stays blocked until Gate G5.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from alpha_desk.marketdata.contracts import BarInterval, Segment
from alpha_desk.marketdata.errors import ProviderConfigError

REPO_ROOT = Path(__file__).resolve().parents[4]

DEFAULT_BASE_URL = "https://apiconnect.angelone.in"
DEFAULT_SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# Angel documents free hist depth caps per interval; we treat these as soft
# planner limits. Expired F&O is NOT available — capability is declared false.
DEFAULT_HISTORY_DAYS: dict[BarInterval, int] = {
    BarInterval.M1: 30,
    BarInterval.M3: 60,
    BarInterval.M5: 100,
    BarInterval.M10: 100,
    BarInterval.M15: 200,
    BarInterval.M30: 200,
    BarInterval.M60: 400,
    BarInterval.EOD: 2000,
}


@dataclass
class AngelOneConfig:
    api_key: str = field(default="", repr=False)
    client_code: str = ""
    mpin: str = field(default="", repr=False)
    totp_secret: str = field(default="", repr=False)
    base_url: str = DEFAULT_BASE_URL
    scrip_master_url: str = DEFAULT_SCRIP_MASTER_URL
    allow_marketdata: bool = False
    request_timeout_s: float = 20.0
    rest_calls_per_minute: int = 60
    max_live_symbols: int = 50
    poll_interval_s: float = 1.0
    history_days: dict[BarInterval, int] = field(default_factory=lambda: dict(DEFAULT_HISTORY_DAYS))
    segments: tuple[Segment, ...] = (
        Segment.INDEX,
        Segment.EQUITY,
        Segment.FUTURES,
        Segment.OPTIONS,
        Segment.CURRENCY,
        Segment.COMMODITY,
    )
    raw_capture_dir: Path | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ProviderConfigError(f"refusing non-TLS Angel base_url: {self.base_url!r}")
        if self.max_live_symbols <= 0:
            raise ProviderConfigError("max_live_symbols must be positive")
        if self.poll_interval_s <= 0:
            raise ProviderConfigError("poll_interval_s must be positive")

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.client_code and self.mpin and self.totp_secret)

    def require_credentials(self) -> None:
        if not self.has_credentials:
            raise ProviderConfigError(
                "Angel One credentials missing: set ANGELONE_API_KEY, "
                "ANGELONE_CLIENT_CODE, ANGELONE_MPIN, ANGELONE_TOTP_SECRET in .env"
            )

    def require_network_allowed(self) -> None:
        """Hard gate: no SmartAPI call without explicit local enable."""
        if not self.allow_marketdata:
            raise ProviderConfigError(
                "Angel One market-data network is DISABLED. "
                "Set ANGELONE_ALLOW_MARKETDATA=true in local .env only after you "
                "explicitly approve a market-data session. Orders stay blocked separately."
            )

    def redacted(self) -> dict[str, object]:
        return {
            "client_code": _mask(self.client_code),
            "api_key": "***" if self.api_key else "<unset>",
            "mpin": "***" if self.mpin else "<unset>",
            "totp_secret": "***" if self.totp_secret else "<unset>",
            "base_url": self.base_url,
            "allow_marketdata": self.allow_marketdata,
            "max_live_symbols": self.max_live_symbols,
            "poll_interval_s": self.poll_interval_s,
            "history_days": {k.value: v for k, v in self.history_days.items()},
            "segments": [s.value for s in self.segments],
        }

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AngelOneConfig:
        if env is None:
            _load_dotenv()
            env = dict(os.environ)
        history = dict(DEFAULT_HISTORY_DAYS)
        for interval, key in (
            (BarInterval.M1, "ANGELONE_HISTORY_DAYS_M1"),
            (BarInterval.M5, "ANGELONE_HISTORY_DAYS_M5"),
            (BarInterval.M15, "ANGELONE_HISTORY_DAYS_M15"),
            (BarInterval.M30, "ANGELONE_HISTORY_DAYS_M30"),
            (BarInterval.M60, "ANGELONE_HISTORY_DAYS_M60"),
            (BarInterval.EOD, "ANGELONE_HISTORY_DAYS_EOD"),
        ):
            raw = env.get(key, "").strip()
            if raw:
                history[interval] = int(raw)
        segments = _parse_segments(env.get("ANGELONE_SEGMENTS", ""))
        return cls(
            api_key=env.get("ANGELONE_API_KEY", "").strip(),
            client_code=env.get("ANGELONE_CLIENT_CODE", "").strip(),
            mpin=env.get("ANGELONE_MPIN", "").strip(),
            totp_secret=env.get("ANGELONE_TOTP_SECRET", "").strip(),
            base_url=env.get("ANGELONE_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            scrip_master_url=(
                env.get("ANGELONE_SCRIP_MASTER_URL", DEFAULT_SCRIP_MASTER_URL).strip()
                or DEFAULT_SCRIP_MASTER_URL
            ),
            allow_marketdata=_truthy(env.get("ANGELONE_ALLOW_MARKETDATA", "false")),
            request_timeout_s=float(env.get("ANGELONE_TIMEOUT_S", "20") or 20),
            rest_calls_per_minute=int(env.get("ANGELONE_REST_RPM", "60") or 60),
            max_live_symbols=int(env.get("ANGELONE_MAX_LIVE_SYMBOLS", "50") or 50),
            poll_interval_s=float(env.get("ANGELONE_POLL_INTERVAL_S", "1") or 1),
            history_days=history,
            segments=segments or (
                Segment.INDEX,
                Segment.EQUITY,
                Segment.FUTURES,
                Segment.OPTIONS,
                Segment.CURRENCY,
                Segment.COMMODITY,
            ),
        )


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _mask(value: str) -> str:
    if not value:
        return "<unset>"
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}…{value[-2:]}"


def _parse_segments(raw: str) -> tuple[Segment, ...]:
    if not raw.strip():
        return ()
    out: list[Segment] = []
    for part in raw.split(","):
        key = part.strip().upper()
        if not key:
            continue
        out.append(Segment(key if key != "COM" else "COM"))
    return tuple(out)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(REPO_ROOT / ".env", override=False)
