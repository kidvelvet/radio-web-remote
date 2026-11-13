"""Generate the app icons (PNG) with no image libraries: navy square, three white arcs.

Run: python tools/make_icons.py   (writes src/radio_web_remote/static/icons/)
"""

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "radio_web_remote" / "static" / "icons"
NAVY = (15, 42, 79)
WHITE = (255, 255, 255)
RED = (229, 57, 53)


def png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + r for r in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def icon(size: int) -> bytes:
    cx, cy = size * 0.30, size * 0.70  # wave source, lower left
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            c = NAVY
            dx, dy = x - cx, cy - y
            r = math.hypot(dx, dy) / size
            ang = math.degrees(math.atan2(dy, dx))
            if r < 0.07:
                c = RED
            elif -10 <= ang <= 100:
                for k in (0.20, 0.34, 0.48):
                    if abs(r - k) < 0.035:
                        c = WHITE
            row += bytes(c)
        rows.append(bytes(row))
    return png(size, size, rows)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for size, name in ((180, "apple-touch-icon.png"), (192, "icon-192.png"), (512, "icon-512.png")):
        (OUT / name).write_bytes(icon(size))
        print("wrote", OUT / name)
