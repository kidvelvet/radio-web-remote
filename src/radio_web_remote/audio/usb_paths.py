"""Where each audio device sits on USB, so a radio's codec can be told from another's.

Every Icom radio here has a USB hub inside: its CI-V serial port(s) and its
"USB Audio CODEC" hang off that one hub (seen 2026-11-14 on macOS: IC-7300
serial port at 32-3.1, its codec at 32-3.4; IC-9700 ports at 34-3.2 and 34-3.3,
codec at 34-3.4). All codecs have the same name, so with two radios "auto"
picks the codec on the same hub as the radio's serial port.

A USB path is "<bus>-<port>.<port>...", the form pyserial gives as a serial
port's location (on Linux followed by ":<config>.<interface>", dropped here).

macOS: Core Audio device UIDs end in the USB location ID
("AppleUSBAudioEngine:...:USB Audio CODEC:20340000:2"). PortAudio lists the
same devices in Core Audio's order, but its list is read once and can be stale,
so the two lists are aligned by name before any path is trusted.
Linux: an ALSA device name ends in "(hw:<card>,<device>)", and
/sys/class/sound/card<N>/device leads to the USB interface.
Windows: not available; set each radio's devices by index or name.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import difflib
import os
import re
import struct
import sys
from collections.abc import Sequence

_MAC_UID = re.compile(r"^AppleUSBAudioEngine:.*:([0-9A-Fa-f]{8}):\d+$")
_ALSA_CARD = re.compile(r"\(hw:(\d+),\d+\)")
_USB_DEVICE_DIR = re.compile(r"^\d+-\d+(\.\d+)*$")


def hub_of(path: str) -> str:
    """The hub a USB path hangs off: "32-3.4" -> "32-3"; "1-2" -> "1-"."""
    return path.rsplit(".", 1)[0] if "." in path else path.split("-", 1)[0] + "-"


def serial_usb_path(location: str | None) -> str | None:
    """pyserial's location ("1-1.2:1.0" on Linux, "32-3.1" on macOS) as a USB path."""
    if not location:
        return None
    path = location.split(":", 1)[0]
    return path if _USB_DEVICE_DIR.match(path) else None


def mac_location_path(location_id: int) -> str:
    """A macOS USB location ID as a path: 0x20340000 -> "32-3.4".

    The top byte is the bus; each following hex digit is one port, until a 0.
    """
    bus = location_id >> 24
    ports = []
    for shift in range(20, -1, -4):
        port = (location_id >> shift) & 0xF
        if port == 0:
            break
        ports.append(str(port))
    return f"{bus}-{'.'.join(ports)}" if ports else f"{bus}-"


def mac_uid_path(uid: str) -> str | None:
    m = _MAC_UID.match(uid)
    return mac_location_path(int(m.group(1), 16)) if m else None


def align(
    portaudio_names: Sequence[str], other: Sequence[tuple[str, str | None]]
) -> list[str | None]:
    """Give each PortAudio device the path of the same device in another list.

    Matched on name order: only runs of names that agree get a path, so a
    device that came or went since PortAudio read its list cannot shift the
    others onto the wrong path.
    """
    paths: list[str | None] = [None] * len(portaudio_names)
    names = [n for n, _ in other]
    # Same-named devices (every radio's codec) can only be told apart by order,
    # so a name whose count changed (a radio plugged in or out) gets no path.
    same = {n for n in set(names) if names.count(n) == list(portaudio_names).count(n)}
    sm = difflib.SequenceMatcher(a=list(portaudio_names), b=names, autojunk=False)
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            if names[block.b + k] in same:
                paths[block.a + k] = other[block.b + k][1]
    return paths


def _mac_devices() -> list[tuple[str, str | None]]:
    """(name, USB path) of every Core Audio device, in Core Audio's order."""
    ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio") or "CoreAudio")
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation") or "CoreFoundation")
    cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_uint32,
    ]
    cf.CFRelease.argtypes = [ctypes.c_void_p]

    class Address(ctypes.Structure):
        _fields_ = [
            ("selector", ctypes.c_uint32),
            ("scope", ctypes.c_uint32),
            ("element", ctypes.c_uint32),
        ]

    def code(s: str) -> int:
        return int(struct.unpack(">I", s.encode())[0])

    def text(device: int, selector: str) -> str:
        addr = Address(code(selector), code("glob"), 0)
        ref = ctypes.c_void_p()
        size = ctypes.c_uint32(ctypes.sizeof(ref))
        if ca.AudioObjectGetPropertyData(
            device, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(ref)
        ):
            return ""
        buf = ctypes.create_string_buffer(1024)
        ok = cf.CFStringGetCString(ref, buf, len(buf), 0x08000100)  # UTF-8
        cf.CFRelease(ref)
        return buf.value.decode("utf-8", "replace") if ok else ""

    addr = Address(code("dev#"), code("glob"), 0)
    size = ctypes.c_uint32()
    if ca.AudioObjectGetPropertyDataSize(1, ctypes.byref(addr), 0, None, ctypes.byref(size)):
        return []
    ids = (ctypes.c_uint32 * (size.value // 4))()
    if ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), ids):
        return []
    return [(text(d, "lnam"), mac_uid_path(text(d, "uid "))) for d in ids]


def linux_path(name: str, sysfs: str = "/sys") -> str | None:
    """USB path of an ALSA device named "... (hw:2,0)", from sysfs."""
    m = _ALSA_CARD.search(name)
    if not m:
        return None
    real = os.path.realpath(os.path.join(sysfs, "class", "sound", f"card{m.group(1)}", "device"))
    for part in reversed(real.split(os.sep)):  # ".../usb1/1-1/1-1.4/1-1.4:1.0"
        if _USB_DEVICE_DIR.match(part):
            return part
    return None


def usb_paths(portaudio_names: Sequence[str]) -> list[str | None]:
    """USB path of each PortAudio device, None where unknown. Never raises."""
    with contextlib.suppress(Exception):
        if sys.platform == "darwin":
            return align(portaudio_names, _mac_devices())
        if sys.platform.startswith("linux"):
            return [linux_path(n) for n in portaudio_names]
    return [None] * len(portaudio_names)
