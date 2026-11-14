"""IC-705 capability table.

Sources, all Icom's own PDFs (see docs/civ-reference.md):
- CI-V Reference Guide A7560-8EX-6 (Jan. 2023), "705-CIV".
- Basic Manual A7560D-1EX-7 (Sep. 2023), "705-BM", for menu items.
Page numbers are the ones printed on the page. Nothing here is copied from
the IC-7300 table: each entry was read in the IC-705 documents.
"""

from __future__ import annotations

from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.radios.base import Meter, Mode
from radio_web_remote.radios.icom.model import IcomModel, MeterCal, ScopeSpec, Sourced
from radio_web_remote.sources import IC705_CIV, Source


def _src(page: str, note: str = "") -> Source:
    return Source(IC705_CIV, page, note, verified_primary=True)


_MODE_SRC = _src("17, 27", "Operating mode data")

IC705 = IcomModel(
    name="IC-705",
    driver_name="icom.ic705",
    default_address=Sourced(0xA4, _src("2", "transceiver's default address A4; Basic Manual 8-16")),
    commands={
        CivCommand.TRANSCEIVE_FREQUENCY: _src("3, 17", "00 Send the frequency data (transceive)"),
        CivCommand.TRANSCEIVE_MODE: _src("3, 17", "01 Send the mode data (transceive)"),
        CivCommand.READ_FREQUENCY: _src("3, 17", "03 Read the operating frequency"),
        CivCommand.READ_MODE: _src("3, 17", "04 Read the operating mode"),
        CivCommand.SET_FREQUENCY: _src("3, 17", "05 Set the operating frequency"),
        CivCommand.SET_MODE: _src("3, 17", "06 Set the operating mode"),
        CivCommand.TX_STATE: _src("14", "1C 00 transceiver's status, 00=RX 01=TX"),
        CivCommand.READ_TX_FREQUENCY: _src("14, 17", "1C 03 read the TX frequency"),
        CivCommand.AF_LEVEL: _src("3", "14 01 AF level, 0000=Minimum ~ 0255=Maximum"),
        CivCommand.RF_GAIN: _src("3", "14 02 RF gain, 0000=Minimum ~ 0255=Maximum"),
        CivCommand.SQUELCH: _src("3", "14 03 squelch, 0000=Minimum ~ 0255=Maximum"),
        CivCommand.RF_POWER: _src("4", "14 0A selected band's RF power, 0000=Min ~ 0255=Max"),
        CivCommand.S_METER: _src("4", "15 02 S-meter level, read"),
        CivCommand.PO_METER: _src("4", "15 11 Po meter level, read"),
        CivCommand.SWR_METER: _src("4", "15 12 SWR meter level, read"),
        CivCommand.ALC_METER: _src("4", "15 13 ALC meter level, read"),
        CivCommand.DATA_MODE: _src("14, 22", "1A 06 DATA mode: 00/01 then filter 01-03"),
        CivCommand.SCOPE_DATA: _src("15, 28", "27 00 waveform data, 11 divisions over USB"),
        CivCommand.SCOPE_ON: _src("15", "27 10 scope ON/OFF, 00/01"),
        CivCommand.SCOPE_OUTPUT: _src("15", "27 11 scope data output, 00/01"),
        CivCommand.SCOPE_MODE: _src(
            "15", "27 14 00 + 00 Center / 01 FIX / 02 SCROLL-C / 03 SCROLL-F"
        ),
        CivCommand.SCOPE_SPAN: _src("15, 29", "27 15 00 + 5-byte span, 2.5k to 500k"),
        CivCommand.SCOPE_EDGE: _src("15, 29", "27 16 00 + edge 01-04"),
        CivCommand.SCOPE_HOLD: _src("16", "27 17 00 + 00 OFF / 01 ON"),
        CivCommand.SCOPE_REF: _src("16, 29", "27 19 00 + level, -20 to +20 dB, 0.5 dB steps"),
        CivCommand.SCOPE_SPEED: _src("16", "27 1A 00 + 00 FAST / 01 MID / 02 SLOW"),
    },
    modes={
        Mode.LSB: Sourced(0x00, _MODE_SRC),
        Mode.USB: Sourced(0x01, _MODE_SRC),
        Mode.AM: Sourced(0x02, _MODE_SRC),
        Mode.CW: Sourced(0x03, _MODE_SRC),
        Mode.RTTY: Sourced(0x04, _MODE_SRC),
        Mode.FM: Sourced(0x05, _MODE_SRC),
        Mode.WFM: Sourced(0x06, _MODE_SRC),
        Mode.CW_R: Sourced(0x07, _MODE_SRC),
        Mode.RTTY_R: Sourced(0x08, _MODE_SRC),
        Mode.DV: Sourced(0x17, _MODE_SRC),
    },
    filters={
        1: Sourced(0x01, _src("17, 27", "Filter setting FIL1")),
        2: Sourced(0x02, _src("17, 27", "Filter setting FIL2")),
        3: Sourced(0x03, _src("17, 27", "Filter setting FIL3")),
    },
    meters={
        Meter.S: MeterCal(
            ((0, 0.0), (120, 9.0)),
            "S",
            _src("4", "0000=S0, 0120=S9, 0241=S9+60dB"),
            over_s9_points=((120, 0.0), (241, 60.0)),
        ),
        Meter.PO: MeterCal(
            ((0, 0.0), (143, 50.0), (213, 100.0)), "%", _src("4", "0000=0%, 0143=50%, 0213=100%")
        ),
        Meter.SWR: MeterCal(
            ((0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0)),
            "SWR",
            _src("4", "0000=SWR1.0, 0048=SWR1.5, 0080=SWR2.0, 0120=SWR3.0"),
        ),
        Meter.ALC: MeterCal(
            ((0, 0.0), (120, 100.0)), "%", _src("4", "0000=Minimum ~ 0120=Maximum")
        ),
    },
    scope=ScopeSpec(
        points=475,
        max_value=160,
        source=_src("28", "data range 0 ~ 160, data length 475"),
        modes=("center", "fixed", "scroll-c", "scroll-f"),
        modes_source=_src("15, 28", "27 14 0000 ~ 0003"),
        edges=4,
        edges_source=_src("15, 29", "27 16 edge number 01=1 ~ 04=4"),
    ),
    # p. 17: the 1 GHz digit is fixed at 0 and the 100 MHz digit runs 0 ~ 4.
    max_encodable_hz=499_999_999,
)
