import pytest

from radio_web_remote.radios.base import ScopeLine
from radio_web_remote.web.scope_wire import HEADER_SIZE, decode_scope_line, encode_scope_line


def test_roundtrip_and_layout():
    line = ScopeLine(14_000_000, 14_350_000, bytes(range(0, 161, 2)), 160, fixed_mode=True)
    frame = encode_scope_line(line)
    assert HEADER_SIZE == 24 and len(frame) == 24 + len(line.points)
    assert frame[0] == 1 and frame[1] == 1 and frame[2] == 0b10
    assert decode_scope_line(frame) == line


def test_large_frequencies_are_exact():
    line = ScopeLine(10_368_000_000, 10_368_500_000, bytes(10), 160)  # 10 GHz band
    assert decode_scope_line(encode_scope_line(line)).low_hz == 10_368_000_000


def test_rejects_wide_scale_and_truncation():
    with pytest.raises(ValueError):
        encode_scope_line(ScopeLine(1, 2, b"\x00", 300))
    with pytest.raises(ValueError):
        decode_scope_line(encode_scope_line(ScopeLine(1, 2, bytes(10), 160))[:-1])
