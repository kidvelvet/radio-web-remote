import json
from pathlib import Path

import pytest

from radio_web_remote.protocols.civ import scope as sc
from radio_web_remote.protocols.civ.commands import ReplyError
from radio_web_remote.radios.base import ScopeLine

FIX = json.loads((Path(__file__).parents[1] / "fixtures" / "ic7300_scope.json").read_text())


def frames(hexes):
    return [bytes.fromhex(h) for h in hexes]


def test_real_sweep_reassembles():
    asm = sc.ScopeAssembler()
    out = [asm.feed(f) for f in frames(FIX["sweep_mode02"])]
    assert out[:-1] == [None] * 10
    line = out[-1]
    assert (line.low_hz, line.high_hz) == (14_224_000, 14_324_000)
    assert len(line.points) == 475 and line.max_value == 160 and line.fixed_mode
    assert asm.stats.lines == 1


@pytest.mark.parametrize(
    ("key", "low", "high", "fixed"),
    [
        ("00_center", 14_254_000, 14_354_000, False),  # center 14.304, span +/-50 kHz
        ("01_fixed", 14_100_000, 14_350_000, True),
        ("02_undocumented", 14_254_000, 14_354_000, True),
        ("03_undocumented", 14_100_000, 14_350_000, True),
    ],
)
def test_real_headers_in_each_mode(key, low, high, fixed):
    data = frames(FIX["sweep_mode02"])[1:]
    asm = sc.ScopeAssembler()
    asm.feed(bytes.fromhex(FIX["headers"][key]))
    line = None
    for f in data:
        line = asm.feed(f)
    assert (line.low_hz, line.high_hz, line.fixed_mode) == (low, high, fixed)


def test_missing_division_drops_sweep_and_recovers():
    fs = frames(FIX["sweep_mode02"])
    asm = sc.ScopeAssembler()
    for f in fs[:4] + fs[5:]:  # division 05 lost
        assert asm.feed(f) is None
    assert asm.stats.dropped == 1
    assert [asm.feed(f) for f in fs][-1] is not None


def test_sweep_without_header_is_ignored():
    asm = sc.ScopeAssembler()
    assert all(asm.feed(f) is None for f in frames(FIX["sweep_mode02"])[1:])
    assert asm.stats.lines == 0


def test_simulator_frames_match_real_layout():
    line = ScopeLine(14_254_000, 14_354_000, bytes(range(160)) * 2 + bytes(155), 160)
    built = sc.sweep_frames(line, sc.MODE_CENTER, 14_304_000, 50_000)
    real = frames(FIX["sweep_mode02"])
    assert [len(b) for b in built] == [len(r) for r in real]
    assert [b[:5] for b in built[1:]] == [r[:5] for r in real[1:]]
    assert built[0] == bytes.fromhex(FIX["headers"]["00_center"])
    asm = sc.ScopeAssembler()
    assert [asm.feed(b) for b in built][-1] == line


def test_out_of_range_sweep_has_no_points():
    line = ScopeLine(14_000_000, 14_100_000, b"", 160, True, out_of_range=True)
    asm = sc.ScopeAssembler()
    result = [asm.feed(b) for b in sc.sweep_frames(line, sc.MODE_FIXED, 0, 0)][-1]
    assert result.out_of_range and result.points == b"" and asm.stats.out_of_range == 1


def test_points_clipped_to_documented_range():
    line = ScopeLine(1_000_000, 1_100_000, bytes([200]) * 475, 160, True)
    asm = sc.ScopeAssembler()
    result = [asm.feed(b) for b in sc.sweep_frames(line, sc.MODE_FIXED, 0, 0)][-1]
    assert max(result.points) == 160


def test_bad_frames():
    with pytest.raises(ReplyError):
        sc.ScopeAssembler().feed(b"\x27\x14\x00\x02")
    with pytest.raises(ReplyError):
        sc.ScopeAssembler().feed(bytes.fromhex("2700000A11"))  # 0A is not BCD


@pytest.mark.parametrize(
    ("db", "wire"), [(0.0, "000000"), (12.5, "125000"), (-20.0, "200001"), (-0.5, "005001")]
)
def test_ref_level(db, wire):
    assert sc.encode_ref_level(db) == bytes.fromhex(wire)
    assert sc.decode_ref_level(bytes.fromhex(wire)) == db


def test_ref_level_range_and_real_reply():
    for bad in (20.5, -21, 0.3):
        with pytest.raises(ValueError):
            sc.encode_ref_level(bad)
    assert sc.decode_ref_level(bytes.fromhex(FIX["settings_replies"]["2719"])[3:]) == 0.0


def test_span_encoding_and_real_reply():
    assert sc.encode_span(50_000) == bytes.fromhex("0000050000")
    assert sc.decode_span(bytes.fromhex(FIX["settings_replies"]["2715"])[3:]) == 50_000
    with pytest.raises(ValueError):
        sc.encode_span(30_000)
