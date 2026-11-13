"""Capability table schema for Icom CI-V radios.

A table says, per model, which CI-V commands and mode codes exist and where
that is documented. Every entry needs a Source; the test suite enforces it.
Never copy an entry from one model's table into another's: if a model's own
document has not been checked, leave the entry out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.radios.base import Feature, Mode
from radio_web_remote.sources import Source


@dataclass(frozen=True, slots=True)
class Sourced:
    value: int
    source: Source


@dataclass(frozen=True)
class IcomModel:
    name: str  # display name, e.g. "IC-7300"
    driver_name: str  # registry key, e.g. "icom.ic7300"
    default_address: Sourced
    commands: dict[CivCommand, Source]
    modes: dict[Mode, Sourced]
    filters: dict[int, Sourced] = field(default_factory=dict)  # 1 -> FIL1 code, ...
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
