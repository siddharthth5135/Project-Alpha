"""Angel One market-data adapter unit tests (no network)."""

from __future__ import annotations

from datetime import date

import pytest

from alpha_desk.marketdata.contracts import Segment
from alpha_desk.marketdata.errors import ProviderConfigError
from alpha_desk.marketdata.registry import available_providers, get_provider
from alpha_desk.vendors.angelone.config import AngelOneConfig
from alpha_desk.vendors.angelone.symbols import parse_scrip_row


def test_angelone_registered():
    assert "angelone" in available_providers()


def test_network_gate_default_off():
    cfg = AngelOneConfig.from_env(
        {
            "ANGELONE_API_KEY": "k",
            "ANGELONE_CLIENT_CODE": "C1",
            "ANGELONE_MPIN": "1234",
            "ANGELONE_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
            "ANGELONE_ALLOW_MARKETDATA": "false",
        }
    )
    with pytest.raises(ProviderConfigError, match="DISABLED"):
        cfg.require_network_allowed()


def test_redacted_hides_secrets():
    cfg = AngelOneConfig(
        api_key="supersecretkey",
        client_code="ABCDE1234",
        mpin="9999",
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    red = cfg.redacted()
    assert "supersecret" not in str(red)
    assert red["api_key"] == "***"
    assert red["mpin"] == "***"
    assert red["totp_secret"] == "***"


def test_parse_fut_row():
    entry = parse_scrip_row(
        {
            "token": "99999",
            "symbol": "NIFTY30SEP26FUT",
            "name": "NIFTY",
            "expiry": "30SEP2026",
            "strike": "-1",
            "lotsize": "65",
            "instrumenttype": "FUTIDX",
            "exch_seg": "NFO",
            "tick_size": "0.05",
        }
    )
    assert entry is not None
    assert entry.contract.segment is Segment.FUTURES
    assert entry.contract.expiry == date(2026, 9, 30)
    assert entry.contract.vendor_token == "99999"


def test_parse_opt_row():
    entry = parse_scrip_row(
        {
            "token": "88888",
            "symbol": "NIFTY30SEP2625000CE",
            "name": "NIFTY",
            "expiry": "30SEP2026",
            "strike": "2500000",
            "lotsize": "65",
            "instrumenttype": "OPTIDX",
            "exch_seg": "NFO",
            "tick_size": "0.05",
        }
    )
    assert entry is not None
    assert entry.contract.segment is Segment.OPTIONS
    assert entry.contract.right.value == "CE"
    assert entry.contract.strike == 25000.0


def test_get_provider_refuses_without_creds(monkeypatch):
    monkeypatch.delenv("ANGELONE_API_KEY", raising=False)
    monkeypatch.delenv("ANGELONE_CLIENT_CODE", raising=False)
    monkeypatch.delenv("ANGELONE_MPIN", raising=False)
    monkeypatch.delenv("ANGELONE_TOTP_SECRET", raising=False)
    with pytest.raises(ProviderConfigError):
        get_provider("angelone", env={})
