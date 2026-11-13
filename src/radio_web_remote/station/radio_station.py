"""One radio: its link, driver, state cache, TX controller, and reconnect loop.

- On every (re)connect the driver sends TX off before anything else.
- State comes from the radio's own transceive frames plus a slow poll
  (ADR 0001 D4). Hardware showed one transceive frame can be missed
  (docs/hardware-test.md, H7300-2), so the poll is not optional.
- Losing the link (serial error, or several poll timeouts in a row) drops
  any TX holder, marks the radio disconnected, and retries with backoff.
- Controls are refused for listeners, and for anyone but the TX holder
  while someone is transmitting.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from radio_web_remote.config import PttConfig, RadioConfig
from radio_web_remote.devices.serial_ports import resolve_port
from radio_web_remote.radios import registry
from radio_web_remote.radios.base import (
    ModeSetting,
    RadioDriver,
    RadioError,
    RadioTimeout,
    StateEvent,
)
from radio_web_remote.station.tx_controller import (
    AuditSink,
    Operator,
    Role,
    TxController,
    TxStatus,
)
from radio_web_remote.transport.base import ByteTransport
from radio_web_remote.transport.serial_port import SerialTransport

log = logging.getLogger(__name__)

TransportFactory = Callable[[], ByteTransport]
StateListener = Callable[["RadioState"], None]


class ControlDenied(PermissionError):
    pass


class RadioLost(ConnectionError):
    pass


@dataclass(frozen=True)
class RadioState:
    radio_id: str
    model: str | None = None
    connected: bool = False
    error: str | None = None
    frequency_hz: int | None = None
    mode: str | None = None
    filter: int | None = None
    features: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()
    tx: TxStatus = field(default_factory=lambda: TxStatus(False))

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["tx"] = {
            "transmitting": self.tx.transmitting,
            "holder": self.tx.holder_callsign,
            "since": self.tx.since,
            "last_stop": self.tx.last_stop_reason.value if self.tx.last_stop_reason else None,
        }
        d["features"] = list(self.features)
        d["modes"] = list(self.modes)
        return d


class _PttProxy:
    """Lets the TX controller outlive driver instances across reconnects."""

    def __init__(self, station: RadioStation) -> None:
        self._station = station

    async def key(self) -> bool:
        driver = self._station._live_driver()
        return await driver.key()

    async def unkey(self, verify: bool = True) -> None:
        driver = self._station._live_driver()
        await driver.unkey(verify)


class RadioStation:
    def __init__(
        self,
        cfg: RadioConfig,
        ptt: PttConfig,
        transport_factory: TransportFactory | None = None,
        audit: AuditSink | None = None,
        poll_interval_s: float = 1.0,
        lost_after_timeouts: int = 3,
        backoff_s: tuple[float, float] = (0.5, 5.0),
        response_timeout_s: float = 0.5,
        tx_watchdog_s: float = 0.1,
    ) -> None:
        self.cfg = cfg
        self._factory = transport_factory or self._serial_factory
        self._poll_interval = poll_interval_s
        self._lost_after = lost_after_timeouts
        self._backoff = backoff_s
        self._response_timeout = response_timeout_s
        self._driver: RadioDriver | None = None
        self._connected = False
        self._connected_once = False
        self.state = RadioState(cfg.id)
        self._listeners: list[StateListener] = []
        self._task: asyncio.Task[None] | None = None
        self._bg: set[asyncio.Task[None]] = set()
        self.tx = TxController(
            cfg.id,
            _PttProxy(self),
            ptt,
            frequency=lambda: self.state.frequency_hz,
            audit=audit,
            watchdog_interval_s=tx_watchdog_s,
        )
        self.tx.add_listener(lambda s: self._update(tx=s))

    # -- lifecycle -------------------------------------------------------
    def _serial_factory(self) -> ByteTransport:
        return SerialTransport(resolve_port(self.cfg.match), self.cfg.baud)

    async def start(self) -> None:
        await self.tx.start()
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"station-{self.cfg.id}")

    async def close(self) -> None:
        await self.tx.close()  # unkeys first
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._drop_driver()

    @property
    def connected(self) -> bool:
        return self._connected

    def _live_driver(self) -> RadioDriver:
        if self._driver is None or not self._connected:
            raise RadioError(f"{self.cfg.id} is not connected")
        return self._driver

    async def _run(self) -> None:
        delay = self._backoff[0]
        while True:
            try:
                await self._serve_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("radio %s link lost: %s", self.cfg.id, exc)
                self._connected = False
                self._update(connected=False, error=str(exc) or type(exc).__name__)
                await self.tx.radio_lost()
                await self._drop_driver()
            if self._connected_once:
                delay = self._backoff[0]  # it worked for a while: start backoff over
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._backoff[1])

    async def _serve_once(self) -> None:
        self._connected_once = False
        transport = self._factory()
        driver = registry.create(
            self.cfg.driver,
            transport,
            registry.DriverOptions(
                address=self.cfg.civ_address,
                controller_address=self.cfg.controller_address,
                response_timeout_s=self._response_timeout,
            ),
        )
        lost = asyncio.Event()
        transport.on_error = lambda exc: lost.set()
        self._driver = driver
        await driver.connect()  # TX off first
        driver.add_listener(self._on_radio_event)
        hz = await driver.get_frequency()
        mode = await driver.get_mode()
        self._connected = True
        self._connected_once = True
        self._update(
            model=driver.model_name,
            connected=True,
            error=None,
            frequency_hz=hz,
            mode=mode.mode.value,
            filter=mode.filter,
            features=tuple(sorted(f.value for f in driver.features)),
            modes=tuple(m.value for m in driver.modes),
        )
        timeouts = 0
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(lost.wait(), self._poll_interval)
            if lost.is_set():
                raise RadioLost("serial link error")
            try:
                await self._poll_once(driver)
                timeouts = 0
            except RadioTimeout:
                timeouts += 1
                if timeouts >= self._lost_after:
                    raise RadioLost(f"no answer to {timeouts} polls in a row") from None

    async def _poll_once(self, driver: RadioDriver) -> None:
        hz = await driver.get_frequency()
        mode = await driver.get_mode()
        if hz != self.state.frequency_hz:
            self._update(frequency_hz=hz)
            await self.tx.frequency_changed(hz)
        if (mode.mode.value, mode.filter) != (self.state.mode, self.state.filter):
            self._update(mode=mode.mode.value, filter=mode.filter)

    async def _drop_driver(self) -> None:
        driver, self._driver = self._driver, None
        self._connected = False
        if driver is not None:
            with contextlib.suppress(Exception):
                await driver.close()

    # -- radio events ------------------------------------------------------
    def _on_radio_event(self, ev: StateEvent) -> None:
        if ev.kind == "frequency" and isinstance(ev.value, int):
            self._update(frequency_hz=ev.value)
            self._spawn(self.tx.frequency_changed(ev.value))
        elif ev.kind == "mode" and isinstance(ev.value, ModeSetting):
            self._update(mode=ev.value.mode.value, filter=ev.value.filter)

    def _spawn(self, coro: Any) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # -- state fan-out -----------------------------------------------------
    def add_listener(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: StateListener) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def _update(self, **changes: Any) -> None:
        new = replace(self.state, **changes)
        if new == self.state:
            return
        self.state = new
        for listener in list(self._listeners):
            try:
                listener(new)
            except Exception:
                log.exception("state listener failed")

    # -- controls ----------------------------------------------------------
    def _require_control(self, op: Operator) -> RadioDriver:
        if op.role is Role.LISTENER:
            raise ControlDenied("listeners cannot change radio settings")
        tx = self.tx.status
        if tx.transmitting and tx.holder_session != op.session_id:
            raise ControlDenied(f"TX held by {tx.holder_callsign}")
        return self._live_driver()

    async def set_frequency(self, op: Operator, hz: int) -> None:
        driver = self._require_control(op)
        await driver.set_frequency(hz)
        self._update(frequency_hz=hz)
        await self.tx.frequency_changed(hz)

    async def set_mode(self, op: Operator, setting: ModeSetting) -> None:
        driver = self._require_control(op)
        await driver.set_mode(setting)
        # Filter omitted means "the mode's default" (p. 19-8); the next poll fills it in.
        self._update(mode=setting.mode.value, filter=setting.filter)
