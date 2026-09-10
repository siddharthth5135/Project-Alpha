"""Transport seams for the TrueData adapter.

The adapter never calls ``requests`` or a websocket library directly. It talks
to these narrow protocols, which gives us three things: the whole vendor layer is
testable offline with deterministic fakes, timeouts and TLS settings are enforced
in exactly one place, and a library swap touches one file.

Retry policy lives here as data so every caller retries identically and tests can
drive it with a fake clock instead of real sleeps.
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from alpha_desk.marketdata.errors import (
    AuthError,
    MarketDataError,
    NotEntitledError,
    RateLimitError,
    TransportError,
    UpstreamError,
)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def retry_after(self) -> float | None:
        raw = self.headers.get("Retry-After") or self.headers.get("retry-after")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None


class HttpTransport(Protocol):
    """Minimal synchronous HTTP surface."""

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        timeout: float = 15.0,
    ) -> HttpResponse: ...


class WsTransport(Protocol):
    """Minimal synchronous websocket surface."""

    @property
    def connected(self) -> bool: ...

    def connect(self, url: str, *, timeout: float = 10.0) -> None: ...

    def send(self, payload: str) -> None: ...

    def recv(self, timeout: float = 5.0) -> str | None:
        """Return the next frame, or None if nothing arrived within timeout."""
        ...

    def close(self) -> None: ...


class RequestsHttpTransport:
    """``requests``-backed transport with a pooled session and hard timeouts."""

    def __init__(self, *, user_agent: str = "alpha-desk/0.1", verify_tls: bool = True):
        self._user_agent = user_agent
        self._verify = verify_tls
        self._session: object | None = None

    def _ensure_session(self) -> object:
        if self._session is None:
            try:
                import requests
            except ImportError as exc:  # pragma: no cover - declared dependency
                raise TransportError("requests is not installed") from exc
            session = requests.Session()
            session.headers.update({"User-Agent": self._user_agent, "Accept": "*/*"})
            self._session = session
        return self._session

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        timeout: float = 15.0,
    ) -> HttpResponse:
        session = self._ensure_session()
        started = time.monotonic()
        try:
            response = session.request(  # type: ignore[attr-defined]
                method.upper(),
                url,
                params=dict(params or {}),
                headers=dict(headers or {}),
                data=dict(data or {}) or None,
                timeout=timeout,
                verify=self._verify,
            )
        except Exception as exc:  # requests raises a family of transport errors
            raise TransportError(f"{method.upper()} {_host(url)} failed: {type(exc).__name__}") from exc
        return HttpResponse(
            status=response.status_code,
            body=response.text,
            headers=dict(response.headers),
            elapsed_s=time.monotonic() - started,
        )

    def close(self) -> None:
        session = self._session
        self._session = None
        if session is not None and hasattr(session, "close"):
            session.close()  # type: ignore[attr-defined]


class WebsocketClientTransport:
    """``websocket-client``-backed transport.

    Reads are non-blocking-with-timeout so the caller owns heartbeat
    supervision: a silent socket is a failure we must detect, not wait on
    forever.
    """

    def __init__(self, *, verify_tls: bool = True):
        self._verify = verify_tls
        self._socket: object | None = None

    @property
    def connected(self) -> bool:
        socket = self._socket
        return bool(socket is not None and getattr(socket, "connected", False))

    def connect(self, url: str, *, timeout: float = 10.0) -> None:
        try:
            from websocket import create_connection
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise TransportError("websocket-client is not installed") from exc
        sslopt = None if self._verify else {"cert_reqs": 0}
        try:
            self._socket = create_connection(url, timeout=timeout, sslopt=sslopt)
        except Exception as exc:
            raise TransportError(f"websocket connect to {_host(url)} failed: {type(exc).__name__}") from exc

    def send(self, payload: str) -> None:
        socket = self._socket
        if socket is None:
            raise TransportError("websocket not connected")
        try:
            socket.send(payload)  # type: ignore[attr-defined]
        except Exception as exc:
            raise TransportError(f"websocket send failed: {type(exc).__name__}") from exc

    def recv(self, timeout: float = 5.0) -> str | None:
        socket = self._socket
        if socket is None:
            raise TransportError("websocket not connected")
        try:
            socket.settimeout(timeout)  # type: ignore[attr-defined]
            frame = socket.recv()  # type: ignore[attr-defined]
        except Exception as exc:
            if type(exc).__name__ in {"WebSocketTimeoutException", "timeout", "TimeoutError"}:
                return None
            raise TransportError(f"websocket recv failed: {type(exc).__name__}") from exc
        if isinstance(frame, bytes):
            return frame.decode("utf-8", errors="replace")
        return frame

    def close(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is not None:
            try:
                socket.close()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - close must never raise upward
                pass


@dataclass
class RetryPolicy:
    """Exponential backoff with full jitter, capped, and never on 4xx."""

    max_attempts: int = 4
    base_s: float = 0.5
    max_s: float = 20.0
    jitter: bool = True
    sleep: object = time.sleep
    rng: random.Random = field(default_factory=lambda: random.Random(0))

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_s)
        raw = min(self.base_s * (2 ** max(0, attempt - 1)), self.max_s)
        if not self.jitter:
            return raw
        return self.rng.uniform(0.0, raw)

    def wait(self, attempt: int, retry_after: float | None = None) -> float:
        delay = self.delay_for(attempt, retry_after)
        if delay > 0:
            self.sleep(delay)  # type: ignore[operator]
        return delay


def classify_response(response: HttpResponse, *, context: str) -> None:
    """Turn an HTTP status into a typed market-data error.

    401/403 mean the credential is wrong; 402/409 and TrueData's textual
    "not subscribed" answers mean the *plan* is wrong. Conflating them would
    make a trial-scope refusal look like a bad password and trigger pointless
    credential rotation.
    """
    if response.ok:
        return
    body = (response.body or "")[:400]
    lowered = body.lower()
    if response.status in {401, 403}:
        if _mentions_entitlement(lowered):
            raise NotEntitledError(f"{context}: not entitled ({response.status}) {body}")
        raise AuthError(f"{context}: credentials rejected ({response.status})")
    if response.status == 429:
        raise RateLimitError(f"{context}: throttled by vendor", retry_after=response.retry_after())
    if response.status in {402, 409}:
        raise NotEntitledError(f"{context}: plan does not cover this request ({response.status})")
    if 400 <= response.status < 500:
        if _mentions_entitlement(lowered):
            raise NotEntitledError(f"{context}: not entitled ({response.status}) {body}")
        raise MarketDataError(f"{context}: rejected ({response.status}) {body}")
    raise UpstreamError(f"{context}: vendor error ({response.status}) {body}")


def _mentions_entitlement(lowered: str) -> bool:
    return any(
        token in lowered
        for token in (
            "not subscribed",
            "not entitled",
            "no permission",
            "not allowed for",
            "subscription",
            "upgrade",
        )
    )


def _host(url: str) -> str:
    """Log hosts, never full URLs: query strings can carry credentials."""
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0].split("?", 1)[0]
