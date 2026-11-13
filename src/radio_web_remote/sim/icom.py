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

import asyncio
import contextlib
import os
import sys
import threading
import time
from dataclasses import dataclass, field

from radio_web_remote.protocols.civ import commands as cmd
from radio_web_remote.protocols.civ.bcd import BcdError, decode_frequency, encode_frequency
from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.protocols.civ.frame import NG, OK, Frame, FrameParser, encode_frame
from radio_web_remote.radios.base import Mode
from radio_web_remote.radios.icom.model import LEVEL_COMMANDS, METER_COMMANDS, IcomModel
from radio_web_remote.transport.loopback import LoopbackEnd

TRANSCEIVE_TO = 0x00  # observed on IC-7300 hardware; see civ_driver.BROADCAST


@dataclass
class SimState:
    frequency_hz: int = 14_074_000
    mode_code: int = 0x01  # USB on the IC-7300 table
    filter_code: int = 0x01
    transmitting: bool = False
    data_mode: bool = False
    levels: dict[int, int] = field(
        default_factory=lambda: {0x01: 128, 0x02: 255, 0x03: 0, 0x0A: 128}  # AF RF SQL PWR
    )
    s_meter: int = 60
    tx_meters: dict[int, int] = field(
        default_factory=lambda: {0x11: 150, 0x12: 30, 0x13: 40}  # Po SWR ALC while keyed
    )


@dataclass
class IcomSimulator:
    model: IcomModel
    address: int | None = None
    echo_back: bool = False
    transceive: bool = True
    silent: bool = False  # answer nothing (timeout tests)
    # Observed on an IC-7300 over USB (2026-11-13): the reply comes a few ms
    # after the command, and a command arriving before that reply is sent
    # goes unanswered. Runners deliver replies after reply_delay_s.
    reply_delay_s: float = 0.003
    drop_while_busy: bool = True
    # Also observed (H7300-4): after TX off is acknowledged the radio keeps
    # reporting TX for roughly 15 to 65 ms.
    tx_release_s: float = 0.04
    state: SimState = field(default_factory=SimState)
    dropped: list[Frame] = field(default_factory=list)
    received: list[Frame] = field(default_factory=list)
    tx_history: list[bool] = field(default_factory=list)
    parser: FrameParser = field(default_factory=FrameParser)

    def __post_init__(self) -> None:
        if self.address is None:
            self.address = self.model.default_address.value
        self._busy_until = 0.0
        self._rx_at: float | None = None  # when a pending TX -> RX switch completes

    @property
    def addr(self) -> int:
        assert self.address is not None
        return self.address

    # -- I/O ------------------------------------------------------------
    def handle_bytes(self, data: bytes, now: float | None = None) -> bytes:
        """Process incoming bytes at time `now` (monotonic seconds); return the reply bytes."""
        now = time.monotonic() if now is None else now
        out = bytearray()
        for frame in self.parser.feed(data):
            if self.echo_back:
                out += frame.encode()
            if frame.to_addr != self.addr:
                continue
            if self.drop_while_busy and now < self._busy_until:
                self.dropped.append(frame)
                continue
            self.received.append(frame)
            if self.silent:
                continue
            reply = self._handle(frame, now)
            if reply is not None:
                out += encode_frame(frame.from_addr, self.addr, reply)
                self._busy_until = now + self.reply_delay_s
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
    def reply_sent(self) -> None:
        """Runners call this once a reply is on the wire: the radio is free again.

        Busy ends here rather than by the clock, because event-loop timers can
        fire early on Windows (about 15 ms clock granularity), which would make
        the simulator drop a command the real radio would answer.
        """
        self._busy_until = 0.0

    def reported_tx(self, now: float | None = None) -> bool:
        """TX state as the radio would report it: still TX until the release delay passes."""
        if self.state.transmitting:
            return True
        now = time.monotonic() if now is None else now
        return self._rx_at is not None and now < self._rx_at

    def _handle(self, frame: Frame, now: float) -> bytes | None:
        p = frame.payload
        m = self.model
        ok, ng = bytes([OK]), bytes([NG])

        level_cmds = {c.prefix: c for c in LEVEL_COMMANDS.values() if m.supports(c)}
        meter_cmds = {c.prefix: c for c in METER_COMMANDS.values() if m.supports(c)}
        if p[:2] in level_cmds:
            sub = p[1]
            if len(p) == 2:
                return p + cmd.encode_level(self.state.levels.get(sub, 0))
            try:
                self.state.levels[sub] = cmd.decode_level(p[2:])
            except cmd.ReplyError:
                return ng
            return ok
        if p[:2] in meter_cmds and len(p) == 2:
            sub = p[1]
            if sub == 0x02:
                value = self.state.s_meter
            else:
                value = self.state.tx_meters.get(sub, 0) if self.reported_tx(now) else 0
            return p + cmd.encode_level(value)
        if p[:2] == CivCommand.DATA_MODE.prefix and m.supports(CivCommand.DATA_MODE):
            data = p[2:]
            if not data:
                flt = self.state.filter_code if self.state.data_mode else 0
                return p + bytes([int(self.state.data_mode), flt])
            if len(data) == 2 and data[0] in (0, 1):
                self.state.data_mode = data[0] == 1
                if data[0] == 1 and data[1]:
                    self.state.filter_code = data[1]
                return ok
            return ng

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
                return CivCommand.TX_STATE.prefix + (b"\x01" if self.reported_tx(now) else b"\x00")
            if data in (b"\x00", b"\x01"):
                want_tx = data == b"\x01"
                if want_tx:
                    self._rx_at = None
                elif self.state.transmitting and self._rx_at is None:
                    self._rx_at = now + self.tx_release_s
                self.state.transmitting = want_tx
                self.tx_history.append(want_tx)
                return ok
            return ng
        return ng


def attach_loopback(sim: IcomSimulator, end: LoopbackEnd) -> None:
    """Make the simulator answer on one end of a loopback pair (end must be opened)."""

    def deliver(reply: bytes) -> None:
        # Write directly, not through the queue, like a real UART would.
        peer = end.peer
        sim.reply_sent()
        if end.is_open and peer is not None and peer.is_open:
            peer._deliver(reply)

    def on_data(data: bytes) -> None:
        reply = sim.handle_bytes(data, time.monotonic())
        if reply:
            asyncio.get_running_loop().call_later(sim.reply_delay_s, deliver, reply)

    end.on_data = on_data


def loopback_transport(sim: IcomSimulator) -> LoopbackEnd:
    """A fresh driver-side transport wired to this simulator (for RadioStation factories)."""
    from radio_web_remote.transport.loopback import loopback_pair

    host, radio_end = loopback_pair()
    attach_loopback(sim, radio_end)
    radio_end._open = True  # the radio side never writes through its queue; it is just "on"
    return host


def push_unsolicited(end: LoopbackEnd, data: bytes) -> None:
    """Deliver radio-originated bytes (transceive, junk) to the driver side."""
    peer = end.peer
    if data and peer is not None and peer.is_open:
        peer._deliver(data)


class PtyRunner:
    """Serve the simulator on a POSIX pseudo-terminal. `path` is what a driver opens."""

    # Declared here because on Windows mypy treats the body of __init__ after
    # the platform check as unreachable and would otherwise not type these.
    sim: IcomSimulator
    path: str
    _master: int
    _slave: int
    _stop: threading.Event
    _lock: threading.Lock
    _thread: threading.Thread

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
                    reply = self.sim.handle_bytes(data, time.monotonic())
                if reply:
                    time.sleep(self.sim.reply_delay_s)
                    self.inject(reply)
                    with self._lock:
                        self.sim.reply_sent()

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
