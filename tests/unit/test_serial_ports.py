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
