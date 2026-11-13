"""Driver registry: config names a radio as "<vendor>.<model>", e.g. "icom.ic7300".

Drivers register at import time. Third-party packages can later register
through a Python entry point; for now the built-in drivers are imported here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from radio_web_remote.radios.base import RadioDriver
from radio_web_remote.transport.base import ByteTransport


@dataclass(frozen=True, slots=True)
class DriverOptions:
    address: int | None = None  # protocol address; None = documented default
    controller_address: int | None = None
    response_timeout_s: float = 0.5


DriverFactory = Callable[[ByteTransport, DriverOptions], RadioDriver]

_REGISTRY: dict[str, DriverFactory] = {}


def register(name: str, factory: DriverFactory) -> None:
    if name in _REGISTRY:
        raise ValueError(f"driver {name!r} is already registered")
    _REGISTRY[name] = factory


def names() -> list[str]:
    _load_builtins()
    return sorted(_REGISTRY)


def create(
    name: str, transport: ByteTransport, options: DriverOptions | None = None
) -> RadioDriver:
    _load_builtins()
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown driver {name!r}; known: {', '.join(sorted(_REGISTRY))}") from None
    return factory(transport, options or DriverOptions())


_loaded = False


def _load_builtins() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from radio_web_remote.radios import icom  # noqa: F401  (registers icom.*)
