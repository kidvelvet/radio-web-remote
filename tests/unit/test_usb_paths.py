"""Telling radios' codecs apart by USB hub (all are named "USB Audio CODEC")."""

import os

import pytest

from radio_web_remote.audio.devices import AudioDevice, AudioDeviceError, resolve
from radio_web_remote.audio.usb_paths import (
    align,
    hub_of,
    linux_path,
    mac_location_path,
    mac_uid_path,
    serial_usb_path,
)


def test_mac_location_ids_as_paths():
    # Seen 2026-11-14: IC-7300 serial port 32-3.1 and codec 0x20340000;
    # IC-9700 ports 34-3.2, 34-3.3 and codec 0x22340000.
    assert mac_location_path(0x20340000) == "32-3.4"
    assert mac_location_path(0x22340000) == "34-3.4"
    assert mac_location_path(0x14100000) == "20-1"
    assert mac_location_path(0x14000000) == "20-"
    uid = "AppleUSBAudioEngine:Burr-Brown from TI:USB Audio CODEC:20340000:2"
    assert mac_uid_path(uid) == "32-3.4"
    assert mac_uid_path("BuiltInSpeakerDevice") is None


def test_serial_locations_and_hubs():
    assert serial_usb_path("32-3.1") == "32-3.1"  # macOS
    assert serial_usb_path("1-1.2:1.0") == "1-1.2"  # Linux: interface dropped
    assert serial_usb_path(None) is None and serial_usb_path("") is None
    assert serial_usb_path("COM3") is None
    assert hub_of("32-3.1") == hub_of("32-3.4") == "32-3"
    assert hub_of("34-3.4") != hub_of("32-3.4")
    assert hub_of("1-2") == "1-"


def test_align_survives_a_device_that_came_or_went():
    core = [
        ("iPhone Microphone", None),
        ("USB Audio CODEC", "32-3.4"),
        ("USB Audio CODEC", "32-3.4"),
        ("USB Audio CODEC", "34-3.4"),
        ("USB Audio CODEC", "34-3.4"),
        ("MacBook Pro Speakers", None),
    ]
    # PortAudio read its list before the iPhone's microphone appeared.
    stale = ["USB Audio CODEC"] * 4 + ["MacBook Pro Speakers"]
    assert align(stale, core) == ["32-3.4", "32-3.4", "34-3.4", "34-3.4", None]
    # A radio unplugged since: which codecs remain cannot be told by order alone.
    fewer = core[:3] + core[5:]
    assert align(stale, fewer)[:4] == [None] * 4


def test_linux_path_from_sysfs(tmp_path):
    usb = tmp_path / "devices" / "pci0000:00" / "usb1" / "1-1" / "1-1.4" / "1-1.4:1.0"
    usb.mkdir(parents=True)
    card = tmp_path / "class" / "sound" / "card2"
    card.mkdir(parents=True)
    os.symlink(usb, card / "device")
    assert linux_path("USB Audio CODEC: USB Audio (hw:2,0)", str(tmp_path)) == "1-1.4"
    assert linux_path("pulse", str(tmp_path)) is None
    assert linux_path("USB Audio CODEC: USB Audio (hw:9,0)", str(tmp_path)) is None


TWO_RADIOS = [
    AudioDevice(0, "iPhone Microphone", 1, 0, "Core Audio"),
    AudioDevice(1, "USB Audio CODEC", 0, 2, "Core Audio", "32-3.4"),
    AudioDevice(2, "USB Audio CODEC", 2, 0, "Core Audio", "32-3.4"),
    AudioDevice(3, "USB Audio CODEC", 0, 2, "Core Audio", "34-3.4"),
    AudioDevice(4, "USB Audio CODEC", 2, 0, "Core Audio", "34-3.4"),
]


def test_auto_picks_the_codec_on_the_radios_hub():
    assert resolve("auto", "input", TWO_RADIOS, near="32-3.1") == 2  # IC-7300
    assert resolve("auto", "output", TWO_RADIOS, near="32-3.1") == 1
    assert resolve("auto", "input", TWO_RADIOS, near="34-3.2") == 4  # IC-9700 port A
    assert resolve("auto", "output", TWO_RADIOS, near="34-3.3") == 3  # and port B
    with pytest.raises(AudioDeviceError, match="set it by index"):
        resolve("auto", "input", TWO_RADIOS)  # no serial port known: still ambiguous
    with pytest.raises(AudioDeviceError, match="set it by index"):
        resolve("auto", "input", TWO_RADIOS, near="40-1.1")  # a hub with no codec
    unknown = [AudioDevice(d.index, d.name, d.inputs, d.outputs, d.host_api) for d in TWO_RADIOS]
    with pytest.raises(AudioDeviceError, match="set it by index"):
        resolve("auto", "input", unknown, near="32-3.1")  # Windows: no paths
