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
    DATA_MODE = "data_mode"
    LEVEL_AF = "level_af"
    LEVEL_RF_GAIN = "level_rf_gain"
    LEVEL_SQUELCH = "level_squelch"
    LEVEL_RF_POWER = "level_rf_power"
    METER_S = "meter_s"
    METER_PO = "meter_po"
    METER_SWR = "meter_swr"
    METER_ALC = "meter_alc"
    SCOPE = "scope"


class Level(Enum):
    """Knob-style controls, as 0..255 of the radio's own range."""

    AF = "af"
    RF_GAIN = "rf_gain"
    SQUELCH = "squelch"
    RF_POWER = "rf_power"  # position of the RF power control, not watts


class Meter(Enum):
    S = "s"
    PO = "po"
    SWR = "swr"
    ALC = "alc"


@dataclass(frozen=True, slots=True)
class MeterReading:
    meter: Meter
    raw: int  # 0..255 as the radio reports it
    value: float | None  # in `unit`, from the documented calibration points
    unit: str  # "S" (S units; above S9 see db_over_s9), "%", "SWR"
    db_over_s9: float | None = None


@dataclass(frozen=True, slots=True)
class ModeSetting:
    mode: Mode
    filter: int | None = None  # 1, 2, 3 for FIL1..FIL3; None = radio default


@dataclass(frozen=True, slots=True)
class StateEvent:
    """A change reported by the radio itself (front panel, transceive, scope)."""

    kind: str  # "frequency" | "mode" | "ptt" | "scope"
    value: object


@dataclass(frozen=True, slots=True)
class ScopeLine:
    """One complete sweep of the radio's spectrum scope, vendor-neutral.

    points[i] is the level at low_hz + i * (high_hz - low_hz) / (len(points) - 1),
    from 0 up to max_value (the radio's own scale, e.g. 0..160 on the IC-7300).
    """

    low_hz: int
    high_hz: int
    points: bytes
    max_value: int
    fixed_mode: bool = False  # False: centered on the operating frequency
    out_of_range: bool = False


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

    # Optional controls: drivers override what their documents support.
    async def get_level(self, level: Level) -> int:
        raise Unsupported(f"{self.model_name} has no documented {level.value} level")

    async def set_level(self, level: Level, value: int) -> None:
        raise Unsupported(f"{self.model_name} has no documented {level.value} level")

    async def get_meter(self, meter: Meter) -> MeterReading:
        raise Unsupported(f"{self.model_name} has no documented {meter.value} meter")

    async def get_data_mode(self) -> bool:
        raise Unsupported(f"{self.model_name} has no documented DATA mode")

    async def set_scope_output(self, on: bool) -> None:
        """Turn the scope and its data output on or off; lines arrive as StateEvent("scope")."""
        raise Unsupported(f"{self.model_name} has no documented scope output")

    def scope_settings_supported(self) -> list[str]:
        return []

    async def get_scope_setting(self, name: str) -> object:
        raise Unsupported(f"{self.model_name} has no documented scope setting {name}")

    async def set_scope_setting(self, name: str, value: object) -> None:
        raise Unsupported(f"{self.model_name} has no documented scope setting {name}")

    async def set_data_mode(self, on: bool, filter: int | None = None) -> None:
        raise Unsupported(f"{self.model_name} has no documented DATA mode")

    def add_listener(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: StateListener) -> None:
        self._listeners.remove(listener)

    def _emit(self, event: StateEvent) -> None:
        for listener in list(self._listeners):
            listener(event)
