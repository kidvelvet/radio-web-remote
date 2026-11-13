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

from radio_web_remote.audio.backend import AudioBackend
from radio_web_remote.audio.router import AudioRouter
from radio_web_remote.config import PttConfig, RadioConfig
from radio_web_remote.devices.serial_ports import resolve_port
from radio_web_remote.radios import registry
from radio_web_remote.radios.base import (
    Feature,
    Level,
    Meter,
    MeterReading,
    ModeSetting,
    RadioDriver,
    RadioError,
    RadioTimeout,
    ScopeLine,
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
ScopeListener = Callable[[ScopeLine], None]


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
    data_mode: bool | None = None
    levels: dict[str, int] = field(default_factory=dict)  # "af" -> 0..255
    meters: dict[str, dict[str, Any]] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)  # mode, span, edge, hold, ref, speed
    audio: dict[str, Any] = field(default_factory=dict)  # available, error, host_mode
    tx: TxStatus = field(default_factory=lambda: TxStatus(False))

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["tx"] = {
            "transmitting": self.tx.transmitting,
            "keyed": self.tx.keyed,
            "unconfirmed": self.tx.unkey_unconfirmed,
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
        meter_interval_s: float = 0.25,
        radio_audio: AudioBackend | None = None,
        host_audio: AudioBackend | None = None,
    ) -> None:
        self.cfg = cfg
        self._factory = transport_factory or self._serial_factory
        self._poll_interval = poll_interval_s
        self._lost_after = lost_after_timeouts
        self._backoff = backoff_s
        self._response_timeout = response_timeout_s
        self._meter_interval = meter_interval_s
        self._driver: RadioDriver | None = None
        self._connected = False
        self._connected_once = False
        # Per-field write counters. A poll result is applied only if nothing
        # wrote that field while the poll was in flight; otherwise a stale
        # reading could overwrite a newer value (seen in browser tests).
        self._writes: dict[str, int] = {}
        self.state = RadioState(cfg.id)
        self._listeners: list[StateListener] = []
        self._scope_listeners: list[ScopeListener] = []
        self._scope_wanted = False
        self._task: asyncio.Task[None] | None = None
        self._bg: set[asyncio.Task[None]] = set()
        self.tx = TxController(
            cfg.id,
            _PttProxy(self),
            ptt,
            frequency=lambda: self.state.frequency_hz,
            power=lambda: self.state.levels.get(Level.RF_POWER.value),
            audit=audit,
            watchdog_interval_s=tx_watchdog_s,
        )
        self.tx.add_listener(self._on_tx_status)
        self.audio = AudioRouter(
            radio_audio,
            host_audio,
            holder=lambda: self.tx.status.holder_session if self.tx.status.transmitting else None,
        )
        self._audio_watch: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------
    def _serial_factory(self) -> ByteTransport:
        return SerialTransport(resolve_port(self.cfg.match), self.cfg.baud)

    def _on_tx_status(self, status: TxStatus) -> None:
        if not status.transmitting:
            self.audio.tx_ended()
        self._update(tx=status)

    async def _watch_audio(self) -> None:
        while True:
            self._update(
                audio={
                    "available": self.audio.running,
                    "error": self.audio.error,
                    "host_mode": self.audio.host_available,
                }
            )
            await asyncio.sleep(1.0)

    async def start(self) -> None:
        await self.tx.start()
        await self.audio.start()
        if self._audio_watch is None:
            self._audio_watch = asyncio.create_task(self._watch_audio())
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"station-{self.cfg.id}")

    async def close(self) -> None:
        await self.tx.close()  # unkeys first
        if self._audio_watch is not None:
            self._audio_watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._audio_watch
            self._audio_watch = None
        await self.audio.close()
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
        if self._scope_wanted and Feature.SCOPE in driver.features:
            await driver.set_scope_output(True)  # re-apply after a reconnect
        slow = self._slow_items(driver)
        for item in slow:  # read everything once up front
            await self._poll_slow(driver, item)
        meters = asyncio.create_task(self._meter_loop(driver), name=f"meters-{self.cfg.id}")
        try:
            timeouts = 0
            turn = 0
            while True:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(lost.wait(), self._poll_interval)
                if lost.is_set():
                    raise RadioLost("serial link error")
                if meters.done() and meters.exception() is not None:
                    raise RadioLost(f"meter polling failed: {meters.exception()}")
                try:
                    await self._poll_once(driver)
                    if slow:  # one slow item per tick, round robin
                        await self._poll_slow(driver, slow[turn % len(slow)])
                        turn += 1
                    timeouts = 0
                except RadioTimeout:
                    timeouts += 1
                    if timeouts >= self._lost_after:
                        raise RadioLost(f"no answer to {timeouts} polls in a row") from None
        finally:
            meters.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await meters

    @staticmethod
    def _slow_items(driver: RadioDriver) -> list[Level | str]:
        items: list[Level | str] = [
            lv for lv in Level if Feature(f"level_{lv.value}") in driver.features
        ]
        if Feature.DATA_MODE in driver.features:
            items.append("data_mode")
        if Feature.SCOPE in driver.features:
            items += [f"scope:{name}" for name in driver.scope_settings_supported()]
        return items

    def _bump(self, *fields: str) -> None:
        for f in fields:
            self._writes[f] = self._writes.get(f, 0) + 1

    def _unchanged_since(self, field: str, mark: int) -> bool:
        return self._writes.get(field, 0) == mark

    async def _poll_slow(self, driver: RadioDriver, item: Level | str) -> None:
        if isinstance(item, str) and item.startswith("scope:"):
            name = item.split(":", 1)[1]
            mark = self._writes.get(item, 0)
            value = await driver.get_scope_setting(name)
            if self._unchanged_since(item, mark) and self.state.scope.get(name) != value:
                self._update(scope={**self.state.scope, name: value})
            return
        if item == "data_mode":
            mark = self._writes.get("data_mode", 0)
            on = await driver.get_data_mode()
            if self._unchanged_since("data_mode", mark):
                self._update(data_mode=on)
            return
        assert isinstance(item, Level)
        key = f"level:{item.value}"
        mark = self._writes.get(key, 0)
        value = await driver.get_level(item)
        if not self._unchanged_since(key, mark):
            return
        if self.state.levels.get(item.value) != value:
            self._update(levels={**self.state.levels, item.value: value})
            if item is Level.RF_POWER:
                await self.tx.power_changed(value)

    async def _meter_loop(self, driver: RadioDriver) -> None:
        """S-meter on receive; Po, SWR, ALC while transmitting. Timeouts are not fatal here."""
        feats = driver.features
        rx = [Meter.S] if Feature.METER_S in feats else []
        tx = [m for m in (Meter.PO, Meter.SWR, Meter.ALC) if Feature(f"meter_{m.value}") in feats]
        while True:
            await asyncio.sleep(self._meter_interval)
            wanted = tx if self.tx.status.transmitting else rx
            readings: dict[str, dict[str, Any]] = {}
            for m in wanted:
                with contextlib.suppress(RadioTimeout):
                    readings[m.value] = _reading_json(await driver.get_meter(m))
            if readings:
                self._update(meters=readings)

    async def _poll_once(self, driver: RadioDriver) -> None:
        f_mark, m_mark = self._writes.get("frequency", 0), self._writes.get("mode", 0)
        hz = await driver.get_frequency()
        mode = await driver.get_mode()
        if self._unchanged_since("frequency", f_mark) and hz != self.state.frequency_hz:
            self._update(frequency_hz=hz)
            await self.tx.frequency_changed(hz)
        if self._unchanged_since("mode", m_mark) and (mode.mode.value, mode.filter) != (
            self.state.mode,
            self.state.filter,
        ):
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
            self._bump("frequency")
            self._update(frequency_hz=ev.value)
            self._spawn(self.tx.frequency_changed(ev.value))
        elif ev.kind == "scope" and isinstance(ev.value, ScopeLine):
            for listener in list(self._scope_listeners):
                try:
                    listener(ev.value)
                except Exception:
                    log.exception("scope listener failed")
        elif ev.kind == "mode" and isinstance(ev.value, ModeSetting):
            self._bump("mode")
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

    # -- scope ---------------------------------------------------------------
    def add_scope_listener(self, listener: ScopeListener) -> None:
        self._scope_listeners.append(listener)

    def remove_scope_listener(self, listener: ScopeListener) -> None:
        with contextlib.suppress(ValueError):
            self._scope_listeners.remove(listener)

    async def set_scope_demand(self, wanted: bool) -> None:
        """Hub calls this as viewers come and go: scope data only flows while someone watches.

        Scope data shares the CI-V link with control traffic, so it is off when no
        one is looking at it.
        """
        if wanted == self._scope_wanted:
            return
        self._scope_wanted = wanted
        driver = self._driver
        if driver is None or not self._connected or Feature.SCOPE not in driver.features:
            return
        try:
            await driver.set_scope_output(wanted)
        except RadioError as exc:
            log.warning("scope %s failed on %s: %s", "on" if wanted else "off", self.cfg.id, exc)

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
        self._bump("frequency")
        await driver.set_frequency(hz)
        self._bump("frequency")
        self._update(frequency_hz=hz)
        await self.tx.frequency_changed(hz)

    async def set_level(self, op: Operator, level: Level, value: int) -> None:
        driver = self._require_control(op)
        if not 0 <= value <= 255:
            raise ValueError("level must be 0..255")
        if level is Level.RF_POWER and not op.limits.allows_power(value):
            raise ControlDenied("RF power above your limit")
        self._bump(f"level:{level.value}")
        await driver.set_level(level, value)
        self._bump(f"level:{level.value}")
        self._update(levels={**self.state.levels, level.value: value})
        if level is Level.RF_POWER:
            await self.tx.power_changed(value)

    async def set_scope_setting(self, op: Operator, name: str, value: Any) -> None:
        driver = self._require_control(op)
        if name not in driver.scope_settings_supported():
            raise ControlDenied(f"{driver.model_name} has no scope setting {name!r}")
        key = f"scope:{name}"
        self._bump(key)
        await driver.set_scope_setting(name, value)
        self._bump(key)
        self._update(scope={**self.state.scope, name: value})

    async def set_data_mode(self, op: Operator, on: bool) -> None:
        driver = self._require_control(op)
        self._bump("data_mode")
        await driver.set_data_mode(on, self.state.filter if on else None)
        self._bump("data_mode")
        self._update(data_mode=on)

    async def set_mode(self, op: Operator, setting: ModeSetting) -> None:
        driver = self._require_control(op)
        self._bump("mode")
        await driver.set_mode(setting)
        self._bump("mode")
        # Filter omitted means "the mode's default" (p. 19-8); the next poll fills it in.
        self._update(mode=setting.mode.value, filter=setting.filter)


def _reading_json(r: MeterReading) -> dict[str, Any]:
    return {"raw": r.raw, "value": r.value, "unit": r.unit, "db_over_s9": r.db_over_s9}
