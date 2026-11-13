"""Fake Icom radio that speaks CI-V, for tests and for running without hardware.

The core (IcomSimulator.handle_bytes) is pure: bytes in, bytes out. Two
runners attach it to something a driver can open:

- attach_loopback(): in-process, any OS (used by CI on Windows).
- PtyRunner: a POSIX pseudo-terminal, so the real SerialTransport and
  pyserial code paths run end to end (Linux and macOS).

The simulator only answers commands that the model's capability table
lists, using the same codes, so it can never "support" something the
documents do not.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from dataclasses import dataclass, field

from radio_web_remote.protocols.civ import commands as cmd
from radio_web_remote.protocols.civ.bcd import BcdError, decode_frequency, encode_frequency
from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.protocols.civ.frame import NG, OK, Frame, FrameParser, encode_frame
from radio_web_remote.radios.base import Mode
from radio_web_remote.radios.icom.model import IcomModel
from radio_web_remote.transport.loopback import LoopbackEnd

TRANSCEIVE_TO = 0x00  # TODO(verify): see civ_driver.BROADCAST


@dataclass
class SimState:
    frequency_hz: int = 14_074_000
    mode_code: int = 0x01  # USB on the IC-7300 table
    filter_code: int = 0x01
    transmitting: bool = False


@dataclass
class IcomSimulator:
    model: IcomModel
    address: int | None = None
    echo_back: bool = False
    transceive: bool = True
    silent: bool = False  # answer nothing (timeout tests)
    state: SimState = field(default_factory=SimState)
    received: list[Frame] = field(default_factory=list)
    tx_history: list[bool] = field(default_factory=list)
    parser: FrameParser = field(default_factory=FrameParser)

    def __post_init__(self) -> None:
        if self.address is None:
            self.address = self.model.default_address.value

    @property
    def addr(self) -> int:
        assert self.address is not None
        return self.address

    # -- I/O ------------------------------------------------------------
    def handle_bytes(self, data: bytes) -> bytes:
        out = bytearray()
        for frame in self.parser.feed(data):
            if self.echo_back:
                out += frame.encode()
            if frame.to_addr != self.addr:
                continue
            self.received.append(frame)
            if self.silent:
                continue
            reply = self._handle(frame)
            if reply is not None:
                out += encode_frame(frame.from_addr, self.addr, reply)
        return bytes(out)

    # -- front panel (test hooks) ----------------------------------------
    def front_panel_tune(self, hz: int) -> bytes:
        """Simulate turning the VFO knob. Returns the transceive frame, if any."""
        self.state.frequency_hz = hz
        if not self.transceive or not self.model.supports(CivCommand.TRANSCEIVE_FREQUENCY):
            return b""
        return encode_frame(TRANSCEIVE_TO, self.addr, b"\x00" + encode_frequency(hz))

    def front_panel_mode(self, mode: Mode) -> bytes:
        self.state.mode_code = self.model.modes[mode].value
        if not self.transceive or not self.model.supports(CivCommand.TRANSCEIVE_MODE):
            return b""
        return encode_frame(
            TRANSCEIVE_TO, self.addr, bytes([0x01, self.state.mode_code, self.state.filter_code])
        )

    # -- command handling --------------------------------------------------
    def _handle(self, frame: Frame) -> bytes | None:
        p = frame.payload
        m = self.model
        ok, ng = bytes([OK]), bytes([NG])

        if p == cmd.read_frequency() and m.supports(CivCommand.READ_FREQUENCY):
            return b"\x03" + encode_frequency(self.state.frequency_hz)
        if p[:1] == b"\x05" and m.supports(CivCommand.SET_FREQUENCY):
            try:
                hz = decode_frequency(p[1:])
            except BcdError:
                return ng
            if not 0 < hz <= m.max_encodable_hz:
                return ng
            self.state.frequency_hz = hz
            return ok
        if p == cmd.read_mode() and m.supports(CivCommand.READ_MODE):
            return bytes([0x04, self.state.mode_code, self.state.filter_code])
        if p[:1] == b"\x06" and m.supports(CivCommand.SET_MODE):
            data = p[1:]
            codes = {e.value for e in m.modes.values()}
            filters = {e.value for e in m.filters.values()}
            if len(data) not in (1, 2) or data[0] not in codes:
                return ng
            if len(data) == 2 and data[1] not in filters:
                return ng
            self.state.mode_code = data[0]
            if len(data) == 2:
                self.state.filter_code = data[1]
            return ok
        if p.startswith(CivCommand.TX_STATE.prefix) and m.supports(CivCommand.TX_STATE):
            data = p[len(CivCommand.TX_STATE.prefix) :]
            if not data:
                return CivCommand.TX_STATE.prefix + (
                    b"\x01" if self.state.transmitting else b"\x00"
                )
            if data in (b"\x00", b"\x01"):
                self.state.transmitting = data == b"\x01"
                self.tx_history.append(self.state.transmitting)
                return ok
            return ng
        return ng


def attach_loopback(sim: IcomSimulator, end: LoopbackEnd) -> None:
    """Make the simulator answer on one end of a loopback pair (end must be opened)."""

    def on_data(data: bytes) -> None:
        reply = sim.handle_bytes(data)
        if reply and end.is_open:
            # Write directly, not through the queue, like a real UART would.
            peer = end.peer
            if peer is not None and peer.is_open:
                peer._deliver(reply)

    end.on_data = on_data


def push_unsolicited(end: LoopbackEnd, data: bytes) -> None:
    """Deliver radio-originated bytes (transceive, junk) to the driver side."""
    peer = end.peer
    if data and peer is not None and peer.is_open:
        peer._deliver(data)


class PtyRunner:
    """Serve the simulator on a POSIX pseudo-terminal. `path` is what a driver opens."""

    def __init__(self, sim: IcomSimulator) -> None:
        if sys.platform == "win32":
            raise OSError("pseudo-terminals are not available on Windows; use attach_loopback")
        import tty

        self.sim = sim
        self._master, self._slave = os.openpty()
        tty.setraw(self._master)
        tty.setraw(self._slave)
        self.path = os.ttyname(self._slave)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="icom-sim-pty", daemon=True)

    def start(self) -> PtyRunner:
        self._thread.start()
        return self

    def inject(self, data: bytes) -> None:
        """Write radio-originated bytes (transceive frames, junk) to the line."""
        with self._lock:
            os.write(self._master, data)

    def _run(self) -> None:
        import select

        while not self._stop.is_set():
            ready, _, _ = select.select([self._master], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self._master, 4096)
            except OSError:
                return
            if data:
                with self._lock:
                    reply = self.sim.handle_bytes(data)
                if reply:
                    self.inject(reply)

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(1.0)
        for fd in (self._master, self._slave):
            with contextlib.suppress(OSError):
                os.close(fd)

    def __enter__(self) -> PtyRunner:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
