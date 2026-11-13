"""IC-7300 style scope waveform frames (CI-V 27 00) and scope settings (27 1x).

Layout, Icom IC-7300 Full Manual p. 19-12, confirmed on hardware 2026-11-13
(docs/hardware-test.md, H7300-14):

  27 00 | 00 | div | max | ...
  first division (div 01): mode, waveform information, out-of-range flag
  later divisions (02..max): waveform points only

- 00: printed as "Fixed" 00.
- div, max: division numbers, BCD (hardware sends 11 as 0x11, not 0x0B).
  Over USB, max = 11 (p. 19-12).
- mode: 00 = Center, 01 = Fixed (p. 19-12). The radio also reported 02 and
  03, which the manual does not list (TODO(verify): Icom's Scroll-C and
  Scroll-F modes?). With 02 and 03 the header carried lower and upper edges,
  like Fixed.
- waveform information: Center mode = center frequency then span; Fixed =
  lower edge then higher edge (p. 19-12). Each is 5 bytes in the operating
  frequency format (p. 19-8). Hardware: no leading 00 before the span.
  The span is a half-width (front panel lists "+/-2.5 ... 500 kHz", p. 5-2),
  confirmed on hardware: center 14.304 MHz, span 50 kHz gave the edges
  14.254 to 14.354 MHz.
- out of range: 00 in range, 01 out of range, and then the waveform data is
  omitted (p. 19-12).
- points: data range 0 to 160, length 475 (p. 19-12). One byte per point
  (hardware: 9 divisions of 50 points and a last one of 25), so binary: one
  BCD byte could not hold 160.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from radio_web_remote.protocols.civ.bcd import decode_bcd_le, decode_frequency, encode_bcd_le
from radio_web_remote.protocols.civ.commands import ReplyError
from radio_web_remote.radios.base import ScopeLine

SCOPE_DATA = b"\x27\x00"
MODE_CENTER = 0x00
MODE_FIXED = 0x01


def _bcd_byte(b: int) -> int:
    hi, lo = b >> 4, b & 0x0F
    if hi > 9 or lo > 9:
        raise ReplyError(f"division byte {b:02X} is not BCD")
    return hi * 10 + lo


@dataclass
class ScopeStats:
    lines: int = 0
    dropped: int = 0  # incomplete or out-of-order sweeps
    out_of_range: int = 0


@dataclass
class ScopeAssembler:
    """Turns a stream of 27 00 division frames into complete ScopeLines."""

    points: int = 475
    max_value: int = 160
    stats: ScopeStats = field(default_factory=ScopeStats)
    _header: tuple[int, int, int, bool] | None = None  # mode, low, high, out_of_range
    _expect: int = 0
    _max_div: int = 0
    _buf: bytearray = field(default_factory=bytearray)

    def _drop(self) -> None:
        if self._header is not None:
            self.stats.dropped += 1
        self._header = None
        self._buf.clear()

    def feed(self, payload: bytes) -> ScopeLine | None:
        """Feed one frame payload starting with 27 00. Returns a line when a sweep completes."""
        if not payload.startswith(SCOPE_DATA) or len(payload) < 5:
            raise ReplyError(f"not a scope data frame: {payload[:8].hex(' ')}")
        div, max_div = _bcd_byte(payload[3]), _bcd_byte(payload[4])
        body = payload[5:]
        if div == 1:
            self._drop()
            if len(body) != 12:
                raise ReplyError(f"scope header has {len(body)} bytes, expected 12")
            mode = body[0]
            a, b = decode_frequency(body[1:6]), decode_frequency(body[6:11])
            out_of_range = body[11] == 0x01
            if mode == MODE_CENTER:
                low, high = a - b, a + b  # center, half-width span
            else:
                low, high = a, b  # Fixed, and the undocumented 02/03
            self._header = (mode, low, high, out_of_range)
            self._expect, self._max_div = 2, max_div
            if max_div == 1:
                return self._finish()
            return None
        if self._header is None or div != self._expect or max_div != self._max_div:
            self._drop()
            return None
        self._buf += body
        self._expect += 1
        if div == max_div:
            return self._finish()
        return None

    def _finish(self) -> ScopeLine | None:
        header, points = self._header, bytes(self._buf)
        self._header = None
        self._buf.clear()
        assert header is not None
        mode, low, high, out_of_range = header
        if out_of_range:
            self.stats.out_of_range += 1
            points = b""
        elif len(points) != self.points:
            self.stats.dropped += 1
            return None
        self.stats.lines += 1
        clipped = bytes(min(p, self.max_value) for p in points)
        return ScopeLine(low, high, clipped, self.max_value, mode != MODE_CENTER, out_of_range)


def sweep_frames(line: ScopeLine, mode: int, center_hz: int, span_hz: int) -> list[bytes]:
    """Build the 11 division payloads a radio would send for one sweep (simulator)."""
    if mode == MODE_CENTER:
        info = encode_bcd_le(center_hz, 5) + encode_bcd_le(span_hz, 5)
    else:
        info = encode_bcd_le(line.low_hz, 5) + encode_bcd_le(line.high_hz, 5)
    out = [SCOPE_DATA + bytes([0x00, 0x01, 0x11, mode]) + info + bytes([int(line.out_of_range)])]
    pts = b"" if line.out_of_range else line.points
    chunks = [pts[i : i + 50] for i in range(0, 475, 50)] if pts else [b""] * 10
    for i, chunk in enumerate(chunks, start=2):
        out.append(SCOPE_DATA + bytes([0x00, (i // 10) << 4 | (i % 10), 0x11]) + chunk)
    return out


# -- settings (27 1x), p. 19-7 table, layouts pp. 19-12, 19-13 ----------------
# Read with the 00 selector (27 14 00); hardware answers NG to a bare 27 14.

SPANS_HZ = (2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000)  # p. 19-12
SPEEDS = ("fast", "mid", "slow")  # 27 1A: 00, 01, 02 (p. 19-13)


def encode_ref_level(db: float) -> bytes:
    """27 19 data after the 00 selector: [10 dB|1 dB] [0.1 dB|0.01 dB] [sign] (p. 19-12)."""
    if not -20.0 <= db <= 20.0 or round(db * 2) != db * 2:
        raise ValueError("reference level must be -20.0..+20.0 dB in 0.5 dB steps")
    mag = round(abs(db) * 100)  # hundredths
    whole, frac = divmod(mag, 100)
    return bytes(
        [(whole // 10) << 4 | (whole % 10), (frac // 10) << 4 | (frac % 10), 1 if db < 0 else 0]
    )


def decode_ref_level(data: bytes) -> float:
    if len(data) != 3 or data[2] not in (0, 1):
        raise ReplyError(f"bad reference level data {data.hex(' ')}")
    whole, frac = _bcd_byte(data[0]), _bcd_byte(data[1])
    value = whole + frac / 100
    return -value if data[2] == 1 else value


def encode_span(hz: int) -> bytes:
    if hz not in SPANS_HZ:
        raise ValueError(f"span must be one of {SPANS_HZ}")
    return encode_bcd_le(hz, 5)


def decode_span(data: bytes) -> int:
    return decode_bcd_le(data)
