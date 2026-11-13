"""RadioDriver for Icom radios over CI-V, driven by a model capability table.

One command on the wire at a time. Every command, PTT included, waits for
its reply (a frame with the same command prefix for reads, FB/FA for sets)
or a timeout before the next is written. Observed on an IC-7300 over USB
(2026-11-13): when two commands arrive back to back the radio answers only
one of them; with a gap of 2 ms or more it answers both.

The bus is a PriorityLock: PTT requests are first in line, ahead of every
waiting command, but wait for the one command already in flight (normally a
few ms). If that in-flight command is stuck, unkey stops waiting after
UNKEY_BUS_WAIT_S and writes TX off anyway; the read-back then confirms it.

A PTT generation counter makes a key request that was overtaken by an
unkey cancel itself instead of keying after the unkey.

Frames from the radio that do not answer the in-flight command are
unsolicited (transceive) and become StateEvents.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging

from radio_web_remote.asyncutil import PriorityLock
from radio_web_remote.protocols.civ import commands as cmd
from radio_web_remote.protocols.civ.commands import CivCommand, ReplyError
from radio_web_remote.protocols.civ.frame import Frame, FrameParser, encode_frame
from radio_web_remote.radios.base import (
    Feature,
    Level,
    Meter,
    MeterReading,
    Mode,
    ModeSetting,
    RadioDriver,
    RadioRejected,
    RadioTimeout,
    StateEvent,
    Unsupported,
)
from radio_web_remote.radios.icom.model import LEVEL_COMMANDS, METER_COMMANDS, IcomModel, MeterCal
from radio_web_remote.transport.base import ByteTransport, Priority

log = logging.getLogger(__name__)

DEFAULT_CONTROLLER = 0xE0
# Transceive frames are addressed to 00 (broadcast): observed on an IC-7300,
# 2026-11-13 (docs/hardware-test.md, H7300-6). The controller address is
# accepted too, in case another model differs.
BROADCAST = 0x00
UNKEY_BUS_WAIT_S = 0.05
# Observed on an IC-7300 (2026-11-13, docs/hardware-test.md H7300-4): the
# radio acknowledges TX off at once but still reports TX for roughly 15 to
# 65 ms. Confirmation therefore polls for a while instead of reading once.
UNKEY_CONFIRM_S = 0.5  # give up and raise after this long
UNKEY_POLL_S = 0.02  # between TX-state reads
UNKEY_RESEND_S = 0.15  # resend TX off if still TX after this long since the last send
COMMAND_RETRIES = 1  # resend once on timeout; every command we send is idempotent


class _Pending:
    __slots__ = ("expect_prefix", "future")

    def __init__(self, expect_prefix: bytes | None, future: asyncio.Future[Frame]) -> None:
        self.expect_prefix = expect_prefix  # None = expecting FB/FA
        self.future = future

    def matches(self, frame: Frame) -> bool:
        if frame.is_ng:
            return True
        if self.expect_prefix is None:
            return frame.is_ok
        return frame.payload.startswith(self.expect_prefix) and len(frame.payload) > len(
            self.expect_prefix
        )


class IcomCivDriver(RadioDriver):
    def __init__(
        self,
        model: IcomModel,
        transport: ByteTransport,
        address: int | None = None,
        controller_address: int | None = None,
        response_timeout_s: float = 0.5,
    ) -> None:
        super().__init__()
        self.model = model
        self.transport = transport
        self.address = model.default_address.value if address is None else address
        self.controller = DEFAULT_CONTROLLER if controller_address is None else controller_address
        self.timeout = response_timeout_s
        self.parser = FrameParser()
        self.echoes_seen = 0
        self._bus = PriorityLock()
        self._pending: _Pending | None = None
        self._ptt_generation = 0

    # -- RadioDriver properties ----------------------------------------
    @property
    def model_name(self) -> str:
        return self.model.name

    @property
    def features(self) -> frozenset[Feature]:
        return self.model.features

    @property
    def modes(self) -> tuple[Mode, ...]:
        return tuple(self.model.modes)

    # -- lifecycle -------------------------------------------------------
    async def connect(self) -> None:
        self.transport.on_data = self._on_bytes
        await self.transport.open()
        # ADR D6 item 6: TX off is the first thing on the wire after (re)connect.
        # This is what clears a key left by a crashed process (H7300-4, case C).
        # Confirm it, but do not fail the connect if the radio is silent.
        try:
            await self.unkey(verify=True)
        except RadioTimeout as exc:
            log.warning("TX off on connect not confirmed: %s", exc)

    async def close(self) -> None:
        if self.transport.is_open:
            try:
                await self.unkey(verify=False)
            finally:
                await self.transport.close()

    # -- frame plumbing --------------------------------------------------
    def _frame(self, payload: bytes) -> bytes:
        return encode_frame(self.address, self.controller, payload)

    def _on_bytes(self, data: bytes) -> None:
        for frame in self.parser.feed(data):
            self._on_frame(frame)

    def _on_frame(self, frame: Frame) -> None:
        if frame.from_addr == self.controller:
            # USB Echo Back: our own frame coming back. Drop it.
            self.echoes_seen += 1
            return
        if frame.from_addr != self.address:
            return
        if frame.to_addr not in (self.controller, BROADCAST):
            return
        pending = self._pending
        if pending is not None and not pending.future.done() and pending.matches(frame):
            pending.future.set_result(frame)
            return
        self._unsolicited(frame)

    def _unsolicited(self, frame: Frame) -> None:
        try:
            if frame.cmd == CivCommand.TRANSCEIVE_FREQUENCY.value[0]:
                self._emit(StateEvent("frequency", cmd.parse_frequency(frame.payload)))
            elif frame.cmd == CivCommand.TRANSCEIVE_MODE.value[0]:
                self._emit(StateEvent("mode", self._mode_setting(*cmd.parse_mode(frame.payload))))
            elif frame.payload.startswith(CivCommand.TX_STATE.prefix):
                self._emit(StateEvent("ptt", cmd.parse_tx_state(frame.payload)))
        except (ReplyError, ValueError) as exc:
            log.debug("ignoring unsolicited frame %r: %s", frame, exc)

    async def _exchange(self, command: CivCommand, payload: bytes, expect_reply: bool) -> Frame:
        """Write one frame and wait for its answer. Caller must hold the bus."""
        fut: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
        self._pending = _Pending(command.prefix if expect_reply else None, fut)
        try:
            await self.transport.send_urgent(self._frame(payload))
            try:
                reply = await asyncio.wait_for(fut, self.timeout)
            except TimeoutError:
                raise RadioTimeout(
                    f"{self.model.name} did not answer {command.name} within {self.timeout}s"
                ) from None
        finally:
            self._pending = None
        if reply.is_ng:
            raise RadioRejected(f"{self.model.name} rejected {command.name} ({payload.hex(' ')})")
        return reply

    async def _transact(
        self,
        command: CivCommand,
        payload: bytes,
        expect_reply: bool,
        priority: Priority = Priority.CONTROL,
    ) -> Frame:
        self._require(command)
        for attempt in range(COMMAND_RETRIES + 1):
            async with self._bus.acquire(priority):
                try:
                    return await self._exchange(command, payload, expect_reply)
                except RadioTimeout:
                    if attempt == COMMAND_RETRIES:
                        raise
                    log.debug("%s timed out, retrying", command.name)
        raise AssertionError("unreachable")

    def _require(self, command: CivCommand) -> None:
        if not self.model.supports(command):
            raise Unsupported(f"{self.model.name} table does not document {command.name}")

    def _mode_setting(self, mode_code: int, filter_code: int | None) -> ModeSetting:
        mode = self.model.mode_from_code(mode_code)
        if mode is None:
            raise ReplyError(f"unknown mode code {mode_code:02X} for {self.model.name}")
        flt = None if filter_code is None else self.model.filter_from_code(filter_code)
        return ModeSetting(mode, flt)

    # -- frequency / mode ------------------------------------------------
    async def get_frequency(self) -> int:
        reply = await self._transact(CivCommand.READ_FREQUENCY, cmd.read_frequency(), True)
        return cmd.parse_frequency(reply.payload)

    async def set_frequency(self, hz: int) -> None:
        if not 0 < hz <= self.model.max_encodable_hz:
            raise ValueError(f"{hz} Hz is outside what {self.model.name} can encode")
        await self._transact(CivCommand.SET_FREQUENCY, cmd.set_frequency(hz), False)

    async def get_mode(self) -> ModeSetting:
        reply = await self._transact(CivCommand.READ_MODE, cmd.read_mode(), True)
        return self._mode_setting(*cmd.parse_mode(reply.payload))

    async def set_mode(self, setting: ModeSetting) -> None:
        entry = self.model.modes.get(setting.mode)
        if entry is None:
            raise Unsupported(f"{self.model.name} table has no code for mode {setting.mode.value}")
        filter_code: int | None = None
        if setting.filter is not None:
            f = self.model.filters.get(setting.filter)
            if f is None:
                raise Unsupported(f"{self.model.name} table has no filter {setting.filter}")
            filter_code = f.value
        await self._transact(CivCommand.SET_MODE, cmd.set_mode(entry.value, filter_code), False)

    # -- levels, meters, DATA mode -----------------------------------------
    async def get_level(self, level: Level) -> int:
        command = LEVEL_COMMANDS[level]
        reply = await self._transact(command, cmd.read_value(command), True, Priority.POLL)
        return cmd.parse_level(command, reply.payload)

    async def set_level(self, level: Level, value: int) -> None:
        command = LEVEL_COMMANDS[level]
        await self._transact(command, cmd.set_level(command, value), False)

    async def get_meter(self, meter: Meter) -> MeterReading:
        command = METER_COMMANDS[meter]
        cal = self.model.meters.get(meter)
        if cal is None:
            raise Unsupported(f"{self.model.name} table has no calibration for {meter.value}")
        reply = await self._transact(command, cmd.read_value(command), True, Priority.POLL)
        return convert_meter(meter, cmd.parse_level(command, reply.payload), cal)

    async def get_data_mode(self) -> bool:
        command = CivCommand.DATA_MODE
        reply = await self._transact(command, cmd.read_value(command), True, Priority.POLL)
        on, _ = cmd.parse_data_mode(reply.payload)
        return on

    async def set_data_mode(self, on: bool, filter: int | None = None) -> None:
        code = None
        if filter is not None:
            entry = self.model.filters.get(filter)
            if entry is None:
                raise Unsupported(f"{self.model.name} table has no filter {filter}")
            code = entry.value
        await self._transact(CivCommand.DATA_MODE, cmd.set_data_mode(on, code), False)

    # -- PTT -------------------------------------------------------------
    async def get_ptt(self) -> bool:
        reply = await self._transact(
            CivCommand.TX_STATE, cmd.read_tx_state(), True, priority=Priority.URGENT
        )
        return cmd.parse_tx_state(reply.payload)

    async def key(self) -> bool:
        """Key the transmitter. Returns False if an unkey overtook this request."""
        self._require(CivCommand.TX_STATE)
        generation = self._ptt_generation
        async with self._bus.acquire(Priority.URGENT):
            if self._ptt_generation != generation:
                return False
            # A key is newer than any unkey still confirming: that unkey's
            # read-back loop must stop rather than send TX off again.
            self._ptt_generation += 1
            await self._exchange(CivCommand.TX_STATE, cmd.set_tx_state(True), False)
        return True

    async def unkey(self, verify: bool = True) -> None:
        """Send TX off first in line; with verify, read back until the radio reports RX."""
        self._require(CivCommand.TX_STATE)
        self._ptt_generation += 1
        generation = self._ptt_generation
        payload = cmd.set_tx_state(False)
        try:
            async with self._bus.acquire(Priority.URGENT, wait_s=UNKEY_BUS_WAIT_S):
                # A missing FB is handled by the read-back below.
                with contextlib.suppress(RadioTimeout):
                    await self._exchange(CivCommand.TX_STATE, payload, False)
        except TimeoutError:
            # The command in flight is stuck. Do not wait for it: send TX off now.
            log.warning("bus busy for %.0f ms; sending TX off anyway", UNKEY_BUS_WAIT_S * 1000)
            await self.transport.send_urgent(self._frame(payload))
        if not verify:
            return
        loop = asyncio.get_running_loop()
        start = last_send = loop.time()
        while loop.time() - start < UNKEY_CONFIRM_S:
            if self._ptt_generation != generation:
                return  # a newer key or unkey owns the PTT state now
            with contextlib.suppress(RadioTimeout):
                if not await self.get_ptt():
                    return
            if loop.time() - last_send >= UNKEY_RESEND_S:
                async with self._bus.acquire(Priority.URGENT):
                    with contextlib.suppress(RadioTimeout):
                        await self._exchange(CivCommand.TX_STATE, payload, False)
                last_send = loop.time()
            await asyncio.sleep(UNKEY_POLL_S)
        raise RadioTimeout(
            f"{self.model.name} did not report RX within {UNKEY_CONFIRM_S}s of TX off"
        )


def _interpolate(points: tuple[tuple[int, float], ...], raw: int) -> float:
    if raw <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        if raw <= x1:
            return y0 + (y1 - y0) * (raw - x0) / (x1 - x0)
    return points[-1][1]


def convert_meter(meter: Meter, raw: int, cal: MeterCal) -> MeterReading:
    """Map a raw 0..255 reading through the manual's calibration points.

    Readings beyond the last printed point are reported as the last point's
    value with raw kept, so the UI can show "over" instead of inventing a number.
    """
    last_raw = cal.points[-1][0]
    if cal.over_s9_points is not None and raw > last_raw:
        over = _interpolate(cal.over_s9_points, raw)
        return MeterReading(meter, raw, cal.points[-1][1], cal.unit, round(over, 1))
    value = None if raw > last_raw else round(_interpolate(cal.points, raw), 2)
    return MeterReading(meter, raw, value, cal.unit)
