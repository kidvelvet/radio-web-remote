import pytest
from hypothesis import given
from hypothesis import strategies as st

from radio_web_remote.protocols.civ.bcd import (
    BcdError,
    decode_bcd_le,
    decode_frequency,
    encode_bcd_le,
    encode_frequency,
)


@pytest.mark.parametrize(
    ("hz", "wire"),
    [
        # Digit layout from IC-7300 Full Manual p. 19-8: 10 Hz/1 Hz byte first.
        (14_074_000, "0040071400"),
        (7_000_000, "0000000700"),
        (50_313_000, "0030315000"),
        (1_234_567_890, "9078563412"),
        (0, "0000000000"),
    ],
)
def test_known_frequencies(hz, wire):
    assert encode_frequency(hz) == bytes.fromhex(wire)
    assert decode_frequency(bytes.fromhex(wire)) == hz


@given(st.integers(0, 9_999_999_999))
def test_frequency_roundtrip(hz):
    assert decode_frequency(encode_frequency(hz)) == hz


@given(st.integers(0, 10**8 - 1), st.integers(1, 4))
def test_generic_roundtrip(value, nbytes):
    value %= 10 ** (2 * nbytes)
    assert decode_bcd_le(encode_bcd_le(value, nbytes)) == value


def test_rejects_out_of_range():
    with pytest.raises(BcdError):
        encode_frequency(10_000_000_000)
    with pytest.raises(BcdError):
        encode_frequency(-1)


def test_rejects_non_decimal_nibbles():
    with pytest.raises(BcdError):
        decode_frequency(bytes.fromhex("0A40071400"))
    with pytest.raises(BcdError):
        decode_frequency(bytes.fromhex("00400714A0"))


def test_rejects_wrong_length():
    with pytest.raises(BcdError):
        decode_frequency(bytes.fromhex("00400714"))
