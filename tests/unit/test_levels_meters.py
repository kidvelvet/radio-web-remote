import pytest

from radio_web_remote.protocols.civ import commands as c
from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.radios.base import Feature, Meter
from radio_web_remote.radios.icom import IC7300
from radio_web_remote.radios.icom.civ_driver import convert_meter


@pytest.mark.parametrize(
    ("value", "wire"), [(0, "0000"), (128, "0128"), (255, "0255"), (9, "0009")]
)
def test_level_bcd_big_endian(value, wire):
    # "0000 to 0255" (p. 19-3), most significant pair first (confirmed on hardware, H7300-8).
    assert c.encode_level(value) == bytes.fromhex(wire)
    assert c.decode_level(bytes.fromhex(wire)) == value


def test_level_rejects_out_of_range_and_non_bcd():
    with pytest.raises(ValueError):
        c.encode_level(256)
    with pytest.raises(c.ReplyError):
        c.decode_level(bytes.fromhex("2801"))  # little-endian reading of 0128: > 255
    with pytest.raises(c.ReplyError):
        c.decode_level(bytes.fromhex("0A00"))


def test_level_builders():
    assert c.set_level(CivCommand.AF_LEVEL, 128) == bytes.fromhex("14010128")
    assert c.set_level(CivCommand.RF_POWER, 255) == bytes.fromhex("140A0255")
    assert c.read_value(CivCommand.S_METER) == bytes.fromhex("1502")
    assert c.parse_level(CivCommand.SWR_METER, bytes.fromhex("15120048")) == 48


def test_data_mode_builders():
    # p. 19-9: on = 01 + filter; when 00 is set, also set 00 as the filter.
    assert c.set_data_mode(True, 0x02) == bytes.fromhex("1A060102")
    assert c.set_data_mode(False, 0x02) == bytes.fromhex("1A060000")
    assert c.parse_data_mode(bytes.fromhex("1A060103")) == (True, 3)
    assert c.parse_data_mode(bytes.fromhex("1A060000")) == (False, None)


@pytest.mark.parametrize(
    ("meter", "raw", "value", "over"),
    [
        (Meter.S, 0, 0.0, None),
        (Meter.S, 120, 9.0, None),
        (Meter.S, 60, 4.5, None),  # between printed points: interpolated, approximate
        (Meter.S, 241, 9.0, 60.0),
        (Meter.S, 180, 9.0, 29.8),
        (Meter.PO, 143, 50.0, None),
        (Meter.PO, 213, 100.0, None),
        (Meter.SWR, 48, 1.5, None),
        (Meter.SWR, 120, 3.0, None),
        (Meter.SWR, 200, None, None),  # beyond the last printed point: no invented number
        (Meter.ALC, 120, 100.0, None),
    ],
)
def test_meter_calibration_points_from_manual(meter, raw, value, over):
    r = convert_meter(meter, raw, IC7300.meters[meter])
    assert r.value == value and r.db_over_s9 == over and r.raw == raw


def test_ic7300_advertises_new_features():
    f = IC7300.features
    assert {
        Feature.DATA_MODE,
        Feature.LEVEL_AF,
        Feature.LEVEL_RF_POWER,
        Feature.METER_S,
        Feature.METER_SWR,
    } <= f
