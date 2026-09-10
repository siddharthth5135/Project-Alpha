"""TrueData bearer-token lifecycle.

The REST APIs use an OAuth2 password grant that returns a short-lived bearer
token. The manager refreshes ahead of expiry, serialises concurrent refreshes
behind a lock so a burst of requests produces one token request, and exposes
``invalidate()`` so a 401 mid-session forces exactly one re-auth instead of a
retry storm. Tokens are never logged, only their expiry.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from alpha_desk.marketdata.errors import AuthError, MarketDataError
from alpha_desk.vendors.truedata.config import TrueDataConfig
from alpha_desk.vendors.truedata.transport import (
    HttpTransport,
    RetryPolicy,
    classify_response,
)


@dataclass(frozen=True)
class Token:
    value: str
    expires_at: float
    scope: str = ""

    def is_fresh(self, now: float, margin_s: float) -> bool:
        return now + margin_s < self.expires_at

    def masked(self) -> str:
        return f"<token len={len(self.value)}>"


class TokenManager:
    def __init__(
        self,
        config: TrueDataConfig,
        transport: HttpTransport,
        *,
        retry: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._transport = transport
        self._retry = retry or RetryPolicy(
            max_attempts=config.max_retries,
            base_s=config.backoff_base_s,
            max_s=config.backoff_max_s,
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._token: Token | None = None
        self.auth_count = 0

    @property
    def token_expires_in(self) -> float | None:
        token = self._token
        if token is None:
            return None
        return max(0.0, token.expires_at - self._clock())

    def invalidate(self) -> None:
        with self._lock:
            self._token = None

    def bearer(self) -> str:
        return f"Bearer {self.access_token()}"

    def access_token(self) -> str:
        self._config.require_credentials()
        with self._lock:
            token = self._token
            if token is not None and token.is_fresh(self._clock(), self._config.token_refresh_margin_s):
                return token.value
            self._token = self._authenticate()
            return self._token.value

    def _authenticate(self) -> Token:
        payload = {
            "username": self._config.username,
            "password": self._config.password,
            "grant_type": "password",
        }
        last_error: Exception | None = None
        for attempt in range(1, max(1, self._retry.max_attempts) + 1):
            response = self._transport.request(
                "POST",
                self._config.auth_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self._config.request_timeout_s,
            )
            try:
                classify_response(response, context="truedata auth")
            except AuthError:
                raise
            except MarketDataError as exc:
                last_error = exc
                if not getattr(exc, "retryable", False) or attempt >= self._retry.max_attempts:
                    raise
                self._retry.wait(attempt)
                continue

            self.auth_count += 1
            return self._parse(response.body)

        raise AuthError(f"truedata auth failed after {self._retry.max_attempts} attempts: {last_error}")

    def _parse(self, body: str) -> Token:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AuthError("truedata auth returned a non-JSON body") from exc

        access = str(payload.get("access_token") or "").strip()
        if not access:
            error = str(payload.get("error_description") or payload.get("error") or "no access_token")
            raise AuthError(f"truedata auth rejected: {error}")

        try:
            expires_in = float(payload.get("expires_in", 0) or 0)
        except (TypeError, ValueError):
            expires_in = 0.0
        if expires_in <= 0:
            # A vendor that stops sending expires_in must not give us an
            # immortal token; assume the documented one-hour lifetime.
            expires_in = 3600.0

        return Token(
            value=access,
            expires_at=self._clock() + expires_in,
            scope=str(payload.get("scope") or ""),
        )
