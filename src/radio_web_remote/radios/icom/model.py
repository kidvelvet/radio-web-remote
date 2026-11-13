"""Capability table schema for Icom CI-V radios.

A table says, per model, which CI-V commands and mode codes exist and where
that is documented. Every entry needs a Source; the test suite enforces it.
Never copy an entry from one model's table into another's: if a model's own
document has not been checked, leave the entry out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.radios.base import Feature, Level, Meter, Mode
from radio_web_remote.sources import Source


@dataclass(frozen=True, slots=True)
class Sourced:
    value: int
    source: Source


LEVEL_COMMANDS: dict[Level, CivCommand] = {
    Level.AF: CivCommand.AF_LEVEL,
    Level.RF_GAIN: CivCommand.RF_GAIN,
    Level.SQUELCH: CivCommand.SQUELCH,
    Level.RF_POWER: CivCommand.RF_POWER,
}
METER_COMMANDS: dict[Meter, CivCommand] = {
    Meter.S: CivCommand.S_METER,
    Meter.PO: CivCommand.PO_METER,
    Meter.SWR: CivCommand.SWR_METER,
    Meter.ALC: CivCommand.ALC_METER,
}


@dataclass(frozen=True)
class MeterCal:
    """Calibration points (raw 0..255 -> value) exactly as the manual prints them.

    Between points the driver interpolates linearly. That is an assumption:
    the manual gives only these points, so readings between them are approximate.
    """

    points: tuple[tuple[int, float], ...]
    unit: str
    source: Source
    # S-meter only: above the last S-unit point, readings are "dB over S9".
    over_s9_points: tuple[tuple[int, float], ...] | None = None


@dataclass(frozen=True)
class ScopeSpec:
    points: int  # waveform length
    max_value: int  # top of the data range
    source: Source


@dataclass(frozen=True)
class IcomModel:
    name: str  # display name, e.g. "IC-7300"
    driver_name: str  # registry key, e.g. "icom.ic7300"
    default_address: Sourced
    commands: dict[CivCommand, Source]
    modes: dict[Mode, Sourced]
    filters: dict[int, Sourced] = field(default_factory=dict)  # 1 -> FIL1 code, ...
    meters: dict[Meter, MeterCal] = field(default_factory=dict)
    scope: ScopeSpec | None = None
    # Highest frequency the frequency encoding allows for this model. A real
    # band-edge table is P1 work; this only stops nonsense values.
    max_encodable_hz: int = 9_999_999_999

    def supports(self, command: CivCommand) -> bool:
        return command in self.commands

    @property
    def features(self) -> frozenset[Feature]:
        f: set[Feature] = set()
        if {CivCommand.READ_FREQUENCY, CivCommand.SET_FREQUENCY} <= self.commands.keys():
            f.add(Feature.FREQUENCY)
        if {CivCommand.READ_MODE, CivCommand.SET_MODE} <= self.commands.keys():
            f.add(Feature.MODE)
            if self.filters:
                f.add(Feature.FILTER)
        if CivCommand.TX_STATE in self.commands:
            f.add(Feature.PTT)
        if CivCommand.TRANSCEIVE_FREQUENCY in self.commands:
            f.add(Feature.TRANSCEIVE)
        if CivCommand.DATA_MODE in self.commands:
            f.add(Feature.DATA_MODE)
        scope_cmds = {CivCommand.SCOPE_DATA, CivCommand.SCOPE_ON, CivCommand.SCOPE_OUTPUT}
        if self.scope is not None and scope_cmds <= self.commands.keys():
            f.add(Feature.SCOPE)
        for level, cmd in LEVEL_COMMANDS.items():
            if cmd in self.commands:
                f.add(Feature(f"level_{level.value}"))
        for meter, cmd in METER_COMMANDS.items():
            if cmd in self.commands and meter in self.meters:
                f.add(Feature(f"meter_{meter.value}"))
        return frozenset(f)

    def mode_from_code(self, code: int) -> Mode | None:
        for mode, entry in self.modes.items():
            if entry.value == code:
                return mode
        return None

    def filter_from_code(self, code: int) -> int | None:
        for number, entry in self.filters.items():
            if entry.value == code:
                return number
        return None
