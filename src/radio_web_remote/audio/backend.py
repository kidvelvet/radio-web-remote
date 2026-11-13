"""Audio backends: the radio's USB codec, the host's mic/speakers, and a simulator.

A backend delivers receive audio as 20 ms int16 mono frames at NET_RATE to a
callback on the event loop, and accepts transmit audio the same way. Output
is a FIFO the device callback drains; underruns play silence, and the FIFO is
capped so transmit latency cannot grow without bound.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from radio_web_remote.audio import pcm
from radio_web_remote.audio.pcm import FRAME_SAMPLES, NET_RATE

log = logging.getLogger(__name__)

RxCallback = Callable[[bytes], None]
MAX_TX_BUFFER_MS = 300  # older audio is dropped beyond this
TX_PREFILL_MS = 60  # wait for this much before starting to play out


class _Fifo:
    """Thread-safe byte FIFO with a cap; the audio thread reads, the loop writes."""

    def __init__(self, max_bytes: int, prefill_bytes: int) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._max = max_bytes
        self._prefill = prefill_bytes
        self._primed = False
        self.dropped_bytes = 0
        self.underruns = 0

    def write(self, data: bytes) -> None:
        with self._lock:
            self._buf += data
            over = len(self._buf) - self._max
            if over > 0:
                over += over % 2
                del self._buf[:over]
                self.dropped_bytes += over

    def read(self, n: int) -> bytes:
        with self._lock:
            if not self._primed:
                if len(self._buf) < self._prefill:
                    return bytes(n)
                self._primed = True
            if len(self._buf) < n:
                if self._buf:
                    self.underruns += 1
                chunk = bytes(self._buf) + bytes(n - len(self._buf))
                self._buf.clear()
                self._primed = False
                return chunk
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
            return chunk

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self._primed = False

    @property
    def buffered_ms(self) -> float:
        with self._lock:
            return len(self._buf) / 2 / NET_RATE * 1000


class AudioBackend(ABC):
    """Receive (input) and transmit (output) audio for one device pair."""

    def __init__(self) -> None:
        self.on_rx: RxCallback | None = None
        self.fifo = _Fifo(
            max_bytes=NET_RATE * 2 * MAX_TX_BUFFER_MS // 1000,
            prefill_bytes=NET_RATE * 2 * TX_PREFILL_MS // 1000,
        )

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    def write_tx(self, frame_pcm: bytes) -> None:
        self.fifo.write(frame_pcm)

    def clear_tx(self) -> None:
        self.fifo.clear()


class SoundDeviceBackend(AudioBackend):
    """PortAudio via sounddevice. Opens at 16 kHz mono, or 48 kHz with our own resampling."""

    def __init__(self, input_device: int | None, output_device: int | None, label: str) -> None:
        super().__init__()
        self.input_device = input_device
        self.output_device = output_device
        self.label = label
        self._streams: list[Any] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self.device_rate = NET_RATE

    def _pick_rate(self, sd: Any) -> int:
        for rate in (NET_RATE, 48_000):
            try:
                if self.input_device is not None:
                    sd.check_input_settings(
                        device=self.input_device, samplerate=rate, channels=1, dtype="int16"
                    )
                if self.output_device is not None:
                    sd.check_output_settings(
                        device=self.output_device, samplerate=rate, channels=1, dtype="int16"
                    )
                return rate
            except Exception:  # noqa: S112  (try the next rate)
                continue
        raise OSError(f"{self.label}: device accepts neither 16 kHz nor 48 kHz mono int16")

    async def start(self) -> None:
        import sounddevice as sd

        self._loop = asyncio.get_running_loop()
        self.device_rate = self._pick_rate(sd)
        factor = self.device_rate // NET_RATE  # 1 or 3
        block = FRAME_SAMPLES * factor

        if self.input_device is not None:

            def on_input(indata: Any, frames: int, _t: Any, status: Any) -> None:
                if status:
                    log.debug("%s input status: %s", self.label, status)
                data = bytes(indata)
                if factor == 3:
                    data = pcm.downsample3(data)
                loop, cb = self._loop, self.on_rx
                if loop is not None and cb is not None:
                    loop.call_soon_threadsafe(cb, data)

            stream_in = sd.RawInputStream(
                device=self.input_device,
                samplerate=self.device_rate,
                channels=1,
                dtype="int16",
                blocksize=block,
                callback=on_input,
                latency="low",
            )
            stream_in.start()
            self._streams.append(stream_in)

        if self.output_device is not None:

            def on_output(outdata: Any, frames: int, _t: Any, status: Any) -> None:
                need = frames * 2 // factor
                data = self.fifo.read(need)
                if factor == 3:
                    data = pcm.upsample3(data)
                outdata[:] = data[: frames * 2].ljust(frames * 2, b"\x00")

            stream_out = sd.RawOutputStream(
                device=self.output_device,
                samplerate=self.device_rate,
                channels=1,
                dtype="int16",
                blocksize=block,
                callback=on_output,
                latency="low",
            )
            stream_out.start()
            self._streams.append(stream_out)
        log.info("%s audio running at %d Hz", self.label, self.device_rate)

    async def stop(self) -> None:
        for s in self._streams:
            with contextlib.suppress(Exception):
                s.stop()
                s.close()
        self._streams.clear()


class SimAudioBackend(AudioBackend):
    """A fake codec: receive is a 700 Hz tone with a little noise, and transmit audio is recorded.

    With monitor=True, transmit audio is mixed back into receive, like the
    radio's TX monitor, so an end-to-end test can hear its own microphone.
    """

    def __init__(self, monitor: bool = True) -> None:
        super().__init__()
        self.monitor = monitor
        self.tx_frames: list[bytes] = []
        self._task: asyncio.Task[None] | None = None
        self._phase = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="sim-audio")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        while True:
            next_t += pcm.FRAME_MS / 1000
            await asyncio.sleep(max(0.0, next_t - loop.time()))
            tx = self.fifo.read(FRAME_SAMPLES * 2)
            if any(tx):
                self.tx_frames.append(tx)
            rx = pcm.tone(700, pcm.FRAME_MS, amplitude=0.1, phase0=self._phase)
            self._phase += FRAME_SAMPLES
            if self.monitor and any(tx):
                rx = tx
            if self.on_rx is not None:
                self.on_rx(rx)
