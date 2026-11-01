"""Provider registry.

Callers ask for a provider by name and get something satisfying the port. The
concrete adapter is imported lazily so a missing vendor dependency (or a missing
credential) can never break an unrelated import, and so tests can register a
fake under a real name.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from alpha_desk.marketdata.errors import ProviderConfigError

ProviderFactory = Callable[..., Any]

_FACTORIES: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory, *, replace: bool = False) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("provider name must be non-empty")
    if key in _FACTORIES and not replace:
        raise ProviderConfigError(f"provider {key!r} already registered")
    _FACTORIES[key] = factory


def unregister_provider(name: str) -> None:
    _FACTORIES.pop(name.strip().lower(), None)


def available_providers() -> list[str]:
    _install_builtins()
    return sorted(_FACTORIES)


def get_provider(name: str, **kwargs: Any) -> Any:
    _install_builtins()
    key = name.strip().lower()
    try:
        factory = _FACTORIES[key]
    except KeyError as exc:
        raise ProviderConfigError(
            f"unknown market-data provider {name!r}; available: {sorted(_FACTORIES)}"
        ) from exc
    return factory(**kwargs)


def _install_builtins() -> None:
    if "angelone" not in _FACTORIES:
        _FACTORIES["angelone"] = _angelone_factory
    if "truedata" not in _FACTORIES:
        _FACTORIES["truedata"] = _truedata_factory
    if "truedata_mock" not in _FACTORIES:
        _FACTORIES["truedata_mock"] = _truedata_mock_factory


def _angelone_factory(**kwargs: Any) -> Any:
    from alpha_desk.vendors.angelone.provider import AngelOneProvider

    return AngelOneProvider.from_env(**kwargs)


def _truedata_factory(**kwargs: Any) -> Any:
    from alpha_desk.vendors.truedata.provider import TrueDataProvider

    return TrueDataProvider.from_env(**kwargs)


def _truedata_mock_factory(**kwargs: Any) -> Any:
    from alpha_desk.vendors.truedata.mock import build_mock_provider

    return build_mock_provider(**kwargs)
