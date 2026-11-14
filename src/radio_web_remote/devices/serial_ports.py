"""Resolve a radio's serial port from config, preferring identifiers that survive re-cabling.

Order: usb_serial, then usb_location, then a literal port name. COM numbers
and /dev/tty* names change when the cable moves, so they are the last resort
(requirement in the brief, "Facts already verified").

Observed on macOS, 2026-11-13: an IC-7300 shows USB VID:PID 10C4:EA60
(Silicon Labs CP2102) with a serial number string "IC-7300 <serial>"
(docs/hardware-test.md).

An IC-705 is one USB device with two serial ports (VID:PID 0C26:0036, both
with serial number "IC-705 <serial>" and the same location; H705-1). Only
"Serial Port A" carries CI-V; port B may stream GPS sentences (IC-705 CI-V
guide p. 2). So a serial number or location can match two ports, and the
CI-V one has to be chosen, see _pick.
"""

from __future__ import annotations

import logging
import re
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


log = logging.getLogger(__name__)
_CIV_PORT = re.compile(r"\(CI-V\)|Serial Port A\b", re.IGNORECASE)


def _pick(hits: list[PortInfo], match: SerialMatch, what: str) -> str:
    """One radio, several serial ports: choose the CI-V port."""
    if len(hits) == 1:
        return hits[0].device
    if match.port and any(p.device == match.port for p in hits):
        return match.port  # explicit choice among the radio's ports
    named = [p for p in hits if _CIV_PORT.search(p.description)]
    if len(named) == 1:
        return named[0].device
    # macOS names both IC-705 ports "IC-705"; port A sorted first there
    # (cu.usbmodem...01 vs ...03, H705-1). TODO(verify) on Linux and Windows.
    chosen = sorted(p.device for p in hits)[0]
    log.info(  # expected for an IC-705 on macOS; not worth a warning on every start
        "%s matches %d serial ports (%s); using %s. Set match.port to choose another.",
        what,
        len(hits),
        ", ".join(sorted(p.device for p in hits)),
        chosen,
    )
    return chosen


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
        hits = [p for p in ports if p.serial_number == match.usb_serial]
        if hits:
            return _pick(hits, match, f"USB serial number {match.usb_serial!r}")
        raise PortNotFound(f"no serial port with USB serial number {match.usb_serial!r}")
    if match.usb_location:
        hits = [p for p in ports if p.location == match.usb_location]
        if hits:
            return _pick(hits, match, f"USB location {match.usb_location!r}")
        raise PortNotFound(f"no serial port at USB location {match.usb_location!r}")
    if match.port:
        return match.port
    raise PortNotFound("radio has no usb_serial, usb_location or port configured")
