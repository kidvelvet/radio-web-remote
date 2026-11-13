"""Serial transport: one blocking reader thread per port, bridged into asyncio.

pyserial-asyncio depends on event-loop fd readiness, which does not work for
serial handles on Windows' default Proactor loop, so a plain thread does the
blocking reads instead (ADR 0001, D2). Writes run in a worker thread too, so
a slow USB write never blocks the event loop.

Safety: DTR and RTS are set low *before* the port opens. A radio whose
"USB SEND" is set to DTR or RTS would otherwise key the transmitter the
moment the port opens, because pyserial asserts both lines by default.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import serial

from radio_web_remote.transport.base import ByteTransport

READ_TIMEOUT_S = 0.05
WRITE_TIMEOUT_S = 1.0


class SerialTransport(ByteTransport):
    def __init__(self, port: str, baudrate: int = 115200) -> None:
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self._ser: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    @staticmethod
    def _make_serial() -> Any:
        return serial.Serial()

    async def _open_impl(self) -> None:
        self._loop = asyncio.get_running_loop()
        ser = self._make_serial()
        ser.port = self.port
        ser.baudrate = self.baudrate
        ser.bytesize = serial.EIGHTBITS
        ser.parity = serial.PARITY_NONE
        ser.stopbits = serial.STOPBITS_ONE
        ser.timeout = READ_TIMEOUT_S
        ser.write_timeout = WRITE_TIMEOUT_S
        # Set before open() so the lines never go high, even briefly.
        ser.dtr = False
        ser.rts = False
        await asyncio.to_thread(ser.open)
        self._ser = ser
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._read_loop, name=f"serial-reader-{self.port}", daemon=True
        )
        self._thread.start()

    async def _close_impl(self) -> None:
        self._stop.set()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 1.0)
            self._thread = None
        if self._ser is not None:
            ser, self._ser = self._ser, None
            await asyncio.to_thread(ser.close)

    async def _write_impl(self, data: bytes) -> None:
        ser = self._ser
        if ser is None:
            raise ConnectionError("serial port is closed")
        await asyncio.to_thread(ser.write, data)

    async def set_control_lines(self, *, dtr: bool | None = None, rts: bool | None = None) -> None:
        """Drive DTR/RTS (used for DTR/RTS PTT in P1). Goes through the write lock."""
        async with self._write_lock:
            ser = self._ser
            if ser is None:
                raise ConnectionError("serial port is closed")
            if dtr is not None:
                ser.dtr = dtr
            if rts is not None:
                ser.rts = rts

    def _read_loop(self) -> None:
        loop = self._loop
        assert loop is not None
        while not self._stop.is_set():
            ser = self._ser
            if ser is None:
                return
            try:
                data = ser.read(ser.in_waiting or 1)
            except Exception as exc:  # unplugged cable, driver error
                if not self._stop.is_set():
                    loop.call_soon_threadsafe(self._report_error, exc)
                return
            if data:
                loop.call_soon_threadsafe(self._deliver, bytes(data))
