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

    @property
    def prefix(self) -> bytes:
        cmd, sub = self.value
        return bytes([cmd]) + sub


class ReplyError(ValueError):
    pass


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
