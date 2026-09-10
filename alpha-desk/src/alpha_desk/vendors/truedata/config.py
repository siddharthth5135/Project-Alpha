"""TrueData connection settings.

Hosts, ports and quotas are configuration, not constants in code: TrueData
assigns the live websocket port per account and gates history depth per plan, so
the trial account and a later commercial account differ only by ``.env``.
Credentials are held in memory only and never appear in ``repr`` or logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from alpha_desk.marketdata.contracts import BarInterval, Segment
from alpha_desk.marketdata.errors import ProviderConfigError

REPO_ROOT = Path(__file__).resolve().parents[4]

DEFAULT_AUTH_URL = "https://auth.truedata.in/token"
DEFAULT_SYMBOLS_URL = "https://api.truedata.in"
DEFAULT_HISTORY_URL = "https://history.truedata.in"
DEFAULT_ANALYTICS_URL = "https://analytics.truedata.in"
DEFAULT_GREEKS_URL = "https://greeks.truedata.in"
DEFAULT_WS_HOST = "wss://push.truedata.in"

# TrueData's default plan ships six months of one-minute history; deeper history
# is a paid add-on. Planners read these numbers, so an upgraded plan is a config
# change rather than a code change.
DEFAULT_HISTORY_DAYS: dict[BarInterval, int] = {
    BarInterval.TICK: 30,
    BarInterval.M1: 183,
    BarInterval.M5: 183,
    BarInterval.M15: 183,
    BarInterval.M30: 183,
    BarInterval.M60: 183,
    BarInterval.EOD: 7300,
}


@dataclass
class TrueDataConfig:
    """Everything needed to talk to TrueData, credentials included."""

    username: str = ""
    password: str = field(default="", repr=False)
    auth_url: str = DEFAULT_AUTH_URL
    symbols_url: str = DEFAULT_SYMBOLS_URL
    history_url: str = DEFAULT_HISTORY_URL
    analytics_url: str = DEFAULT_ANALYTICS_URL
    greeks_url: str = DEFAULT_GREEKS_URL
    ws_host: str = DEFAULT_WS_HOST
    ws_port: int = 8082
    sandbox: bool = False
    request_timeout_s: float = 15.0
    connect_timeout_s: float = 10.0
    max_retries: int = 4
    backoff_base_s: float = 0.5
    backoff_max_s: float = 20.0
    rest_calls_per_minute: int = 60
    max_live_symbols: int = 100
    token_refresh_margin_s: float = 300.0
    heartbeat_timeout_s: float = 20.0
    reconnect_max_backoff_s: float = 60.0
    history_days: dict[BarInterval, int] = field(default_factory=lambda: dict(DEFAULT_HISTORY_DAYS))
    segments: tuple[Segment, ...] = (
        Segment.INDEX,
        Segment.EQUITY,
        Segment.FUTURES,
        Segment.OPTIONS,
    )
    entitled_greeks: bool = True
    entitled_ticks: bool = True
    raw_capture_dir: Path | None = None

    def __post_init__(self) -> None:
        self.symbols_url = self.symbols_url.rstrip("/")
        self.history_url = self.history_url.rstrip("/")
        self.analytics_url = self.analytics_url.rstrip("/")
        self.greeks_url = self.greeks_url.rstrip("/")
        self.ws_host = self.ws_host.rstrip("/")
        for url in (self.auth_url, self.history_url, self.analytics_url, self.greeks_url):
            if not url.startswith("https://"):
                raise ProviderConfigError(f"refusing non-TLS endpoint: {url!r}")
        if not self.ws_host.startswith("wss://"):
            raise ProviderConfigError(f"refusing non-TLS websocket host: {self.ws_host!r}")
        if self.max_live_symbols <= 0:
            raise ProviderConfigError("max_live_symbols must be positive")

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def ws_url(self) -> str:
        return f"{self.ws_host}:{self.ws_port}"

    def require_credentials(self) -> None:
        if not self.has_credentials:
            raise ProviderConfigError(
                "TrueData credentials missing: set TRUEDATA_USER and TRUEDATA_PASSWORD in .env"
            )

    def redacted(self) -> dict[str, object]:
        """Safe to log: identifies the account without exposing the secret."""
        return {
            "username": _mask(self.username),
            "password": "***" if self.password else "<unset>",
            "auth_url": self.auth_url,
            "symbols_url": self.symbols_url,
            "history_url": self.history_url,
            "analytics_url": self.analytics_url,
            "greeks_url": self.greeks_url,
            "ws_url": self.ws_url,
            "sandbox": self.sandbox,
            "rest_calls_per_minute": self.rest_calls_per_minute,
            "max_live_symbols": self.max_live_symbols,
            "history_days": {k.value: v for k, v in self.history_days.items()},
            "segments": [s.value for s in self.segments],
        }

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> TrueDataConfig:
        source = _env(env)
        history_days = dict(DEFAULT_HISTORY_DAYS)
        for interval in BarInterval:
            key = f"TRUEDATA_HISTORY_DAYS_{interval.name}"
            if source.get(key):
                history_days[interval] = _int(source, key, history_days.get(interval, 0))

        segments = tuple(
            Segment(part.strip().upper())
            for part in source.get("TRUEDATA_SEGMENTS", "INDEX,EQ,FUT,OPT").split(",")
            if part.strip()
        )

        capture = source.get("TRUEDATA_RAW_CAPTURE_DIR", "").strip()
        return cls(
            username=source.get("TRUEDATA_USER", "").strip(),
            password=source.get("TRUEDATA_PASSWORD", "").strip(),
            auth_url=source.get("TRUEDATA_AUTH_URL", DEFAULT_AUTH_URL).strip(),
            symbols_url=source.get("TRUEDATA_SYMBOLS_URL", DEFAULT_SYMBOLS_URL).strip(),
            history_url=source.get(
                "TRUEDATA_HISTORY_URL",
                source.get("TRUEDATA_HIST_URL", DEFAULT_HISTORY_URL),
            ).strip(),
            analytics_url=source.get("TRUEDATA_ANALYTICS_URL", DEFAULT_ANALYTICS_URL).strip(),
            greeks_url=source.get("TRUEDATA_GREEKS_URL", DEFAULT_GREEKS_URL).strip(),
            ws_host=source.get("TRUEDATA_WS_HOST", DEFAULT_WS_HOST).strip(),
            ws_port=_int(source, "TRUEDATA_WS_PORT", 8082),
            sandbox=_bool(source, "TRUEDATA_SANDBOX", False),
            request_timeout_s=_float(source, "TRUEDATA_TIMEOUT_S", 15.0),
            max_retries=_int(source, "TRUEDATA_MAX_RETRIES", 4),
            rest_calls_per_minute=_int(source, "TRUEDATA_REST_RPM", 60),
            max_live_symbols=_int(source, "TRUEDATA_MAX_LIVE_SYMBOLS", 100),
            heartbeat_timeout_s=_float(source, "TRUEDATA_HEARTBEAT_TIMEOUT_S", 20.0),
            history_days=history_days,
            segments=segments or (Segment.INDEX,),
            entitled_greeks=_bool(source, "TRUEDATA_ENTITLED_GREEKS", True),
            entitled_ticks=_bool(source, "TRUEDATA_ENTITLED_TICKS", True),
            raw_capture_dir=Path(capture) if capture else None,
        )


def _env(env: dict[str, str] | None) -> dict[str, str]:
    if env is not None:
        return env
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env", override=False)
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        pass
    return dict(os.environ)


def _int(source: dict[str, str], key: str, default: int) -> int:
    raw = source.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ProviderConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _float(source: dict[str, str], key: str, default: float) -> float:
    raw = source.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ProviderConfigError(f"{key} must be a number, got {raw!r}") from exc


def _bool(source: dict[str, str], key: str, default: bool) -> bool:
    raw = source.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ProviderConfigError(f"{key} must be a boolean, got {raw!r}")


def _mask(value: str) -> str:
    if len(value) <= 3:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 3)}{value[-1]}"
