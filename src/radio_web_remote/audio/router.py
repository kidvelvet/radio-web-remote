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
import contextlib
import logging
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
        self.tx_peak_dbfs = -120.0
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
        """Start the devices; if a device is missing, keep retrying."""
        while True:
            if not self.running:
                try:
                    assert self.radio is not None
                    await self.radio.start()
                    if self.host is not None:
                        await self.host.start()
                    self.running, self.error = True, None
                except Exception as exc:
                    self.error = str(exc) or type(exc).__name__
                    log.warning("audio not available: %s (retrying in %.0f s)", self.error, RETRY_S)
            await asyncio.sleep(RETRY_S)

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
        self.tx_peak_dbfs = pcm.peak_dbfs(frame_pcm)
        self.radio.write_tx(frame_pcm)
        return True

    def _on_host_mic(self, frame_pcm: bytes) -> None:
        holder = self._holder()
        if self.radio is None or holder is None or self._modes.get(holder) is not AudioMode.HOST:
            return
        self.tx_peak_dbfs = pcm.peak_dbfs(frame_pcm)
        self.radio.write_tx(frame_pcm)

    def tx_ended(self) -> None:
        """Called when TX stops: drop any transmit audio still buffered."""
        if self.radio is not None:
            self.radio.clear_tx()
        self.tx_peak_dbfs = -120.0

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
