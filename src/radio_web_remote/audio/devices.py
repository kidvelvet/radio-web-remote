"""Find audio devices by config reference: "auto", a name substring, or an index.

A reference made only of digits is an index, quoted or not: YAML reads
`input_device: "3"` as a string, and as a name it would match any device with
a 3 in its name ("CalDigit Thunderbolt 3 Audio", seen 2026-11-14).

"auto" means the only device for that direction whose name contains
"USB Audio CODEC" (PortAudio decorates it on Linux, e.g. "USB Audio CODEC:
USB Audio (hw:2,0)", and Windows, e.g. "Microphone (USB Audio CODEC)"; TODO(verify)
on those systems). Windows lists each device once per host API, so several
matches are narrowed to the default host API before being called ambiguous.
The IC-7300 codec appears under that name on macOS (observed 2026-11-13,
manufacturer "Burr-Brown from TI"), and the brief notes Windows shows a
generic "USB Audio CODEC" too, so names alone cannot tell two radios apart.
With several radios on one host, set the device explicitly.
TODO(verify): match codec to serial port by USB topology (planned for doctor, P5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CODEC_NAME = "USB Audio CODEC"
Direction = Literal["input", "output"]


class AudioDeviceError(LookupError):
    pass


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    inputs: int
    outputs: int
    host_api: str


def list_devices(backend: Any = None) -> list[AudioDevice]:
    if backend is None:
        import sounddevice

        backend = sounddevice
    sd = backend
    apis = sd.query_hostapis()
    return [
        AudioDevice(
            i,
            d["name"],
            d["max_input_channels"],
            d["max_output_channels"],
            apis[d["hostapi"]]["name"],
        )
        for i, d in enumerate(sd.query_devices())
    ]


def _usable(d: AudioDevice, direction: Direction) -> bool:
    return (d.inputs if direction == "input" else d.outputs) > 0


def resolve(
    ref: str | int | None,
    direction: Direction,
    devices: list[AudioDevice],
    preferred_api: str | None = None,
) -> int | None:
    if ref is None:
        return None
    candidates = [d for d in devices if _usable(d, direction)]
    if isinstance(ref, str) and ref.strip().isdecimal():
        ref = int(ref.strip())
    if isinstance(ref, int):
        if any(d.index == ref for d in candidates):
            return ref
        raise AudioDeviceError(f"no {direction} device with index {ref}")
    if ref == "auto":
        hits = [d for d in candidates if CODEC_NAME.lower() in d.name.lower()]
        what = f'"{CODEC_NAME}"'
    else:
        hits = [d for d in candidates if ref.lower() in d.name.lower()]
        what = repr(ref)
    if len(hits) > 1 and preferred_api:
        hits = [d for d in hits if d.host_api == preferred_api] or hits
    if len(hits) == 1:
        return hits[0].index
    names = ", ".join(f"{d.index}: {d.name}" for d in candidates) or "none"
    if not hits:
        raise AudioDeviceError(
            f"no {direction} device matching {what}. {direction} devices: {names}"
        )
    raise AudioDeviceError(
        f"{len(hits)} {direction} devices match {what}; set it by index. "
        f"{direction} devices: {names}"
    )
