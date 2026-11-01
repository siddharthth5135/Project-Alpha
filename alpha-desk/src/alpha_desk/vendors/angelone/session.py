"""Angel One login session (JWT + feed token).

Never logs secrets. Refuses to contact the network unless
``config.allow_marketdata`` is true.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pyotp
import requests

from alpha_desk.marketdata.errors import AuthError, ProviderConfigError, TransportError, UpstreamError
from alpha_desk.vendors.angelone.config import AngelOneConfig

LOGIN_PATH = "/rest/auth/angelbroking/user/v1/loginByPassword"
PROFILE_PATH = "/rest/secure/angelbroking/user/v1/getProfile"


@dataclass
class AngelSession:
    config: AngelOneConfig
    jwt_token: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)
    feed_token: str = field(default="", repr=False)
    _http: requests.Session = field(default_factory=requests.Session, repr=False)
    _logged_in_at: float = 0.0

    @property
    def is_authenticated(self) -> bool:
        return bool(self.jwt_token)

    def login(self) -> None:
        self.config.require_network_allowed()
        self.config.require_credentials()
        try:
            totp = pyotp.TOTP(self.config.totp_secret).now()
        except Exception as exc:  # noqa: BLE001 - invalid secret is config
            raise ProviderConfigError(f"invalid ANGELONE_TOTP_SECRET: {exc}") from exc

        payload = {
            "clientcode": self.config.client_code,
            "password": self.config.mpin,
            "totp": totp,
        }
        body = self._post(LOGIN_PATH, payload, authed=False)
        data = body.get("data") or {}
        jwt = data.get("jwtToken") or ""
        if jwt and not str(jwt).startswith("Bearer "):
            jwt = f"Bearer {jwt}"
        self.jwt_token = str(jwt)
        self.refresh_token = str(data.get("refreshToken") or "")
        self.feed_token = str(data.get("feedToken") or "")
        if not self.jwt_token:
            raise AuthError("Angel login returned no jwtToken")
        self._logged_in_at = time.monotonic()

    def ensure_login(self) -> None:
        if not self.is_authenticated:
            self.login()

    def auth_headers(self) -> dict[str, str]:
        self.ensure_login()
        return {
            "Authorization": self.jwt_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.config.api_key,
        }

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.config.require_network_allowed()
        url = f"{self.config.base_url}{path}"
        try:
            resp = self._http.get(
                url,
                headers=self.auth_headers(),
                params=params,
                timeout=self.config.request_timeout_s,
            )
        except requests.RequestException as exc:
            raise TransportError(f"Angel GET {path} failed: {exc}") from exc
        return self._parse(resp, path)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(path, payload, authed=True)

    def _post(self, path: str, payload: dict[str, Any], *, authed: bool) -> dict[str, Any]:
        self.config.require_network_allowed()
        url = f"{self.config.base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.config.api_key,
        }
        if authed:
            headers = self.auth_headers()
        try:
            resp = self._http.post(
                url, json=payload, headers=headers, timeout=self.config.request_timeout_s
            )
        except requests.RequestException as exc:
            raise TransportError(f"Angel POST {path} failed: {exc}") from exc
        return self._parse(resp, path)

    @staticmethod
    def _parse(resp: requests.Response, path: str) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError as exc:
            raise UpstreamError(f"Angel {path}: non-JSON status={resp.status_code}") from exc
        if resp.status_code == 401 or body.get("errorcode") in {"AG8001", "AB1000"}:
            raise AuthError(f"Angel auth failed on {path}: {body.get('message')}")
        if resp.status_code >= 500:
            raise UpstreamError(f"Angel {path} HTTP {resp.status_code}")
        if body.get("status") is False:
            msg = body.get("message") or body.get("errorcode") or "request failed"
            raise UpstreamError(f"Angel {path}: {msg}")
        return body if isinstance(body, dict) else {"data": body}
