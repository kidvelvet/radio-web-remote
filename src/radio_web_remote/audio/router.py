"""Per-radio audio routing (requirement 5 in the brief).

Each control connection picks a mode:

- remote:  radio receive audio -> that browser; that browser's mic -> radio
- host:    radio receive audio -> this computer's speakers; this computer's
           mic -> radio. The browser only controls.
- monitor: radio receive audio -> that browser; no transmit audio path.

Transmit audio goes to the radio only from the current TX holder, and only
in the holder's mode. When TX ends, buffered transmit audio is discarded at
once. Other connections may listen at the same time.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import time
from collections.abc import Callable
from enum import Enum

from radio_web_remote.audio import pcm
from radio_web_remote.audio.backend import AudioBackend

log = logging.getLogger(__name__)

RxListener = Callable[[bytes], None]
RETRY_S = 10.0


class AudioMode(Enum):
    REMOTE = "remote"
    HOST = "host"
    MONITOR = "monitor"


class AudioRouter:
    def __init__(
        self,
        radio: AudioBackend | None,
        host: AudioBackend | None,
        holder: Callable[[], str | None],
    ) -> None:
        """holder() returns the session id currently holding TX, or None."""
        self.radio = radio
        self.host = host
        self._holder = holder
        self._modes: dict[str, AudioMode] = {}
        self._rx_listeners: list[RxListener] = []
        self.error: str | None = None if radio else "no radio audio device configured"
        self.running = False
        self.rx_seq = 0
        self._tx_peaks: collections.deque[tuple[float, float]] = collections.deque()
        self._task: asyncio.Task[None] | None = None
        if radio is not None:
            radio.on_rx = self._on_radio_rx
        if host is not None:
            host.on_rx = self._on_host_mic

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self.radio is not None and self._task is None:
            self._task = asyncio.create_task(self._keep_running(), name="audio-router")

    async def _keep_running(self) -> None:
        """Start the devices; if a device is missing, keep retrying.

        The radio codec decides whether audio is running. The optional host
        devices are retried on their own: a missing headset must not take
        radio audio away from remote operators.
        """
        assert self.radio is not None
        reload = False
        host_up = False
        while True:
            if self.running and self.radio.stalled():
                log.warning("audio device stopped delivering; reopening")
                self.running, self.error = False, "audio device stopped"
                await self._stop_all()
                host_up, reload = False, True
            if host_up and self.host is not None and self.host.stalled():
                log.warning("host audio device stopped delivering; reopening")
                with contextlib.suppress(Exception):
                    await self.host.stop()
                host_up = False
            if not self.running:
                if reload:  # with every stream closed: see devices plugged in since
                    with contextlib.suppress(Exception):
                        self.radio.reload_devices()
                    reload = False
                try:
                    await self.radio.start()
                    self.running, self.error = True, None
                except Exception as exc:
                    self.error = str(exc) or type(exc).__name__
                    log.warning("audio not available: %s (retrying in %.0f s)", self.error, RETRY_S)
                    await self._stop_all()  # never leave half of it open between tries
                    reload = True
            if self.running and self.host is not None and not host_up:
                try:
                    await self.host.start()
                    host_up, self.error = True, None
                except Exception as exc:
                    error = f"host audio: {exc or type(exc).__name__}"
                    if error != self.error:
                        log.warning("%s (retrying in %.0f s)", error, RETRY_S)
                    self.error = error
                    with contextlib.suppress(Exception):
                        await self.host.stop()
            await asyncio.sleep(RETRY_S)

    async def _stop_all(self) -> None:
        for b in (self.radio, self.host):
            if b is not None:
                with contextlib.suppress(Exception):
                    await b.stop()

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for b in (self.radio, self.host):
            if b is not None:
                with contextlib.suppress(Exception):
                    await b.stop()
        self.running = False

    @property
    def host_available(self) -> bool:
        return self.host is not None

    # -- sessions ----------------------------------------------------------
    def set_mode(self, session_id: str, mode: AudioMode) -> None:
        if mode is AudioMode.HOST and self.host is None:
            raise ValueError("host audio is not configured on this server")
        self._modes[session_id] = mode

    def remove(self, session_id: str) -> None:
        self._modes.pop(session_id, None)

    def mode(self, session_id: str) -> AudioMode | None:
        return self._modes.get(session_id)

    def add_rx_listener(self, listener: RxListener) -> None:
        self._rx_listeners.append(listener)

    def remove_rx_listener(self, listener: RxListener) -> None:
        with contextlib.suppress(ValueError):
            self._rx_listeners.remove(listener)

    # -- transmit ----------------------------------------------------------
    def tx_from_browser(self, session_id: str, frame_pcm: bytes) -> bool:
        """Forward browser mic audio if this session holds TX in remote mode."""
        if self.radio is None or self._holder() != session_id:
            return False
        if self._modes.get(session_id) is not AudioMode.REMOTE:
            return False
        self._note_peak(frame_pcm)
        self.radio.write_tx(frame_pcm)
        return True

    def _on_host_mic(self, frame_pcm: bytes) -> None:
        holder = self._holder()
        if self.radio is None or holder is None or self._modes.get(holder) is not AudioMode.HOST:
            return
        self._note_peak(frame_pcm)
        self.radio.write_tx(frame_pcm)

    PEAK_HOLD_S = 1.0

    def _note_peak(self, frame_pcm: bytes) -> None:
        now = time.monotonic()
        self._tx_peaks.append((now, pcm.peak_dbfs(frame_pcm)))
        while self._tx_peaks and now - self._tx_peaks[0][0] > self.PEAK_HOLD_S:
            self._tx_peaks.popleft()

    @property
    def tx_peak_dbfs(self) -> float:
        """Peak transmit level over the last second (a peak-hold meter); -120 if none."""
        now = time.monotonic()
        recent = [p for t, p in self._tx_peaks if now - t <= self.PEAK_HOLD_S]
        return max(recent, default=-120.0)

    def tx_ended(self) -> None:
        """Called when TX stops: drop any transmit audio still buffered."""
        if self.radio is not None:
            self.radio.clear_tx()
        self._tx_peaks.clear()

    # -- receive -----------------------------------------------------------
    def _on_radio_rx(self, frame_pcm: bytes) -> None:
        self.rx_seq += 1
        if self.host is not None and AudioMode.HOST in self._modes.values():
            self.host.write_tx(frame_pcm)  # host speakers
        for listener in list(self._rx_listeners):
            try:
                listener(frame_pcm)
            except Exception:
                log.exception("audio listener failed")
