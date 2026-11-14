"""IC-9700 capability table.

Sources, all Icom's own PDFs (see docs/civ-reference.md):
- CI-V Reference Guide A7508-3EX-4 (Mar. 2023), "9700-CIV".
- Basic Manual A7508H-1EX-7 (Jan. 2026), "9700-BM", for menus and MAIN/SUB.
Page numbers are the ones printed on the page. Nothing here is copied from
another model's table.

MAIN and SUB are two receive bands. Commands 03 to 06 are assumed to act on
the selected band; the guide does not say so (TODO(verify) on hardware,
docs/hardware-test.md). Normally only MAIN transmits; in satellite mode SUB
is the uplink (9700-BM 3-2, 7-1). The TX limit check therefore reads 1C 03
rather than trusting the displayed band.
"""

from __future__ import annotations

from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.radios.base import Meter, Mode
from radio_web_remote.radios.icom.model import IcomModel, MeterCal, ScopeSpec, Sourced
from radio_web_remote.sources import IC9700_BASIC, IC9700_CIV, Source


def _src(page: str, note: str = "") -> Source:
    return Source(IC9700_CIV, page, note, verified_primary=True)


_MODE_SRC = _src("13", "Operating mode data")

IC9700 = IcomModel(
    name="IC-9700",
    driver_name="icom.ic9700",
    default_address=Sourced(0xA2, _src("2", "default address A2h; Basic Manual 8-16")),
    commands={
        CivCommand.TRANSCEIVE_FREQUENCY: _src("4, 13", "00 Send the frequency data (transceive)"),
        CivCommand.TRANSCEIVE_MODE: _src("4, 13", "01 Send the mode data (transceive)"),
        CivCommand.READ_FREQUENCY: _src("4, 13", "03 Read the operating frequency"),
        CivCommand.READ_MODE: _src("4, 13", "04 Read the operating mode"),
        CivCommand.SET_FREQUENCY: _src("4, 13", "05 Set the operating frequency"),
        CivCommand.SET_MODE: _src("4, 13", "06 Set the operating mode"),
        CivCommand.BAND_EXCHANGE: _src("4", "07 B0 exchange MAIN and SUB bands"),
        CivCommand.BAND_MAIN: _src("4", "07 D0 select the MAIN band"),
        CivCommand.BAND_SUB: _src("4", "07 D1 select the SUB band"),
        CivCommand.BAND_SELECTION: _src("4", "07 D2 + 00 MAIN / 01 SUB, send/read"),
        CivCommand.DUALWATCH: _src("5", "16 59 sub band (Dualwatch) 00 OFF / 01 ON"),
        CivCommand.SATELLITE: _src("5", "16 5A satellite mode 00 OFF / 01 ON"),
        CivCommand.TX_STATE: _src("11", "1C 00 transceiver's status, 00=RX 01=TX"),
        CivCommand.READ_TX_FREQUENCY: _src("11, 13", "1C 03 read the transmit frequency"),
        CivCommand.AF_LEVEL: _src("4", "14 01 AF level, 0000=Minimum to 0255=Maximum"),
        CivCommand.RF_GAIN: _src("4", "14 02 RF gain, 0000=Minimum to 0255=Maximum"),
        CivCommand.SQUELCH: _src("4", "14 03 squelch, 0000=Minimum to 0255=Maximum"),
        CivCommand.RF_POWER: _src("4", "14 0A RF power, 0000=Minimum to 0255=Maximum"),
        CivCommand.S_METER: _src("4", "15 02 S-meter level, read"),
        CivCommand.PO_METER: _src("4", "15 11 Po meter level, read"),
        CivCommand.SWR_METER: _src("5", "15 12 SWR meter level, read"),
        CivCommand.ALC_METER: _src("5", "15 13 ALC meter level, read"),
        CivCommand.DATA_MODE: _src("11, 18", "1A 06 DATA mode: 00/01 then filter 01-03"),
        CivCommand.SCOPE_DATA: _src("12, 25", "27 00 waveform data, byte 1 00 Main / 01 Sub"),
        CivCommand.SCOPE_ON: _src("12", "27 10 scope ON/OFF, 00/01"),
        CivCommand.SCOPE_OUTPUT: _src("12", "27 11 scope data output, 00/01"),
        CivCommand.SCOPE_BAND: _src("12", "27 12 00 Main / 01 Sub scope"),
        CivCommand.SCOPE_MODE: _src(
            "12, 25", "27 14 + 00/01 + Center 00 / Fixed 01 / SCROLL-C 02 / SCROLL-F 03"
        ),
        CivCommand.SCOPE_SPAN: _src("12, 25", "27 15 + 00/01 + 5-byte span, 2.5k to 500k"),
        CivCommand.SCOPE_EDGE: _src("12, 25", "27 16 + 00/01 + edge 01-04"),
        CivCommand.SCOPE_HOLD: _src("12, 25", "27 17 + 00/01 + 00 OFF / 01 ON"),
        CivCommand.SCOPE_REF: _src("12, 25", "27 19 + 00/01 + level, -20 to +20 dB, 0.5 dB"),
        CivCommand.SCOPE_SPEED: _src("12, 26", "27 1A + 00/01 + 00 FAST / 01 MID / 02 SLOW"),
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
        Mode.DV: Sourced(0x17, _MODE_SRC),
        # p. 13: DD only on the 1200 MHz band, and not in satellite mode.
        Mode.DD: Sourced(0x22, _MODE_SRC),
    },
    filters={
        1: Sourced(0x01, _src("13", "Filter setting FIL1")),
        2: Sourced(0x02, _src("13", "Filter setting FIL2")),
        3: Sourced(0x03, _src("13", "Filter setting FIL3")),
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
            _src("5", "0000=1.0, 0048=1.5, 0080=2.0, 0120=3.0"),
        ),
        Meter.ALC: MeterCal(
            ((0, 0.0), (120, 100.0)), "%", _src("5", "0000=Minimum to 0120=Maximum")
        ),
    },
    scope=ScopeSpec(
        points=475,
        max_value=160,
        source=_src("25", "data range 0 ~ 160, data length 475"),
        modes=("center", "fixed", "scroll-c", "scroll-f"),
        modes_source=_src("12, 25", "27 14 00 Center ~ 03 SCROLL-F"),
        edges=4,
        edges_source=_src("12, 25", "27 16 edge 01 ~ 04"),
    ),
    # p. 13: byte 5 holds the 1 GHz digit (0 ~ 1) and the 100 MHz digit (0 ~ 4).
    max_encodable_hz=1_499_999_999,
    coverage_hz=(
        (144_000_000, 148_000_000),
        (430_000_000, 450_000_000),
        (1_240_000_000, 1_300_000_000),
    ),
    coverage_source=Source(
        IC9700_BASIC, "11-1", "USA version receiver/transmitter coverage", verified_primary=True
    ),
)
