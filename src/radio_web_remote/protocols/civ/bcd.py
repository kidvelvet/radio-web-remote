"""BCD encodings used by CI-V.

Operating frequency data, Icom IC-7300 Full Manual p. 19-8 (Data content
description, "Operating frequency", used by commands 00, 03, 05 and 1C 03):
five bytes, listed with digit labels in this order

    10 Hz, 1 Hz | 1 kHz, 100 Hz | 100 kHz, 10 kHz | 10 MHz, 1 MHz | 1000 MHz, 100 MHz

so each byte holds two decimal digits (higher digit in the high nibble) and
the byte with the 10 Hz / 1 Hz digits is sent first: little-endian by byte,
big-endian within a byte. 14.074.000 Hz is therefore 00 40 07 14 00.

TODO(verify): the manual shows this as a digit-position diagram without a
worked example. Confirm the byte order on a real IC-7300 in P1 (read the
frequency shown on the front panel and compare). Tracked in
docs/hardware-test.md.
"""

from __future__ import annotations

FREQ_BYTES = 5


class BcdError(ValueError):
    pass


def encode_bcd_le(value: int, nbytes: int) -> bytes:
    """Encode a non-negative integer as little-endian packed BCD."""
    if value < 0:
        raise BcdError("BCD value must be non-negative")
    if value >= 10 ** (2 * nbytes):
        raise BcdError(f"{value} does not fit in {nbytes} BCD bytes")
    out = bytearray()
    for _ in range(nbytes):
        value, pair = divmod(value, 100)
        tens, ones = divmod(pair, 10)
        out.append((tens << 4) | ones)
    return bytes(out)


def decode_bcd_le(data: bytes) -> int:
    """Decode little-endian packed BCD. Raises BcdError on a non-decimal nibble."""
    value = 0
    for b in reversed(data):
        hi, lo = b >> 4, b & 0x0F
        if hi > 9 or lo > 9:
            raise BcdError(f"byte {b:02X} is not BCD")
        value = value * 100 + hi * 10 + lo
    return value


def encode_frequency(hz: int) -> bytes:
    return encode_bcd_le(hz, FREQ_BYTES)


def decode_frequency(data: bytes) -> int:
    if len(data) != FREQ_BYTES:
        raise BcdError(f"frequency data must be {FREQ_BYTES} bytes, got {len(data)}")
    return decode_bcd_le(data)
