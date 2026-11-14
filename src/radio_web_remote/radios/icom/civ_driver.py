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
import functools
import itertools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, TypeVar

from radio_web_remote.asyncutil import PriorityLock
from radio_web_remote.protocols.civ import commands as cmd
from radio_web_remote.protocols.civ import scope as sc
from radio_web_remote.protocols.civ.commands import CivCommand, ReplyError
from radio_web_remote.protocols.civ.frame import Frame, FrameParser, encode_frame
from radio_web_remote.radios.base import (
    Feature,
    Level,
    Meter,
    MeterReading,
    Mode,
    ModeSetting,
    RadioBadReply,
    RadioDriver,
    RadioError,
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
COMMAND_RETRIES = 1  # resend once on timeout (not for 07 B0 swap, which is not idempotent)
LATE_REPLY_DRAIN_S = 0.05  # after a timeout, swallow a late FB/FA before the next command


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


_Read = TypeVar("_Read", bound=Callable[..., Awaitable[Any]])


def _reads(fn: _Read) -> _Read:
    """A reply in an unexpected form (ReplyError, BcdError: ValueErrors) becomes
    RadioBadReply, a RadioError that every caller already handles."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except RadioError:
            raise
        except ValueError as exc:
            raise RadioBadReply(f"unexpected reply: {exc}") from exc

    return wrapper  # type: ignore[return-value]


class IcomCivDriver(RadioDriver):
    def __init__(
        self,
        model: IcomModel,
        transport: ByteTransport,
        address: int | None = None,
        controller_address: int | None = None,
        response_timeout_s: float = 0.5,
        auto_detect_address: bool = False,
    ) -> None:
        super().__init__()
        self.auto_detect_address = auto_detect_address
        self._detected: list[tuple[int, bytes]] = []  # (address, ID) seen while detecting
        self._detecting = False
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
        self._last_ptt_was_key = False
        spec = model.scope
        self.scope = sc.ScopeAssembler(spec.points, spec.max_value) if spec else None
        # 27 1x selector byte: the scope's band (00 MAIN / 01 SUB). Always 00 on
        # single-band radios; on the IC-9700 it follows 27 12 and the 27 00 data.
        self._scope_band = 0

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
            if not (self.auto_detect_address and await self._detect_address()):
                log.warning(
                    "TX off on connect not confirmed: %s. If the radio is on, check its CI-V "
                    "address (menu) against civ_address, or set auto_detect_address: true.",
                    exc,
                )
        if self.model.supports(CivCommand.SCOPE_BAND):
            with contextlib.suppress(RadioError):
                self._scope_band = await self._read_flag(CivCommand.SCOPE_BAND)

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

    async def _detect_address(self) -> bool:
        """Silent at the configured address: broadcast read ID and adopt the one answer.

        The radio's CI-V address is a menu setting, and Icom's IC-705 manual
        suggests changing it for software that expects another model; a PRESET
        can change it too (705-AM 9-9). Seen on hardware: an IC-705 set to 94
        answered the broadcast from 94 with ID A4 (H705-1). TX off goes to the
        found address straight away.
        """
        if not self.model.supports(CivCommand.READ_ID):
            log.warning("%s: address auto-detect needs 19 00, not in its table", self.model.name)
            return False
        self._detected.clear()
        self._detecting = True
        try:
            frame = encode_frame(BROADCAST, self.controller, CivCommand.READ_ID.prefix)
            async with self._bus.acquire(Priority.URGENT):
                await self.transport.send_urgent(frame)
                await asyncio.sleep(self.timeout)
        finally:
            self._detecting = False
        found = {addr for addr, _ in self._detected}
        if len(found) != 1:
            log.warning(
                "%s: address auto-detect got %s",
                self.model.name,
                "no answer" if not found else f"answers from {sorted(found)}",
            )
            return False
        old, self.address = self.address, found.pop()
        log.warning(
            "%s did not answer at %02X but answered at %02X (ID %s); using %02X. "
            'Set civ_address: "%02X" in the config to skip this.',
            self.model.name,
            old,
            self.address,
            self._detected[0][1].hex().upper() or "-",
            self.address,
            self.address,
        )
        try:
            await self.unkey(verify=True)
        except RadioTimeout as exc:
            log.warning("TX off at the found address not confirmed: %s", exc)
        return True

    def _on_frame(self, frame: Frame) -> None:
        if self._detecting and frame.payload.startswith(CivCommand.READ_ID.prefix):
            if frame.to_addr == self.controller and frame.from_addr != self.controller:
                self._detected.append((frame.from_addr, frame.payload[2:]))
            return
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
        if frame.payload.startswith(sc.SCOPE_DATA):
            band = frame.payload[2] if len(frame.payload) > 2 else None
            if band in (0, 1) and self.model.supports(CivCommand.SCOPE_BAND):
                self._scope_band = band  # 27 00 byte 1: 00 Main / 01 Sub scope
            if self.scope is not None:
                try:
                    line = self.scope.feed(frame.payload)
                except (ReplyError, ValueError) as exc:
                    log.debug("bad scope frame: %s", exc)
                    return
                if line is not None:
                    self._emit(StateEvent("scope", line))
            return
        try:
            if frame.cmd == CivCommand.TRANSCEIVE_FREQUENCY.value[0]:
                self._emit(StateEvent("frequency", cmd.parse_frequency(frame.payload)))
            elif frame.cmd == CivCommand.TRANSCEIVE_MODE.value[0]:
                self._emit(StateEvent("mode", self._mode_setting(*cmd.parse_mode(frame.payload))))
            elif frame.payload.startswith(CivCommand.TX_STATE.prefix):
                self._emit(StateEvent("ptt", cmd.parse_tx_state(frame.payload)))
        except (ReplyError, ValueError) as exc:
            log.debug("ignoring unsolicited frame %r: %s", frame, exc)

    async def _exchange(
        self,
        command: CivCommand,
        payload: bytes,
        expect_reply: bool,
        wait_s: float | None = None,
    ) -> Frame:
        """Write one frame and wait for its answer. Caller must hold the bus."""
        wait = self.timeout if wait_s is None else max(0.0, wait_s)
        fut: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
        self._pending = _Pending(command.prefix if expect_reply else None, fut)
        try:
            try:
                await self.transport.send_urgent(self._frame(payload))
            except OSError as exc:  # serial error or closed transport (ConnectionError)
                raise RadioError(f"{self.model.name} link error: {exc}") from exc
            try:
                reply = await asyncio.wait_for(fut, wait)
            except TimeoutError:
                # Replies carry no command id: an FB/FA arriving late would be
                # taken as the answer to the next command. Let it land as an
                # unsolicited frame (ignored) before giving up the bus.
                self._pending = None
                await asyncio.sleep(LATE_REPLY_DRAIN_S)
                raise RadioTimeout(
                    f"{self.model.name} did not answer {command.name} within {wait:.2f}s"
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
        retries: int = COMMAND_RETRIES,
    ) -> Frame:
        self._require(command)
        for attempt in range(retries + 1):
            async with self._bus.acquire(priority):
                try:
                    return await self._exchange(command, payload, expect_reply)
                except RadioTimeout:
                    if attempt == retries:
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
    @_reads
    async def get_frequency(self) -> int:
        reply = await self._transact(CivCommand.READ_FREQUENCY, cmd.read_frequency(), True)
        return cmd.parse_frequency(reply.payload)

    @_reads
    async def get_tx_frequency(self) -> int:
        self._require(CivCommand.READ_TX_FREQUENCY)
        command = CivCommand.READ_TX_FREQUENCY
        reply = await self._transact(command, command.prefix, True, Priority.URGENT)
        return cmd.decode_frequency_reply(command, reply.payload)

    async def set_frequency(self, hz: int) -> None:
        if not 0 < hz <= self.model.max_encodable_hz:
            raise ValueError(f"{hz} Hz is outside what {self.model.name} can encode")
        await self._transact(CivCommand.SET_FREQUENCY, cmd.set_frequency(hz), False)

    @_reads
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
    @_reads
    async def get_level(self, level: Level) -> int:
        command = LEVEL_COMMANDS[level]
        reply = await self._transact(command, cmd.read_value(command), True, Priority.POLL)
        return cmd.parse_level(command, reply.payload)

    async def set_level(self, level: Level, value: int) -> None:
        command = LEVEL_COMMANDS[level]
        await self._transact(command, cmd.set_level(command, value), False)

    @_reads
    async def get_meter(self, meter: Meter) -> MeterReading:
        command = METER_COMMANDS[meter]
        cal = self.model.meters.get(meter)
        if cal is None:
            raise Unsupported(f"{self.model.name} table has no calibration for {meter.value}")
        reply = await self._transact(command, cmd.read_value(command), True, Priority.POLL)
        return convert_meter(meter, cmd.parse_level(command, reply.payload), cal)

    @_reads
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

    # -- scope -----------------------------------------------------------
    _SCOPE_SETTINGS: ClassVar[dict[str, CivCommand]] = {
        "mode": CivCommand.SCOPE_MODE,
        "span": CivCommand.SCOPE_SPAN,
        "edge": CivCommand.SCOPE_EDGE,
        "hold": CivCommand.SCOPE_HOLD,
        "ref": CivCommand.SCOPE_REF,
        "speed": CivCommand.SCOPE_SPEED,
    }

    async def set_scope_output(self, on: bool) -> None:
        self._require(CivCommand.SCOPE_OUTPUT)
        if on:
            reply = await self._transact(CivCommand.SCOPE_ON, CivCommand.SCOPE_ON.prefix, True)
            if reply.payload[-1:] != b"\x01":  # the radio's own scope must be on (p. 19-7)
                await self._transact(
                    CivCommand.SCOPE_ON, CivCommand.SCOPE_ON.prefix + b"\x01", False
                )
        flag = b"\x01" if on else b"\x00"
        await self._transact(CivCommand.SCOPE_OUTPUT, CivCommand.SCOPE_OUTPUT.prefix + flag, False)

    def scope_modes(self) -> tuple[str, ...]:
        return self.model.scope.modes if self.model.scope else ()

    def scope_edges(self) -> int:
        return self.model.scope.edges if self.model.scope else 0

    def coverage_hz(self) -> tuple[tuple[int, int], ...]:
        return self.model.coverage_hz

    def scope_settings_supported(self) -> list[str]:
        return [k for k, c in self._SCOPE_SETTINGS.items() if self.model.supports(c)]

    @_reads
    async def get_scope_setting(self, name: str) -> Any:
        command = self._SCOPE_SETTINGS[name]
        sel = bytes([self._scope_band])
        reply = await self._transact(command, command.prefix + sel, True, Priority.POLL)
        data = reply.payload[len(command.prefix) + 1 :]  # after the selector
        if name == "span":
            return sc.decode_span(data)
        if name == "ref":
            return sc.decode_ref_level(data)
        if not data:
            raise ReplyError(f"empty {name} reply")
        value = data[0]
        if name == "mode":
            modes = self.scope_modes()
            return modes[value] if value < len(modes) else f"mode {value:02X} (not in manual)"
        if name == "speed":
            return sc.SPEEDS[value] if value < len(sc.SPEEDS) else f"speed {value:02X}"
        if name == "hold":
            return value == 1
        return value  # edge number

    async def set_scope_setting(self, name: str, value: Any) -> None:
        command = self._SCOPE_SETTINGS[name]
        self._require(command)
        if name == "mode":
            modes = self.scope_modes()
            if value not in modes:
                raise ValueError(f"scope mode must be one of {', '.join(modes)}")
            data = bytes([modes.index(value)])
        elif name == "span":
            data = sc.encode_span(int(value))
        elif name == "edge":
            edges = self.scope_edges()
            if isinstance(value, bool) or value not in range(1, edges + 1):
                raise ValueError(f"edge must be 1 to {edges}")
            data = bytes([value])
        elif name == "hold":
            data = b"\x01" if value else b"\x00"
        elif name == "ref":
            data = sc.encode_ref_level(float(value))
        elif name == "speed":
            data = bytes([sc.SPEEDS.index(value)])
        else:
            raise ValueError(f"unknown scope setting {name!r}")
        await self._transact(command, command.prefix + bytes([self._scope_band]) + data, False)

    # -- MAIN / SUB (IC-9700) ------------------------------------------------
    @_reads
    async def _read_flag(self, command: CivCommand) -> int:
        """Read a command answering prefix + 00/01."""
        self._require(command)
        reply = await self._transact(command, command.prefix, True, Priority.POLL)
        data = reply.payload[len(command.prefix) :]
        if len(data) != 1 or data[0] not in (0, 1):
            raise ReplyError(f"bad {command.name} reply: {reply.payload.hex(' ')}")
        return data[0]

    async def get_band(self) -> str:
        return ("main", "sub")[await self._read_flag(CivCommand.BAND_SELECTION)]

    async def set_band(self, band: str) -> None:
        command = {"main": CivCommand.BAND_MAIN, "sub": CivCommand.BAND_SUB}.get(band)
        if command is None:
            raise ValueError("band must be main or sub")
        self._require(command)
        await self._transact(command, command.prefix, False)
        if self.model.supports(CivCommand.SCOPE_BAND):  # the scope follows the selected band
            index = 0 if band == "main" else 1
            await self._transact(
                CivCommand.SCOPE_BAND, CivCommand.SCOPE_BAND.prefix + bytes([index]), False
            )
            self._scope_band = index

    async def swap_bands(self) -> None:
        self._require(CivCommand.BAND_EXCHANGE)
        # Not retried: if the first swap was done but its FB was lost, a second
        # would swap back and report success.
        await self._transact(
            CivCommand.BAND_EXCHANGE, CivCommand.BAND_EXCHANGE.prefix, False, retries=0
        )

    async def get_dualwatch(self) -> bool:
        return await self._read_flag(CivCommand.DUALWATCH) == 1

    async def set_dualwatch(self, on: bool) -> None:
        self._require(CivCommand.DUALWATCH)
        flag = b"\x01" if on else b"\x00"
        await self._transact(CivCommand.DUALWATCH, CivCommand.DUALWATCH.prefix + flag, False)

    async def get_satellite(self) -> bool:
        return await self._read_flag(CivCommand.SATELLITE) == 1

    # -- PTT -------------------------------------------------------------
    @_reads
    async def get_ptt(self, retries: int = COMMAND_RETRIES) -> bool:
        reply = await self._transact(
            CivCommand.TX_STATE, cmd.read_tx_state(), True, Priority.URGENT, retries
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
            self._last_ptt_was_key = True
            await self._exchange(CivCommand.TX_STATE, cmd.set_tx_state(True), False)
        return True

    async def unkey(self, verify: bool = True) -> bool:
        """Send TX off first in line; with verify, read back until the radio reports RX.

        Returns False only if a newer key took over before RX was seen. A newer
        unkey wants the same thing, so this one keeps confirming.
        """
        self._require(CivCommand.TX_STATE)
        self._ptt_generation += 1
        self._last_ptt_was_key = False
        generation = self._ptt_generation
        payload = cmd.set_tx_state(False)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + UNKEY_CONFIRM_S  # the whole confirmation, from the first send

        def keyed_since() -> bool:
            return self._ptt_generation != generation and self._last_ptt_was_key

        def budget(cap: float) -> float:
            return min(self.timeout, cap, max(0.0, deadline - loop.time()))

        try:
            async with self._bus.acquire(Priority.URGENT, wait_s=UNKEY_BUS_WAIT_S):
                # A missing FB, an FA (possibly a late one for an earlier command)
                # or a link error: the read-back below decides, and resends. Its
                # FB is not waited for longer than a resend interval.
                with contextlib.suppress(RadioError):
                    await self._exchange(
                        CivCommand.TX_STATE, payload, False, wait_s=budget(UNKEY_RESEND_S)
                    )
        except TimeoutError:
            # The command in flight is stuck. Do not wait for it: send TX off now,
            # and do not let its FB complete the stuck command as if acknowledged.
            log.warning("bus busy for %.0f ms; sending TX off anyway", UNKEY_BUS_WAIT_S * 1000)
            self._pending = None
            with contextlib.suppress(OSError):
                await self.transport.send_urgent(self._frame(payload))
        if not verify:
            return True
        last_send = loop.time()
        while loop.time() < deadline:
            if keyed_since():
                return False  # a newer key owns the PTT state now
            with contextlib.suppress(RadioError):  # one attempt per read, within the budget
                async with self._bus.acquire(Priority.URGENT):
                    reply = await self._exchange(
                        CivCommand.TX_STATE, cmd.read_tx_state(), True, wait_s=budget(deadline)
                    )
                if not cmd.parse_tx_state(reply.payload):
                    return True
            if loop.time() - last_send >= UNKEY_RESEND_S:
                async with self._bus.acquire(Priority.URGENT):
                    # Checked while holding the bus: a key cannot slip in between
                    # this check and the resend, which would unkey it.
                    if keyed_since():
                        return False
                    with contextlib.suppress(RadioError):
                        await self._exchange(
                            CivCommand.TX_STATE, payload, False, wait_s=budget(UNKEY_RESEND_S)
                        )
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
