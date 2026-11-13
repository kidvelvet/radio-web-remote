"""Binary WebSocket frame for one scope sweep (our protocol, not the radio's).

Little-endian, 24-byte header then the points:

  offset size  field
  0      1     type        0x01 = scope line
  1      1     version     1
  2      1     flags       bit 0 out of range, bit 1 fixed mode
  3      1     reserved    0
  4      2     max_value   u16, top of the level scale (IC-7300: 160)
  6      2     count       u16, number of points
  8      8     low_hz      f64 (exact for any integer below 2**53)
  16     8     high_hz     f64
  24     count points      u8 each, 0..max_value

JSON messages stay text frames, so a client tells them apart by frame type.
"""

from __future__ import annotations

import struct

from radio_web_remote.radios.base import ScopeLine

TYPE_SCOPE_LINE = 0x01
VERSION = 1
_HEADER = struct.Struct("<BBBBHHdd")
HEADER_SIZE = _HEADER.size  # 24


def encode_scope_line(line: ScopeLine) -> bytes:
    if line.max_value > 255:
        raise ValueError("points are sent as one byte each; max_value must be <= 255")
    flags = (1 if line.out_of_range else 0) | (2 if line.fixed_mode else 0)
    header = _HEADER.pack(
        TYPE_SCOPE_LINE,
        VERSION,
        flags,
        0,
        line.max_value,
        len(line.points),
        float(line.low_hz),
        float(line.high_hz),
    )
    return header + bytes(line.points)


def decode_scope_line(data: bytes) -> ScopeLine:
    """Inverse of encode_scope_line (used by tests and tools)."""
    kind, version, flags, _, max_value, count, low, high = _HEADER.unpack_from(data)
    if kind != TYPE_SCOPE_LINE or version != VERSION:
        raise ValueError("not a version 1 scope line")
    points = data[HEADER_SIZE : HEADER_SIZE + count]
    if len(points) != count:
        raise ValueError("truncated scope line")
    return ScopeLine(
        low_hz=int(low),
        high_hz=int(high),
        points=points,
        max_value=max_value,
        fixed_mode=bool(flags & 2),
        out_of_range=bool(flags & 1),
    )
