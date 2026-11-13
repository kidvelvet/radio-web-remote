"""RadioDriver for Icom radios over CI-V, driven by a model capability table.

Request/response: one request is outstanding at a time (CI-V is a shared
half-duplex bus by design). A read is answered by a frame with the same
command prefix; a set is answered by FB (OK) or FA (NG). Frames from the
radio that do not answer the outstanding request are unsolicited
(transceive) and become StateEvents.

PTT never waits for the request lock. Both key and unkey go out through the
transport's urgent path, in call order, and each bumps or checks a PTT
generation counter so a key request overtaken by an unkey is dropped.
"""

from __future__ import annotations

import asyncio
import logging

from radio_web_remote.protocols.civ import commands as cmd
from radio_web_remote.protocols.civ.commands import CivCommand, ReplyError
from radio_web_remote.protocols.civ.frame import Frame, FrameParser, encode_frame
from radio_web_remote.radios.base import (
    Feature,
    Mode,
    ModeSetting,
    RadioDriver,
    RadioRejected,
    RadioTimeout,
    StateEvent,
    Unsupported,
)
from radio_web_remote.radios.icom.model import IcomModel
from radio_web_remote.transport.base import ByteTransport, Priority

log = logging.getLogger(__name__)

DEFAULT_CONTROLLER = 0xE0
# TODO(verify): transceive output is assumed to be addressed to 00 (broadcast)
# or to the controller. Accept both until confirmed on hardware.
BROADCAST = 0x00
UNKEY_RETRIES = 3


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
        self._req_lock = asyncio.Lock()
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
        await self.unkey(verify=False)

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

    async def _transact(
        self,
        command: CivCommand,
        payload: bytes,
        expect_reply: bool,
        priority: Priority = Priority.CONTROL,
    ) -> Frame:
        self._require(command)
        async with self._req_lock:
            fut: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
            self._pending = _Pending(command.prefix if expect_reply else None, fut)
            try:
                await self.transport.send(self._frame(payload), priority)
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

    # -- PTT -------------------------------------------------------------
    async def get_ptt(self) -> bool:
        reply = await self._transact(CivCommand.TX_STATE, cmd.read_tx_state(), True)
        return cmd.parse_tx_state(reply.payload)

    async def key(self) -> bool:
        self._require(CivCommand.TX_STATE)
        generation = self._ptt_generation
        return await self.transport.send_urgent(
            self._frame(cmd.set_tx_state(True)),
            guard=lambda: self._ptt_generation == generation,
        )

    async def unkey(self, verify: bool = True) -> None:
        """Send TX off ahead of all queued traffic; optionally confirm by reading back."""
        self._require(CivCommand.TX_STATE)
        self._ptt_generation += 1
        frame = self._frame(cmd.set_tx_state(False))
        await self.transport.send_urgent(frame)
        if not verify:
            return
        for _ in range(UNKEY_RETRIES):
            try:
                if not await self.get_ptt():
                    return
            except RadioTimeout:
                pass
            await self.transport.send_urgent(frame)
        raise RadioTimeout(f"{self.model.name} did not confirm RX after {UNKEY_RETRIES} retries")
