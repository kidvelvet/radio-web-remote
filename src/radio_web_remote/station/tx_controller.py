"""Server-side transmit control. Every path to transmit goes through here.

ADR 0001, decision D6. Transmit stops when any of these happen, enforced on
the server and never left to the client:

- the holder releases PTT                         StopReason.RELEASED
- the holder's control connection closes          StopReason.CONNECTION_CLOSED
- no heartbeat from the holder for the timeout    StopReason.HEARTBEAT_LOST
- the holder's page is hidden or backgrounded     StopReason.HIDDEN
- the TX timeout expires (max 180 s)              StopReason.TIMEOUT
- the frequency leaves the holder's band limits   StopReason.OUT_OF_LIMITS
- the holder logs out or is disabled              StopReason.LOGGED_OUT
- the server shuts down                           StopReason.SHUTDOWN

One holder at a time. After a TX timeout, that session must release PTT
before it may key again, so a held button cannot re-key in a loop.

Unkey never waits for a key in progress: it bumps a generation counter and
calls the driver, whose own guard drops an overtaken key (civ_driver.py).
If the radio does not confirm RX, the watchdog keeps sending TX off.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from radio_web_remote.config import PttConfig
from radio_web_remote.radios.base import RadioError

log = logging.getLogger(__name__)

WATCHDOG_INTERVAL_S = 0.1
UNKEY_RETRY_S = 1.0


class Role(Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    LISTENER = "listener"


@dataclass(frozen=True, slots=True)
class TxLimits:
    """Per-user limits set by an admin. None means no limit of that kind."""

    bands_hz: tuple[tuple[int, int], ...] | None = None  # inclusive (low, high) ranges
    # Highest RF power *control position* allowed, in percent of its range
    # (IC-7300: 14 0A, 0000=max CCW to 0255=max CW, p. 19-3). Not watts.
    max_power_pct: float | None = None

    def allows_power(self, raw: int | None) -> bool:
        if self.max_power_pct is None:
            return True
        if raw is None:
            return False
        return raw * 100 / 255 <= self.max_power_pct + 1e-9

    def allows_frequency(self, hz: int | None) -> bool:
        if self.bands_hz is None:
            return True
        if hz is None:
            return False  # cannot prove it is in band
        return any(lo <= hz <= hi for lo, hi in self.bands_hz)


@dataclass(frozen=True, slots=True)
class Operator:
    session_id: str
    username: str
    callsign: str
    role: Role
    limits: TxLimits = field(default_factory=TxLimits)

    @property
    def may_transmit(self) -> bool:
        return self.role in (Role.OPERATOR, Role.ADMIN) and bool(self.callsign.strip())


class StopReason(Enum):
    RELEASED = "released"
    CONNECTION_CLOSED = "connection_closed"
    HEARTBEAT_LOST = "heartbeat_lost"
    HIDDEN = "hidden"
    TIMEOUT = "timeout"
    OUT_OF_LIMITS = "out_of_limits"
    LOGGED_OUT = "logged_out"
    SHUTDOWN = "shutdown"
    RADIO_ERROR = "radio_error"


class DenyReason(Enum):
    NOT_PERMITTED = "not_permitted"  # listener, or no callsign
    HELD_BY_OTHER = "held_by_other"
    OUT_OF_LIMITS = "out_of_limits"
    RELEASE_FIRST = "release_first"  # timed out; release before keying again
    RADIO_ERROR = "radio_error"


class TxDenied(Exception):
    def __init__(self, reason: DenyReason, detail: str = "") -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TxStatus:
    transmitting: bool  # someone holds the TX lock (keying may still be in progress)
    holder_callsign: str | None = None
    holder_session: str | None = None
    since: float | None = None  # wall clock (time.time) when TX started
    last_stop_reason: StopReason | None = None
    keyed: bool = False  # the radio acknowledged TX for the current holder
    # TX off was sent but the radio has not confirmed RX: it may still be on
    # the air (seen on hardware when the USB cable was pulled, H7300-7).
    unkey_unconfirmed: bool = False


class PttDriver(Protocol):
    """The two driver calls the controller needs (a RadioDriver, or a station proxy)."""

    async def key(self) -> bool: ...

    async def unkey(self, verify: bool = True) -> None: ...


AuditSink = Callable[[dict[str, Any]], None]
StatusListener = Callable[[TxStatus], None]


class TxController:
    def __init__(
        self,
        radio_id: str,
        driver: PttDriver,
        ptt: PttConfig,
        frequency: Callable[[], int | None],
        audit: AuditSink | None = None,
        power: Callable[[], int | None] = lambda: None,
        # Reads the frequency the radio will transmit on, where the radio can
        # report it (split, IC-9700 sub band or satellite uplink differ from the
        # displayed frequency). None: use `frequency`.
        tx_frequency: Callable[[], Awaitable[int | None]] | None = None,
        watchdog_interval_s: float = WATCHDOG_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.radio_id = radio_id
        self.driver = driver
        self.ptt = ptt
        self._frequency = frequency
        self._tx_frequency = tx_frequency
        self._power = power
        self._audit = audit
        self._interval = watchdog_interval_s
        self._clock = clock
        self._key_lock = asyncio.Lock()
        self._holder: Operator | None = None
        self._keyed = False  # radio confirmed (acked) TX for the current holder
        self._generation = 0
        self._started_mono = 0.0
        self._started_wall = 0.0
        self._started_hz: int | None = None
        self._last_heartbeat = 0.0
        self._unkey_pending = False
        self._unkey_failures = 0
        self._unconfirmed_since = 0.0
        self._unkey_tasks: set[asyncio.Task[None]] = set()
        self._last_unkey_try = 0.0
        self._must_release: set[str] = set()
        self._last_stop: StopReason | None = None
        self._listeners: list[StatusListener] = []
        self._watchdog: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watch(), name=f"tx-watchdog-{self.radio_id}")

    async def close(self) -> None:
        await self._stop(StopReason.SHUTDOWN, force=True)
        if self._watchdog is not None:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None

    # -- status ----------------------------------------------------------
    @property
    def status(self) -> TxStatus:
        h = self._holder
        if h is None:
            return TxStatus(
                False, last_stop_reason=self._last_stop, unkey_unconfirmed=self._unkey_failures > 0
            )
        return TxStatus(
            True,
            h.callsign,
            h.session_id,
            self._started_wall,
            self._last_stop,
            self._keyed,
            self._unkey_failures > 0,
        )

    @property
    def unkey_unconfirmed(self) -> bool:
        return self._unkey_pending

    def add_listener(self, listener: StatusListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: StatusListener) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def _notify(self) -> None:
        status = self.status
        for listener in list(self._listeners):
            try:
                listener(status)
            except Exception:
                log.exception("TX status listener failed")

    # -- client events -----------------------------------------------------
    async def ptt_down(self, op: Operator) -> None:
        """Key for this operator, or raise TxDenied."""
        if not op.may_transmit:
            raise TxDenied(DenyReason.NOT_PERMITTED, "role or callsign does not permit transmit")
        if op.session_id in self._must_release:
            raise TxDenied(
                DenyReason.RELEASE_FIRST, "TX timed out; release PTT before keying again"
            )
        async with self._key_lock:
            holder = self._holder
            if holder is not None:
                if holder.session_id == op.session_id:
                    self._last_heartbeat = self._clock()
                    return  # already holding; a repeated press is a heartbeat
                raise TxDenied(DenyReason.HELD_BY_OTHER, holder.callsign)
            try:
                hz = await self._read_tx_hz()
            except RadioError as exc:
                raise TxDenied(
                    DenyReason.RADIO_ERROR, f"could not read the transmit frequency: {exc}"
                ) from exc
            if not op.limits.allows_frequency(hz):
                raise TxDenied(DenyReason.OUT_OF_LIMITS, f"{hz} Hz is outside this user's bands")
            if not op.limits.allows_power(self._power()):
                raise TxDenied(DenyReason.OUT_OF_LIMITS, "RF power is above this user's limit")

            self._generation += 1
            generation = self._generation
            self._holder = op
            self._keyed = False
            now = self._clock()
            self._started_mono = self._last_heartbeat = now
            self._started_wall = time.time()
            self._started_hz = hz
            self._notify()
            try:
                keyed = await self.driver.key()
            except RadioError as exc:
                log.error("key failed on %s: %s", self.radio_id, exc)
                if self._generation == generation:
                    await self._stop(StopReason.RADIO_ERROR, force=True)
                raise TxDenied(DenyReason.RADIO_ERROR, str(exc)) from exc
            if not keyed or self._generation != generation:
                return  # an unkey overtook this request; it already cleaned up
            self._keyed = True
            self._notify()
            self._emit_audit("tx_start", op, frequency_hz=hz)

    async def ptt_up(self, op: Operator) -> None:
        self._must_release.discard(op.session_id)
        if self._holder is not None and self._holder.session_id == op.session_id:
            await self._stop(StopReason.RELEASED)

    def heartbeat(self, op: Operator) -> None:
        if self._holder is not None and self._holder.session_id == op.session_id:
            self._last_heartbeat = self._clock()

    async def client_gone(self, session_id: str, reason: StopReason) -> None:
        """Connection closed, page hidden, or session logged out."""
        if reason is not StopReason.HIDDEN:
            self._must_release.discard(session_id)
        if self._holder is not None and self._holder.session_id == session_id:
            await self._stop(reason)

    async def radio_lost(self) -> None:
        """The radio link dropped. Clear any holder; reconnect sends TX off first.

        Only a link lost while someone held TX (or with an unkey already
        unconfirmed) can mean the radio is still transmitting. A radio that is
        simply off or unplugged while idle must not raise that alarm.
        """
        if self._holder is None and not self._unkey_pending:
            return
        await self._stop(StopReason.RADIO_ERROR, force=True)

    async def power_changed(self, raw: int) -> None:
        holder = self._holder
        if holder is not None and not holder.limits.allows_power(raw):
            await self._stop(StopReason.OUT_OF_LIMITS)

    async def frequency_changed(self, hz: int) -> None:
        """The displayed frequency changed. With a TX frequency reader, re-read that instead."""
        if self._tx_frequency is not None:
            await self.recheck_frequency()
            return
        holder = self._holder
        if holder is not None and not holder.limits.allows_frequency(hz):
            await self._stop(StopReason.OUT_OF_LIMITS)

    async def recheck_frequency(self) -> None:
        """While keyed: read the transmit frequency again and stop if it left the limits."""
        holder = self._holder
        if holder is None:
            return
        try:
            hz = await self._read_tx_hz()
        except RadioError as exc:
            log.error("TX frequency read failed on %s while keyed: %s", self.radio_id, exc)
            if self._holder is holder:
                await self._stop(StopReason.RADIO_ERROR)
            return
        if self._holder is holder and not holder.limits.allows_frequency(hz):
            await self._stop(StopReason.OUT_OF_LIMITS)

    async def _read_tx_hz(self) -> int | None:
        if self._tx_frequency is not None:
            return await self._tx_frequency()
        return self._frequency()

    # -- internals ---------------------------------------------------------
    async def _stop(self, reason: StopReason, force: bool = False) -> None:
        holder = self._holder
        if holder is None and not force:
            return
        was_keyed = self._keyed
        self._holder = None
        self._keyed = False
        self._generation += 1
        self._last_stop = reason
        if holder is not None:
            if reason is StopReason.TIMEOUT:
                self._must_release.add(holder.session_id)
            if was_keyed:
                self._emit_audit(
                    "tx_stop",
                    holder,
                    frequency_hz=self._started_hz,
                    duration_s=round(self._clock() - self._started_mono, 3),
                    reason=reason.value,
                )
        # Pending until the radio confirms RX. Set before touching the radio so
        # that if anything interrupts the unkey, the watchdog retries it.
        self._unkey_pending = True
        self._notify()
        # Run the unkey as its own task and shield it: cancelling the caller
        # (a closing WebSocket, server shutdown) must not cancel TX off.
        task = asyncio.ensure_future(self._unkey())
        self._unkey_tasks.add(task)
        task.add_done_callback(self._unkey_tasks.discard)
        await asyncio.shield(task)

    async def _unkey(self) -> None:
        self._last_unkey_try = self._clock()
        try:
            await self.driver.unkey()
        except Exception as exc:  # RadioError, or anything unexpected: keep retrying
            if self._unkey_failures == 0:
                log.critical("TX off NOT confirmed on %s: %s. Retrying.", self.radio_id, exc)
                self._unconfirmed_since = self._clock()
                self._emit_radio_audit("tx_off_unconfirmed", error=str(exc))
            self._unkey_failures += 1
            self._unkey_pending = True
            if self._unkey_failures == 1:
                self._notify()
        else:
            if self._unkey_failures:
                after = round(self._clock() - self._unconfirmed_since, 3)
                log.warning("TX off confirmed on %s after %.1f s", self.radio_id, after)
                self._emit_radio_audit("tx_off_confirmed", after_s=after)
                self._unkey_failures = 0
                self._unkey_pending = False
                self._notify()
            self._unkey_failures = 0
            self._unkey_pending = False

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._check()
            except Exception:
                log.exception("TX watchdog check failed on %s", self.radio_id)

    async def _check(self) -> None:
        now = self._clock()
        if self._holder is not None:
            if now - self._last_heartbeat > self.ptt.heartbeat_timeout_s:
                await self._stop(StopReason.HEARTBEAT_LOST)
            elif now - self._started_mono > self.ptt.tx_timeout_s:
                await self._stop(StopReason.TIMEOUT)
        elif self._unkey_pending and now - self._last_unkey_try >= UNKEY_RETRY_S:
            await self._unkey()

    def _emit_radio_audit(self, event: str, **fields: Any) -> None:
        if self._audit is not None:
            try:
                self._audit({"event": event, "radio": self.radio_id, **fields})
            except Exception:
                log.exception("audit sink failed")

    def _emit_audit(self, event: str, op: Operator, **fields: Any) -> None:
        if self._audit is None:
            return
        record = {
            "event": event,
            "radio": self.radio_id,
            "user": op.username,
            "callsign": op.callsign,
            "session": op.session_id[:8],
            **fields,
        }
        try:
            self._audit(record)
        except Exception:
            log.exception("audit sink failed")
