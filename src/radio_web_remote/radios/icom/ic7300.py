"""IC-7300 capability table.

Source for every entry: Icom IC-7300 Full Manual, section 19 Control
Command. Page numbers are the manual's own. See docs/civ-reference.md.
"""

from __future__ import annotations

from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.radios.base import Meter, Mode
from radio_web_remote.radios.icom.model import IcomModel, MeterCal, Sourced
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
        CivCommand.AF_LEVEL: _src("19-3", "14 01 AF level, 0000=min to 0255=max"),
        CivCommand.RF_GAIN: _src("19-3", "14 02 RF level, 0000=min to 0255=max"),
        CivCommand.SQUELCH: _src("19-3", "14 03 SQL level, 0000=min to 0255=max"),
        CivCommand.RF_POWER: _src("19-3", "14 0A [RF PWR] position, 0000=max CCW to 0255=max CW"),
        CivCommand.S_METER: _src("19-3", "15 02 S-meter level, read"),
        CivCommand.PO_METER: _src("19-3", "15 11 Po meter level, read"),
        CivCommand.SWR_METER: _src("19-3", "15 12 SWR meter level, read"),
        CivCommand.ALC_METER: _src("19-3", "15 13 ALC meter level, read"),
        CivCommand.DATA_MODE: _src("19-6, 19-9", "1A 06 DATA mode: 00/01 then filter 01-03"),
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
    meters={
        Meter.S: MeterCal(
            ((0, 0.0), (120, 9.0)),
            "S",
            _src("19-3", "0000=S0, 0120=S9, 0241=S9+60dB"),
            over_s9_points=((120, 0.0), (241, 60.0)),
        ),
        Meter.PO: MeterCal(
            ((0, 0.0), (143, 50.0), (213, 100.0)), "%", _src("19-3", "0000=0%, 0143=50%, 0213=100%")
        ),
        Meter.SWR: MeterCal(
            ((0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0)),
            "SWR",
            _src("19-3", "0000=1.0, 0048=1.5, 0080=2.0, 0120=3.0"),
        ),
        Meter.ALC: MeterCal(((0, 0.0), (120, 100.0)), "%", _src("19-3", "0000=Min. to 0120=Max.")),
    },
    # p. 19-8: the 100 MHz and 1000 MHz digits are fixed at 0 on the IC-7300.
    max_encodable_hz=99_999_999,
)
