"""IC-7300 capability table.

Source for every entry: Icom IC-7300 Full Manual, section 19 Control
Command. Page numbers are the manual's own. See docs/civ-reference.md.
"""

from __future__ import annotations

from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.radios.base import Mode
from radio_web_remote.radios.icom.model import IcomModel, Sourced
from radio_web_remote.sources import IC7300_FULL_MANUAL, Source


def _src(page: str, note: str = "") -> Source:
    return Source(IC7300_FULL_MANUAL, page, note)


_MODE_SRC = _src("19-8", "Operating mode data")

IC7300 = IcomModel(
    name="IC-7300",
    driver_name="icom.ic7300",
    # p. 19-2 frame diagrams use 94h as the transceiver address. TODO(verify):
    # the menu default (SET > Connectors > CI-V > CI-V Address) in the Icom PDF.
    default_address=Sourced(0x94, _src("19-2", "frame example address")),
    commands={
        CivCommand.TRANSCEIVE_FREQUENCY: _src("19-3", "00 Send frequency data (transceive)"),
        CivCommand.TRANSCEIVE_MODE: _src("19-3", "01 Send mode data (transceive)"),
        CivCommand.READ_FREQUENCY: _src("19-3", "03 Read operating frequency"),
        CivCommand.READ_MODE: _src("19-3", "04 Read operating mode"),
        CivCommand.SET_FREQUENCY: _src("19-3", "05 Set operating frequency"),
        CivCommand.SET_MODE: _src("19-3", "06 Operating mode selection"),
        CivCommand.TX_STATE: _src("19-7", "1C 00 Send/read transceiver's status, 00=RX 01=TX"),
    },
    modes={
        Mode.LSB: Sourced(0x00, _MODE_SRC),
        Mode.USB: Sourced(0x01, _MODE_SRC),
        Mode.AM: Sourced(0x02, _MODE_SRC),
        Mode.CW: Sourced(0x03, _MODE_SRC),
        Mode.RTTY: Sourced(0x04, _MODE_SRC),
        Mode.FM: Sourced(0x05, _MODE_SRC),
        Mode.CW_R: Sourced(0x07, _MODE_SRC),
        Mode.RTTY_R: Sourced(0x08, _MODE_SRC),
    },
    filters={
        1: Sourced(0x01, _src("19-8", "Filter setting FIL1")),
        2: Sourced(0x02, _src("19-8", "Filter setting FIL2")),
        3: Sourced(0x03, _src("19-8", "Filter setting FIL3")),
    },
    # p. 19-8: the 100 MHz and 1000 MHz digits are fixed at 0 on the IC-7300.
    max_encodable_hz=99_999_999,
)
