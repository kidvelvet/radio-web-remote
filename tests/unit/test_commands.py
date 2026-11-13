import pytest

from radio_web_remote.protocols.civ import commands as c

# Expected bytes per IC-7300 Full Manual pp. 19-3, 19-7, 19-8.


def test_builders():
    assert c.read_frequency() == b"\x03"
    assert c.set_frequency(14_074_000) == bytes.fromhex("050040071400")
    assert c.read_mode() == b"\x04"
    assert c.set_mode(0x01) == b"\x06\x01"
    assert c.set_mode(0x03, 0x02) == b"\x06\x03\x02"
    assert c.read_tx_state() == b"\x1c\x00"
    assert c.set_tx_state(True) == b"\x1c\x00\x01"
    assert c.set_tx_state(False) == b"\x1c\x00\x00"


def test_parse_frequency_read_and_transceive():
    assert c.parse_frequency(bytes.fromhex("030040071400")) == 14_074_000
    assert c.parse_frequency(bytes.fromhex("000040071400")) == 14_074_000
    with pytest.raises(c.ReplyError):
        c.parse_frequency(bytes.fromhex("0500400714"))
    with pytest.raises(c.ReplyError):
        c.parse_frequency(bytes.fromhex("03004007"))


def test_parse_mode():
    assert c.parse_mode(b"\x04\x01\x02") == (0x01, 0x02)
    assert c.parse_mode(b"\x01\x03") == (0x03, None)
    with pytest.raises(c.ReplyError):
        c.parse_mode(b"\x04")
    with pytest.raises(c.ReplyError):
        c.parse_mode(b"\x06\x01")


def test_parse_tx_state():
    assert c.parse_tx_state(b"\x1c\x00\x01") is True
    assert c.parse_tx_state(b"\x1c\x00\x00") is False
    for bad in (b"\x1c\x00", b"\x1c\x00\x02", b"\x1c\x01\x00", b"\x1c\x00\x00\x00"):
        with pytest.raises(c.ReplyError):
            c.parse_tx_state(bad)


# Frames captured from a real IC-7300 on 2026-11-13 (docs/hardware-test.md,
# H7300-2 and H7300-6), transceive ON, addressed to 00.
REAL_IC7300 = [
    ("FEFE0094010001FD", "mode", (0x00, 0x01)),  # LSB FIL1
    ("FEFE0094010701FD", "mode", (0x07, 0x01)),  # CW-R FIL1
    ("FEFE0094010103FD", "mode", (0x01, 0x03)),  # USB FIL3
    ("FEFE0094001000301400FD", "freq", 14_300_010),  # payload 00 10 00 30 14 00
    ("FEFE0094000060281400FD", "freq", 14_286_000),  # payload 00 00 60 28 14 00
    ("FEFE0094003097291400FD", "freq", 14_299_730),  # payload 00 30 97 29 14 00
]


def test_real_ic7300_transceive_frames():
    from radio_web_remote.protocols.civ.frame import FrameParser

    for raw, kind, expected in REAL_IC7300:
        (frame,) = FrameParser().feed(bytes.fromhex(raw))
        assert (frame.to_addr, frame.from_addr) == (0x00, 0x94)
        if kind == "mode":
            assert c.parse_mode(frame.payload) == expected
        else:
            assert c.parse_frequency(frame.payload) == expected
