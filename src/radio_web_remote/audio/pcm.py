"""PCM helpers and the audio wire format. Pure Python, no numpy.

Network audio is 16-bit signed little-endian mono at NET_RATE (16 kHz):
plenty for SSB and AM voice (under 3 kHz audio), 256 kbit/s per direction.

Binary WebSocket frame (12-byte header, little-endian):

  offset size  field
  0      1     type      0x02 = receive audio (server -> client)
                         0x03 = transmit audio (client -> server)
  1      1     version   1
  2      1     flags     0
  3      1     reserved  0
  4      4     seq       u32, increments per frame (gaps mean dropped frames)
  8      2     rate      u16, samples per second (16000)
  10     2     count     u16, number of samples
  12     2*count         int16 samples
"""

from __future__ import annotations

import math
import struct
import sys
from array import array

NET_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = NET_RATE * FRAME_MS // 1000  # 320
TYPE_RX_AUDIO = 0x02
TYPE_TX_AUDIO = 0x03
VERSION = 1
_HEADER = struct.Struct("<BBBBIHH")
HEADER_SIZE = _HEADER.size  # 12
MAX_FRAME_SAMPLES = 4800  # 300 ms at 16 kHz: anything larger is refused


class AudioFrameError(ValueError):
    pass


def _as_samples(pcm: bytes) -> array[int]:
    a = array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _to_bytes(a: array[int]) -> bytes:
    if sys.byteorder == "big":
        a = array("h", a)
        a.byteswap()
    return a.tobytes()


def encode_frame(kind: int, seq: int, pcm: bytes, rate: int = NET_RATE) -> bytes:
    count = len(pcm) // 2
    return _HEADER.pack(kind, VERSION, 0, 0, seq & 0xFFFFFFFF, rate, count) + pcm[: count * 2]


def decode_frame(data: bytes, expect_kind: int) -> tuple[int, int, bytes]:
    """Return (seq, rate, pcm) or raise AudioFrameError."""
    if len(data) < HEADER_SIZE:
        raise AudioFrameError("short audio frame")
    kind, version, _flags, _r, seq, rate, count = _HEADER.unpack_from(data)
    if kind != expect_kind or version != VERSION:
        raise AudioFrameError("unexpected audio frame type or version")
    if rate != NET_RATE:
        raise AudioFrameError(f"audio rate must be {NET_RATE}")
    if count > MAX_FRAME_SAMPLES or len(data) != HEADER_SIZE + 2 * count:
        raise AudioFrameError("bad audio frame length")
    return seq, rate, data[HEADER_SIZE:]


def peak_dbfs(pcm: bytes) -> float:
    """Peak level in dB relative to full scale; -120 for silence."""
    a = _as_samples(pcm)
    if not a:
        return -120.0
    peak = max(max(a), -min(a))
    return -120.0 if peak == 0 else round(20 * math.log10(peak / 32768), 1)


def downsample3(pcm: bytes) -> bytes:
    """48 kHz -> 16 kHz. Averages each group of three samples (a crude low-pass).

    Only used when a device refuses to open at 16 kHz; most OS audio layers
    resample for us. Voice from the radio is already band-limited to about
    3 kHz, so the weak filtering is acceptable.
    """
    a = _as_samples(pcm)
    n = len(a) // 3
    out = array("h", [(a[3 * i] + a[3 * i + 1] + a[3 * i + 2]) // 3 for i in range(n)])
    return _to_bytes(out)


def upsample3(pcm: bytes) -> bytes:
    """16 kHz -> 48 kHz by linear interpolation (fallback path only)."""
    a = _as_samples(pcm)
    out = array("h")
    for i, s in enumerate(a):
        nxt = a[i + 1] if i + 1 < len(a) else s
        out.extend((s, (2 * s + nxt) // 3, (s + 2 * nxt) // 3))
    return _to_bytes(out)


def tone(
    freq_hz: float, ms: int, rate: int = NET_RATE, amplitude: float = 0.3, phase0: int = 0
) -> bytes:
    """A sine tone as int16 PCM (simulator and tests)."""
    n = rate * ms // 1000
    k = 2 * math.pi * freq_hz / rate
    a = array("h", (int(amplitude * 32767 * math.sin(k * (phase0 + i))) for i in range(n)))
    return _to_bytes(a)
