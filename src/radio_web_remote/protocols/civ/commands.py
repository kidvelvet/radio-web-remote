"""CI-V command builders and reply parsers.

These assemble bytes only. Whether a radio model supports a command, and the
document that says so, lives in that model's capability table
(radios/icom/*.py). A driver refuses to send a command its model's table
does not list.

Command numbers below are from the Icom IC-7300 Full Manual, p. 19-3
(Command table), p. 19-7 (1C 00) and p. 19-8 (Data content description).
Other models cite their own documents in their tables.
"""

from __future__ import annotations

from enum import Enum

from radio_web_remote.protocols.civ.bcd import FREQ_BYTES, decode_frequency, encode_frequency


class CivCommand(Enum):
    """Command identities, as (command byte, sub command bytes)."""

    TRANSCEIVE_FREQUENCY = (0x00, b"")  # radio -> controller, unsolicited
    TRANSCEIVE_MODE = (0x01, b"")  # radio -> controller, unsolicited
    READ_FREQUENCY = (0x03, b"")
    READ_MODE = (0x04, b"")
    SET_FREQUENCY = (0x05, b"")
    SET_MODE = (0x06, b"")
    TX_STATE = (0x1C, b"\x00")  # read with no data, set with 00 (RX) / 01 (TX)
    AF_LEVEL = (0x14, b"\x01")
    RF_GAIN = (0x14, b"\x02")
    SQUELCH = (0x14, b"\x03")
    RF_POWER = (0x14, b"\x0a")
    S_METER = (0x15, b"\x02")
    PO_METER = (0x15, b"\x11")
    SWR_METER = (0x15, b"\x12")
    ALC_METER = (0x15, b"\x13")
    DATA_MODE = (0x1A, b"\x06")  # data: on/off, then filter (p. 19-9)

    @property
    def prefix(self) -> bytes:
        cmd, sub = self.value
        return bytes([cmd]) + sub


class ReplyError(ValueError):
    pass


# Level and meter data are printed as four digits, "0000 to 0255" (p. 19-3),
# and data is BCD (p. 19-2), so two BCD bytes, most-significant pair first.
# The manual does not say the byte order in words; confirmed on an IC-7300 on
# 2026-11-13 (docs/hardware-test.md, H7300-8): RF gain at maximum read 02 55,
# and RF power read 00 39 (15%) with the radio set to about 15 W.
LEVEL_MAX = 255


def encode_level(value: int) -> bytes:
    if not 0 <= value <= LEVEL_MAX:
        raise ValueError(f"level {value} outside 0..{LEVEL_MAX}")
    hi, lo = divmod(value, 100)
    return bytes([((hi // 10) << 4) | (hi % 10), ((lo // 10) << 4) | (lo % 10)])


def decode_level(data: bytes) -> int:
    if len(data) != 2:
        raise ReplyError(f"level data must be 2 bytes, got {len(data)}")
    digits = []
    for b in data:
        hi, lo = b >> 4, b & 0x0F
        if hi > 9 or lo > 9:
            raise ReplyError(f"level byte {b:02X} is not BCD")
        digits += [hi, lo]
    value = digits[0] * 1000 + digits[1] * 100 + digits[2] * 10 + digits[3]
    if value > LEVEL_MAX:
        raise ReplyError(f"level {value} above {LEVEL_MAX}: byte order assumption may be wrong")
    return value


def read_value(command: CivCommand) -> bytes:
    return command.prefix


def set_level(command: CivCommand, value: int) -> bytes:
    return command.prefix + encode_level(value)


def parse_level(command: CivCommand, payload: bytes) -> int:
    if not payload.startswith(command.prefix):
        raise ReplyError(f"not a {command.name} reply: {payload.hex(' ')}")
    return decode_level(payload[len(command.prefix) :])


def set_data_mode(on: bool, filter_code: int | None) -> bytes:
    """1A 06: on/off, then filter. Off must send 00 as the filter too (p. 19-9 note)."""
    if not on:
        return CivCommand.DATA_MODE.prefix + b"\x00\x00"
    return CivCommand.DATA_MODE.prefix + bytes([0x01, filter_code or 0x01])


def parse_data_mode(payload: bytes) -> tuple[bool, int | None]:
    prefix = CivCommand.DATA_MODE.prefix
    if not payload.startswith(prefix):
        raise ReplyError(f"not a DATA mode reply: {payload.hex(' ')}")
    data = payload[len(prefix) :]
    if len(data) not in (1, 2) or data[0] not in (0, 1):
        raise ReplyError(f"bad DATA mode data {data.hex(' ')}")
    return data[0] == 1, (data[1] if len(data) == 2 and data[1] else None)


def read_frequency() -> bytes:
    return CivCommand.READ_FREQUENCY.prefix


def set_frequency(hz: int) -> bytes:
    return CivCommand.SET_FREQUENCY.prefix + encode_frequency(hz)


def read_mode() -> bytes:
    return CivCommand.READ_MODE.prefix


def set_mode(mode_code: int, filter_code: int | None = None) -> bytes:
    """Mode byte, then optional filter byte (p. 19-8: if omitted, the mode's default filter)."""
    data = bytes([mode_code]) if filter_code is None else bytes([mode_code, filter_code])
    return CivCommand.SET_MODE.prefix + data


def read_tx_state() -> bytes:
    return CivCommand.TX_STATE.prefix


def set_tx_state(transmit: bool) -> bytes:
    """1C 00 with data 01 = TX, 00 = RX (p. 19-7)."""
    return CivCommand.TX_STATE.prefix + (b"\x01" if transmit else b"\x00")


def parse_frequency(payload: bytes) -> int:
    """Parse a 00 (transceive) or 03 (read) reply payload into Hz."""
    if payload[:1] not in (b"\x00", b"\x03"):
        raise ReplyError(f"not a frequency reply: {payload.hex(' ')}")
    data = payload[1:]
    if len(data) != FREQ_BYTES:
        raise ReplyError(f"frequency reply has {len(data)} data bytes, expected {FREQ_BYTES}")
    return decode_frequency(data)


def parse_mode(payload: bytes) -> tuple[int, int | None]:
    """Parse a 01 (transceive) or 04 (read) reply into (mode code, filter code or None)."""
    if payload[:1] not in (b"\x01", b"\x04"):
        raise ReplyError(f"not a mode reply: {payload.hex(' ')}")
    data = payload[1:]
    if len(data) == 1:
        return data[0], None
    if len(data) == 2:
        return data[0], data[1]
    raise ReplyError(f"mode reply has {len(data)} data bytes")


def parse_tx_state(payload: bytes) -> bool:
    prefix = CivCommand.TX_STATE.prefix
    if not payload.startswith(prefix) or len(payload) != len(prefix) + 1:
        raise ReplyError(f"not a TX state reply: {payload.hex(' ')}")
    value = payload[-1]
    if value not in (0, 1):
        raise ReplyError(f"TX state data {value:02X} is not 00 or 01")
    return value == 1
