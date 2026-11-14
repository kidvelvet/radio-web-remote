import pytest

from radio_web_remote.config import SerialMatch
from radio_web_remote.devices.serial_ports import PortInfo, PortNotFound, resolve_port

PORTS = [
    PortInfo(
        "/dev/cu.usbserial-112410", "CP2102", "IC-7300 12345678", "0-1.1.2.4.1", 0x10C4, 0xEA60
    ),
    PortInfo("/dev/cu.usbserial-2", "CP2105", "IC-9700 87654321", "0-1.3", 0x10C4, 0xEA70),
]


def test_usb_serial_wins_over_port_name():
    m = SerialMatch(usb_serial="IC-7300 12345678", port="/dev/wrong")
    assert resolve_port(m, PORTS) == "/dev/cu.usbserial-112410"


def test_usb_location():
    assert resolve_port(SerialMatch(usb_location="0-1.3"), PORTS) == "/dev/cu.usbserial-2"


def test_literal_port_last_resort():
    assert resolve_port(SerialMatch(port="COM7"), PORTS) == "COM7"


def test_missing_radio_is_an_error():
    with pytest.raises(PortNotFound):
        resolve_port(SerialMatch(usb_serial="IC-705 0000"), PORTS)
    with pytest.raises(PortNotFound):
        resolve_port(SerialMatch(), PORTS)


# IC-705: one USB device, two serial ports with the same serial number (H705-1).
IC705_MAC = [
    PortInfo("/dev/cu.usbmodem1124203", "IC-705", "IC-705 00000001", "0-1.1", 0x0C26, 0x0036),
    PortInfo("/dev/cu.usbmodem1124201", "IC-705", "IC-705 00000001", "0-1.1", 0x0C26, 0x0036),
]
# Windows names as the IC-705 CI-V guide p. 2 gives them (not observed here).
IC705_WIN = [
    PortInfo("COM7", "IC-705 Serial Port B", "IC-705 00000001", "1-2", 0x0C26, 0x0036),
    PortInfo("COM6", "IC-705 Serial Port A (CI-V)", "IC-705 00000001", "1-2", 0x0C26, 0x0036),
]


def test_two_port_radio_picks_port_a_by_sort_on_macos():
    m = SerialMatch(usb_serial="IC-705 00000001")
    assert resolve_port(m, IC705_MAC) == "/dev/cu.usbmodem1124201"
    assert resolve_port(SerialMatch(usb_location="0-1.1"), IC705_MAC) == "/dev/cu.usbmodem1124201"


def test_two_port_radio_prefers_the_port_named_ci_v():
    assert resolve_port(SerialMatch(usb_serial="IC-705 00000001"), IC705_WIN) == "COM6"


def test_two_port_radio_explicit_port_among_matches():
    m = SerialMatch(usb_serial="IC-705 00000001", port="/dev/cu.usbmodem1124203")
    assert resolve_port(m, IC705_MAC) == "/dev/cu.usbmodem1124203"


def test_empty_match_uses_the_one_connected_radio_of_the_model():
    from radio_web_remote.devices.serial_ports import model_for_driver

    nine_a = PortInfo(
        "/dev/cu.usbserial-20", "CP2102N", "IC-9700 33333333 A", "0-1.3", 0x10C4, 0xEA60
    )
    nine_b = PortInfo(
        "/dev/cu.usbserial-10", "CP2102N", "IC-9700 33333333 B", "0-1.4", 0x10C4, 0xEA60
    )
    empty = SerialMatch()
    assert resolve_port(empty, [nine_b, nine_a], model_for_driver("icom.ic9700")) == nine_a.device
    assert resolve_port(empty, IC705_MAC, "IC-705") == "/dev/cu.usbmodem1124201"
    with pytest.raises(PortNotFound, match="no IC-7300 found"):
        resolve_port(empty, IC705_MAC, "IC-7300")
    two = [*PORTS, PortInfo("/dev/cu.x", "CP2102", "IC-7300 99999999", "0-2", 0x10C4, 0xEA60)]
    with pytest.raises(PortNotFound, match="2 IC-7300 radios"):
        resolve_port(empty, two, "IC-7300")
