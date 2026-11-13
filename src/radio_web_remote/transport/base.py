"""Protocol-agnostic byte transport with prioritized writes.

Normal traffic goes through a priority queue drained by one writer task.
PTT frames never queue: send_urgent() writes as soon as the frame currently
on the wire is finished, ahead of everything queued. Urgent writes keep
their call order (asyncio.Lock is FIFO), so "key" followed by "unkey" always
ends unkeyed on the wire. An optional guard is evaluated while holding the
write lock, so a key request that was overtaken by an unkey can cancel
itself at the last moment.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

DataCallback = Callable[[bytes], None]
ErrorCallback = Callable[[BaseException], None]


class Priority(IntEnum):
    CONTROL = 10  # user actions
    POLL = 20  # background state polling
    BULK = 30  # scope settings and other bulk traffic


class TransportClosed(ConnectionError):
    pass


@dataclass(order=True)
class _Item:
    priority: int
    seq: int
    data: bytes = field(compare=False)
    tag: str | None = field(compare=False, default=None)
    done: asyncio.Future[None] | None = field(compare=False, default=None)


class ByteTransport(ABC):
    def __init__(self) -> None:
        self.on_data: DataCallback | None = None
        self.on_error: ErrorCallback | None = None
        self._queue: asyncio.PriorityQueue[_Item] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._write_lock = asyncio.Lock()
        self._writer: asyncio.Task[None] | None = None
        self._open = False

    # -- subclass hooks -------------------------------------------------
    @abstractmethod
    async def _open_impl(self) -> None: ...

    @abstractmethod
    async def _close_impl(self) -> None: ...

    @abstractmethod
    async def _write_impl(self, data: bytes) -> None: ...

    # -- public API -----------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._open

    async def open(self) -> None:
        if self._open:
            return
        await self._open_impl()
        self._open = True
        self._writer = asyncio.create_task(
            self._writer_loop(), name=f"{type(self).__name__}-writer"
        )

    async def close(self) -> None:
        if not self._open:
            return
        self._open = False
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
            self._writer = None
        self._fail_pending(TransportClosed("transport closed"))
        await self._close_impl()

    async def send(
        self, data: bytes, priority: Priority = Priority.CONTROL, tag: str | None = None
    ) -> None:
        """Queue data and wait until it has been written."""
        if not self._open:
            raise TransportClosed("transport is not open")
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._queue.put(_Item(int(priority), next(self._seq), bytes(data), tag, fut))
        await fut

    async def send_urgent(self, data: bytes, guard: Callable[[], bool] | None = None) -> bool:
        """Write ahead of the queue. Returns False if guard() said not to send."""
        if not self._open:
            raise TransportClosed("transport is not open")
        async with self._write_lock:
            if guard is not None and not guard():
                return False
            await self._write_impl(bytes(data))
            return True

    def purge(self, tag: str) -> int:
        """Drop queued (not yet written) items carrying this tag."""
        kept: list[_Item] = []
        dropped = 0
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item.tag == tag:
                dropped += 1
                if item.done is not None and not item.done.done():
                    item.done.cancel()
            else:
                kept.append(item)
        for item in kept:
            self._queue.put_nowait(item)
        return dropped

    # -- internals ------------------------------------------------------
    async def _writer_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item.done is not None and item.done.cancelled():
                continue
            try:
                async with self._write_lock:
                    await self._write_impl(item.data)
            except asyncio.CancelledError:
                # close() cancelled us mid-write: the sender must not wait forever.
                if item.done is not None and not item.done.done():
                    item.done.set_exception(TransportClosed("transport closed during write"))
                raise
            except Exception as exc:
                if item.done is not None and not item.done.done():
                    item.done.set_exception(exc)
                self._report_error(exc)
            else:
                if item.done is not None and not item.done.done():
                    item.done.set_result(None)

    def _fail_pending(self, exc: BaseException) -> None:
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item.done is not None and not item.done.done():
                item.done.set_exception(exc)

    def _deliver(self, data: bytes) -> None:
        if self.on_data is not None and data:
            self.on_data(data)

    def _report_error(self, exc: BaseException) -> None:
        if self.on_error is not None:
            self.on_error(exc)
