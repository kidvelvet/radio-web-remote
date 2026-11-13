"""Vendor-neutral radio model. Nothing in this module knows about CI-V.

The web layer, TX controller and UI talk only to RadioDriver and these
types. A Yaesu or Kenwood driver implements the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class Mode(Enum):
    LSB = "LSB"
    USB = "USB"
    AM = "AM"
    CW = "CW"
    CW_R = "CW-R"
    RTTY = "RTTY"
    RTTY_R = "RTTY-R"
    FM = "FM"


class Feature(Enum):
    """Things a UI can offer. A driver lists only what its documents support."""

    FREQUENCY = "frequency"
    MODE = "mode"
    FILTER = "filter"
    PTT = "ptt"
    TRANSCEIVE = "transceive"  # radio pushes front-panel changes unprompted


@dataclass(frozen=True, slots=True)
class ModeSetting:
    mode: Mode
    filter: int | None = None  # 1, 2, 3 for FIL1..FIL3; None = radio default


@dataclass(frozen=True, slots=True)
class StateEvent:
    """A change reported by the radio itself (front panel, transceive)."""

    kind: str  # "frequency" | "mode" | "ptt"
    value: object


StateListener = Callable[[StateEvent], None]


class RadioError(Exception):
    pass


class RadioTimeout(RadioError):
    pass


class RadioRejected(RadioError):
    """The radio answered NG (or the vendor equivalent)."""


class Unsupported(RadioError):
    """The driver's capability table does not document this feature."""


class RadioDriver(ABC):
    def __init__(self) -> None:
        self._listeners: list[StateListener] = []

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def features(self) -> frozenset[Feature]: ...

    @property
    @abstractmethod
    def modes(self) -> tuple[Mode, ...]: ...

    @abstractmethod
    async def connect(self) -> None:
        """Open the link. Implementations must unkey before anything else."""

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def get_frequency(self) -> int: ...

    @abstractmethod
    async def set_frequency(self, hz: int) -> None: ...

    @abstractmethod
    async def get_mode(self) -> ModeSetting: ...

    @abstractmethod
    async def set_mode(self, setting: ModeSetting) -> None: ...

    @abstractmethod
    async def get_ptt(self) -> bool: ...

    @abstractmethod
    async def key(self) -> bool:
        """Request transmit. Returns False if an unkey overtook the request."""

    @abstractmethod
    async def unkey(self, verify: bool = True) -> None:
        """Stop transmitting, ahead of all queued traffic; with verify, confirm RX or raise."""

    def add_listener(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: StateListener) -> None:
        self._listeners.remove(listener)

    def _emit(self, event: StateEvent) -> None:
        for listener in list(self._listeners):
            listener(event)
