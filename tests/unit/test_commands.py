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
