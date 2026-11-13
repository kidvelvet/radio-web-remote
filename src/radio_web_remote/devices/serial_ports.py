"""Resolve a radio's serial port from config, preferring identifiers that survive re-cabling.

Order: usb_serial, then usb_location, then a literal port name. COM numbers
and /dev/tty* names change when the cable moves, so they are the last resort
(requirement in the brief, "Facts already verified").

Observed on macOS, 2026-11-13: an IC-7300 shows USB VID:PID 10C4:EA60
(Silicon Labs CP2102) with a serial number string "IC-7300 <serial>"
(docs/hardware-test.md).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from serial.tools import list_ports

from radio_web_remote.config import SerialMatch


@dataclass(frozen=True, slots=True)
class PortInfo:
    device: str
    description: str
    serial_number: str | None
    location: str | None
    vid: int | None
    pid: int | None


class PortNotFound(LookupError):
    pass


def list_serial_ports() -> list[PortInfo]:
    ports = [
        PortInfo(
            device=p.device,
            description=p.description or "",
            serial_number=p.serial_number,
            location=p.location,
            vid=p.vid,
            pid=p.pid,
        )
        for p in list_ports.comports()
    ]
    if sys.platform == "darwin":
        # macOS lists both /dev/tty.* and /dev/cu.*; the cu device does not
        # wait for carrier detect, so it is the one to open.
        ports = [p for p in ports if not p.device.startswith("/dev/tty.")] or ports
    return ports


def resolve_port(match: SerialMatch, ports: list[PortInfo] | None = None) -> str:
    ports = list_serial_ports() if ports is None else ports
    if match.usb_serial:
        for p in ports:
            if p.serial_number == match.usb_serial:
                return p.device
        raise PortNotFound(f"no serial port with USB serial number {match.usb_serial!r}")
    if match.usb_location:
        for p in ports:
            if p.location == match.usb_location:
                return p.device
        raise PortNotFound(f"no serial port at USB location {match.usb_location!r}")
    if match.port:
        return match.port
    raise PortNotFound("radio has no usb_serial, usb_location or port configured")
