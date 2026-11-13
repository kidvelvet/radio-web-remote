"""EXPERIMENTAL WebRTC audio transport (P3b spike, ADR 0002). Optional extra: aiortc.

One peer per control connection. The browser posts an offer for its own
control connection; receive audio goes out as an Opus track, and the
browser's mic track comes back through the same AudioRouter as the
WebSocket path, so the rule is unchanged: only the TX holder in remote
mode reaches the radio.

Limits found while building it (ADR 0002):
- aioice 0.13.0's Connection takes no port or port-range option, so media
  uses ephemeral UDP ports, not the single HTTPS port.
- Host candidates only (no STUN/TURN): LAN and routed VPN paths only.
"""

from __future__ import annotations

import asyncio
import contextlib
import fractions
import logging
import sys
from array import array
from typing import Any

from radio_web_remote.audio import pcm

log = logging.getLogger(__name__)

try:
    import av
    from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription

    AVAILABLE = True
except ImportError:  # the 'webrtc' extra is not installed
    AVAILABLE = False

RATE_48K = 48_000
SAMPLES_20MS = 960


if AVAILABLE:

    class RxTrack(MediaStreamTrack):
        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=10)
            self._pts = 0

        def push(self, frame_16k: bytes) -> None:
            if self.queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()  # drop oldest: latency over completeness
            self.queue.put_nowait(frame_16k)

        async def recv(self) -> Any:
            data = pcm.upsample3(await self.queue.get())
            frame = av.AudioFrame(format="s16", layout="mono", samples=len(data) // 2)
            frame.planes[0].update(data)
            frame.sample_rate = RATE_48K
            frame.pts = self._pts
            frame.time_base = fractions.Fraction(1, RATE_48K)
            self._pts += len(data) // 2
            return frame


def _to_16k_mono(frame: Any) -> bytes:
    """Decoded Opus frame (s16, usually stereo, 48 kHz) -> 16 kHz mono int16."""
    raw = bytes(frame.planes[0])
    a = array("h")
    a.frombytes(raw[: len(raw) - len(raw) % 2])
    if sys.byteorder == "big":
        a.byteswap()
    channels = len(frame.layout.channels)
    mono = a[::channels] if channels > 1 else a
    data = mono.tobytes()
    if frame.sample_rate == RATE_48K:
        return pcm.downsample3(data)
    return data


class RtcPeers:
    def __init__(self) -> None:
        self._peers: dict[str, Any] = {}
        self._tracks: dict[str, Any] = {}
        self._pumps: set[asyncio.Task[None]] = set()

    async def answer(
        self, conn_id: str, station: Any, offer_sdp: str, offer_type: str
    ) -> dict[str, str]:
        if not AVAILABLE:
            raise RuntimeError("WebRTC is not installed (pip extra 'webrtc')")
        await self.close(conn_id)
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        rx = RxTrack()
        self._peers[conn_id], self._tracks[conn_id] = pc, rx
        pc.addTrack(rx)

        @pc.on("track")
        def on_track(track: Any) -> None:
            if track.kind != "audio":
                return

            async def pump() -> None:
                n = used = 0
                try:
                    while True:
                        frame = await track.recv()
                        n += 1
                        mono = _to_16k_mono(frame)
                        if station.audio.tx_from_browser(conn_id, mono):
                            used += 1
                            lvl = pcm.peak_dbfs(mono)
                            if lvl > -40:
                                log.info(
                                    "rtc %s: loud mic frame %d at %.1f dBFS", conn_id[:8], n, lvl
                                )
                        if n % 250 == 0:
                            log.info(
                                "rtc %s: %d mic frames, %d sent to radio", conn_id[:8], n, used
                            )
                except Exception as exc:
                    log.info("rtc %s mic track ended after %d frames: %r", conn_id[:8], n, exc)

            task = asyncio.ensure_future(pump())
            self._pumps.add(task)
            task.add_done_callback(self._pumps.discard)

        @pc.on("connectionstatechange")
        async def on_state() -> None:
            if pc.connectionState in ("failed", "closed"):
                await self.close(conn_id)

        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        await pc.setLocalDescription(await pc.createAnswer())
        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    def push_rx(self, conn_id: str, frame_16k: bytes) -> bool:
        track = self._tracks.get(conn_id)
        if track is None:
            return False
        track.push(frame_16k)
        return True

    def has(self, conn_id: str) -> bool:
        return conn_id in self._peers

    async def close(self, conn_id: str) -> None:
        pc = self._peers.pop(conn_id, None)
        self._tracks.pop(conn_id, None)
        if pc is not None:
            with contextlib.suppress(Exception):
                await pc.close()

    async def close_all(self) -> None:
        for cid in list(self._peers):
            await self.close(cid)
