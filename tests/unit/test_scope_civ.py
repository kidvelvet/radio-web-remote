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
    results = [asm.feed(b) for b in sc.sweep_frames(line, sc.MODE_FIXED, 0, 0)]
    # The header says out of range: the line is complete at once (no waveform
    # follows), and any empty divisions the radio still sends are ignored.
    assert results[0].out_of_range and results[0].points == b""
    assert results[1:] == [None] * 10
    assert asm.stats.out_of_range == 1 and asm.stats.dropped == 0


def test_points_clipped_to_documented_range():
    line = ScopeLine(1_000_000, 1_100_000, bytes([200]) * 475, 160, True)
    asm = sc.ScopeAssembler()
    result = [asm.feed(b) for b in sc.sweep_frames(line, sc.MODE_FIXED, 0, 0)][-1]
    assert max(result.points) == 160


def test_bad_frames():
    with pytest.raises(ReplyError):
        sc.ScopeAssembler().feed(b"\x27\x14\x00\x02")
    for div in ("1F", "12", "00", "0C"):  # not 1..11 as BCD or as binary
        with pytest.raises(ReplyError):
            sc.ScopeAssembler().feed(bytes.fromhex(f"270000{div}11"))


def test_binary_division_numbers_also_accepted():
    """The IC-705 and IC-9700 guides don't say BCD or binary; both must work."""
    line = ScopeLine(14_000_000, 14_350_000, bytes(range(160)) + bytes(315), 160, True)
    bcd = sc.sweep_frames(line, sc.MODE_FIXED, 0, 0)
    binary = []
    for f in bcd:
        div = int(f"{f[3]:02X}")  # BCD byte -> number
        binary.append(f[:3] + bytes([div, 11]) + f[5:])
    for frames_ in (bcd, binary):
        asm = sc.ScopeAssembler()
        out = [asm.feed(f) for f in frames_]
        assert out[-1] is not None and out[-1].points == line.points
        assert asm.stats.dropped == 0


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


FIX705 = json.loads((Path(__file__).parents[1] / "fixtures" / "ic705_scope.json").read_text())


@pytest.mark.parametrize(
    ("mode", "low", "high"),
    [
        ("center", 7_122_000, 7_172_000),  # center 7.147, span +/-25 kHz
        ("fixed", 7_000_000, 7_300_000),
        ("scroll-c", 7_122_000, 7_172_000),  # Scroll-C header carries edges (guide rev 6 p. 28)
        ("scroll-f", 7_000_000, 7_300_000),
    ],
)
def test_real_ic705_sweeps(mode, low, high):
    """IC-705 hardware (H705-6): BCD division numbers, 9 x 50 + 25 points, like the IC-7300."""
    asm = sc.ScopeAssembler()
    out = [asm.feed(bytes.fromhex(f)) for f in FIX705["sweeps"][mode]]
    line = out[-1]
    assert line is not None and out[:-1] == [None] * 10
    assert (line.low_hz, line.high_hz, len(line.points)) == (low, high, 475)
    assert asm.stats.dropped == 0


def test_real_ic705_fourth_edge_header():
    asm = sc.ScopeAssembler()
    asm.feed(bytes.fromhex(FIX705["edge4_header"]))
    assert asm._header is not None and asm._header[1:3] == (7_000_000, 7_050_000)


FIX9700 = json.loads((Path(__file__).parents[1] / "fixtures" / "ic9700_scope.json").read_text())


@pytest.mark.parametrize(
    ("key", "low", "high"),
    [
        ("00:center", 146_020_000, 147_020_000),  # MAIN 146.52, span +/-500 kHz
        ("00:fixed", 144_000_000, 144_500_000),
        ("00:scroll-c", 146_020_000, 147_020_000),
        ("00:scroll-f", 146_500_000, 147_000_000),
        ("01:center", 445_975_000, 446_025_000),  # SUB 446.000, span +/-25 kHz
        ("01:fixed", 432_000_000, 433_000_000),
        ("01:scroll-c", 445_975_000, 446_025_000),
        ("01:scroll-f", 446_000_000, 447_000_000),
    ],
)
def test_real_ic9700_sweeps(key, low, high):
    """IC-9700 hardware (H9700-8): byte 1 is the scope band, otherwise like the IC-705."""
    frames_ = [bytes.fromhex(f) for f in FIX9700["sweeps"][key]]
    assert {f[2] for f in frames_} == {int(key[:2])}
    asm = sc.ScopeAssembler()
    line = [asm.feed(f) for f in frames_][-1]
    assert line is not None and (line.low_hz, line.high_hz, len(line.points)) == (low, high, 475)


def test_short_span_reply_is_rejected():
    """Review A3-3: a short 27 15 answer must not decode to a span the radio never had."""
    for data in (b"", bytes.fromhex("000050")):
        with pytest.raises(ReplyError):
            sc.decode_span(data)
