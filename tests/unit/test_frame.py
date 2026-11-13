import pytest
from hypothesis import given
from hypothesis import strategies as st

from radio_web_remote.protocols.civ.frame import (
    COLLISION,
    END,
    MAX_FRAME_LEN,
    PREAMBLE,
    Frame,
    FrameError,
    FrameParser,
    encode_frame,
)

# Bytes allowed inside a payload: anything except FE, FD, FC.
payload_byte = st.integers(0, 0xFB)
payloads = st.binary(min_size=1, max_size=64).map(
    lambda b: bytes(x if x < 0xFC else x - 0x10 for x in b)
)
addresses = st.integers(0, 0xFB)


def test_encode_layout_matches_manual():
    # IC-7300 Full Manual p. 19-2: FE FE <to> <from> Cn Sc data FD
    assert encode_frame(0x94, 0xE0, b"\x03") == bytes.fromhex("FEFE94E003FD")


def test_encode_rejects_reserved_payload_bytes():
    for b in (END, PREAMBLE, COLLISION):
        with pytest.raises(FrameError):
            encode_frame(0x94, 0xE0, bytes([0x05, b]))
    with pytest.raises(FrameError):
        encode_frame(0x94, 0xE0, b"")
    with pytest.raises(FrameError):
        encode_frame(PREAMBLE, 0xE0, b"\x03")


def test_parse_single_frame():
    frames = FrameParser().feed(bytes.fromhex("FEFEE094FBFD"))
    assert frames == [Frame(0xE0, 0x94, b"\xfb")]
    assert frames[0].is_ok and not frames[0].is_ng


def test_parse_skips_junk_and_counts_it():
    p = FrameParser()
    frames = p.feed(b"\x00\x11" + bytes.fromhex("FEFEE094FAFD") + b"\x42")
    assert len(frames) == 1 and frames[0].is_ng
    assert p.stats.junk_bytes == 3


def test_extra_preamble_bytes_are_one_preamble():
    frames = FrameParser().feed(bytes.fromhex("FEFEFEFEE09403FD"))
    assert frames == [Frame(0xE0, 0x94, b"\x03")]


def test_single_fe_is_not_a_preamble():
    p = FrameParser()
    assert p.feed(bytes.fromhex("FEE09403FD")) == []


def test_truncated_frame_then_new_frame():
    p = FrameParser()
    # first frame loses its FD; a new preamble starts the next one
    frames = p.feed(bytes.fromhex("FEFE94E005") + bytes.fromhex("FEFEE094FBFD"))
    assert frames == [Frame(0xE0, 0x94, b"\xfb")]
    assert p.stats.short == 1


def test_collision_frame_dropped():
    p = FrameParser()
    assert p.feed(bytes.fromhex("FEFEE094FCFD")) == []
    assert p.stats.collisions == 1
    # parser recovers
    assert p.feed(bytes.fromhex("FEFEE094FBFD"))[0].is_ok


def test_too_short_frame_dropped():
    p = FrameParser()
    assert p.feed(bytes.fromhex("FEFEE094FD")) == []
    assert p.stats.short == 1


def test_oversize_frame_resyncs():
    p = FrameParser()
    junk = bytes([0x01]) * (MAX_FRAME_LEN + 10)
    assert p.feed(b"\xfe\xfe" + junk + b"\xfd") == []
    assert p.stats.oversize == 1
    assert p.feed(bytes.fromhex("FEFEE094FBFD"))[0].is_ok


@given(to=addresses, frm=addresses, payload=payloads)
def test_roundtrip(to, frm, payload):
    frames = FrameParser().feed(encode_frame(to, frm, payload))
    assert frames == [Frame(to, frm, payload)]


@given(
    items=st.lists(st.tuples(addresses, addresses, payloads), min_size=1, max_size=8),
    junk=st.lists(
        st.binary(max_size=5).map(lambda b: b.replace(b"\xfe", b"")), min_size=9, max_size=9
    ),
    cuts=st.lists(st.integers(1, 7), max_size=200),
)
def test_chunking_and_junk_do_not_change_result(items, junk, cuts):
    stream = bytearray()
    for i, (to, frm, payload) in enumerate(items):
        stream += junk[i] + encode_frame(to, frm, payload)
    stream += junk[len(items)]

    expected = [Frame(t, f, p) for t, f, p in items]
    assert FrameParser().feed(bytes(stream)) == expected

    parser, got, pos = FrameParser(), [], 0
    for n in cuts:
        got += parser.feed(bytes(stream[pos : pos + n]))
        pos += n
    got += parser.feed(bytes(stream[pos:]))
    assert got == expected


@given(st.binary(max_size=400))
def test_parser_never_raises_on_garbage(data):
    FrameParser().feed(data)
