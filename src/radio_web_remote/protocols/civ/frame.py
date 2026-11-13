"""CI-V frame encoder and streaming parser.

Frame layout, from the Icom IC-7300 Full Manual, section 19 "Control
Command", p. 19-2 (Data format):

    FE FE <to address> <from address> <Cn> [Sc] [data] FD

Cn is the command, Sc the sub command (only some commands have one). Whether
a payload byte is a sub command or data depends on the command, so this
module treats everything between the addresses and FD as an opaque payload;
protocols/civ/commands.py gives it meaning.

The same page shows the radio's replies to a set command: FB means OK and
FA means NG (not good).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

PREAMBLE = 0xFE
END = 0xFD
OK = 0xFB
NG = 0xFA
# TODO(verify): FC as the collision/jamming code is not in the IC-7300 Full
# Manual pages read so far (19-1 to 19-10). It is treated defensively: a frame
# containing FC is dropped. No CI-V value this project encodes (BCD, scope
# points 0-160) can contain FC, so this cannot discard a valid frame we use.
COLLISION = 0xFC

# Longest frame we accept before declaring the stream corrupt and resyncing.
# IC-7300 scope divisions are far shorter; larger radios send longer frames
# (689-point models over LAN), so leave generous headroom.
MAX_FRAME_LEN = 1024


class FrameError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Frame:
    to_addr: int
    from_addr: int
    payload: bytes

    @property
    def cmd(self) -> int | None:
        return self.payload[0] if self.payload else None

    @property
    def body(self) -> bytes:
        """Payload after the command byte (sub command and data)."""
        return self.payload[1:]

    @property
    def is_ok(self) -> bool:
        return self.payload == bytes([OK])

    @property
    def is_ng(self) -> bool:
        return self.payload == bytes([NG])

    def encode(self) -> bytes:
        return encode_frame(self.to_addr, self.from_addr, self.payload)

    def __repr__(self) -> str:
        payload = self.payload.hex(" ").upper()
        return f"Frame(to={self.to_addr:02X}, from={self.from_addr:02X}, payload={payload})"


def _check_addr(name: str, value: int) -> None:
    if not 0 <= value <= 0xFF or value in (PREAMBLE, END):
        raise FrameError(f"{name} {value:#x} is not a valid CI-V address")


def encode_frame(to_addr: int, from_addr: int, payload: bytes | Iterable[int]) -> bytes:
    """Build one frame. Rejects payload bytes that would break framing."""
    _check_addr("to_addr", to_addr)
    _check_addr("from_addr", from_addr)
    body = bytes(payload)
    if not body:
        raise FrameError("payload must contain at least a command byte")
    for b in (END, PREAMBLE, COLLISION):
        if b in body:
            raise FrameError(f"payload contains reserved byte {b:02X}")
    return bytes([PREAMBLE, PREAMBLE, to_addr, from_addr]) + body + bytes([END])


@dataclass(slots=True)
class ParserStats:
    frames: int = 0
    junk_bytes: int = 0
    collisions: int = 0
    oversize: int = 0
    short: int = 0


@dataclass(slots=True)
class FrameParser:
    """Turn an arbitrary byte stream into frames.

    Feeding the same bytes in any chunking yields the same frames. Junk between
    frames is skipped and counted. A run of more than two FE bytes is accepted
    as one preamble.
    """

    stats: ParserStats = field(default_factory=ParserStats)
    _buf: bytearray = field(default_factory=bytearray)
    _preamble: int = 0  # FE bytes seen while hunting for a frame start
    _in_frame: bool = False

    def reset(self) -> None:
        self._buf.clear()
        self._preamble = 0
        self._in_frame = False

    def feed(self, data: bytes) -> list[Frame]:
        out: list[Frame] = []
        for b in data:
            if not self._in_frame:
                if b == PREAMBLE:
                    self._preamble += 1
                    continue
                if self._preamble >= 2:
                    self._in_frame = True
                    self._preamble = 0
                    # fall through: b is the first byte after the preamble
                else:
                    self.stats.junk_bytes += self._preamble + 1
                    self._preamble = 0
                    continue

            if b == END:
                frame = self._finish()
                if frame is not None:
                    out.append(frame)
                continue
            if b == PREAMBLE:
                # A new preamble inside a frame means the previous frame was
                # cut off. Drop it and start hunting again from this byte.
                self.stats.short += 1
                self.reset()
                self._preamble = 1
                continue
            self._buf.append(b)
            if len(self._buf) > MAX_FRAME_LEN:
                self.stats.oversize += 1
                self.reset()
        return out

    def _finish(self) -> Frame | None:
        buf = bytes(self._buf)
        self.reset()
        if COLLISION in buf:
            self.stats.collisions += 1
            return None
        if len(buf) < 3:  # to, from, and at least a command byte
            self.stats.short += 1
            return None
        self.stats.frames += 1
        return Frame(to_addr=buf[0], from_addr=buf[1], payload=buf[2:])
