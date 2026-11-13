"""Small asyncio helpers."""

from __future__ import annotations

import asyncio
import heapq
import itertools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class PriorityLock:
    """A mutex whose waiters are served lowest priority number first, FIFO within a priority.

    Used as the radio "bus": one command in flight at a time, PTT first in line.
    """

    def __init__(self) -> None:
        self._locked = False
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()

    @property
    def locked(self) -> bool:
        return self._locked

    @asynccontextmanager
    async def acquire(self, priority: int, wait_s: float | None = None) -> AsyncIterator[None]:
        """Hold the lock. Raises TimeoutError if not granted within wait_s."""
        await self._acquire(priority, wait_s)
        try:
            yield
        finally:
            self._release()

    async def _acquire(self, priority: int, wait_s: float | None) -> None:
        if not self._locked and not self._waiters:
            self._locked = True
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (priority, next(self._seq), fut)
        heapq.heappush(self._waiters, entry)
        try:
            await asyncio.wait_for(asyncio.shield(fut), wait_s)
        except BaseException:
            if fut.done() and not fut.cancelled():
                # Granted just as we gave up: pass it on.
                self._release()
            else:
                fut.cancel()
                self._waiters.remove(entry)
                heapq.heapify(self._waiters)
            raise

    def _release(self) -> None:
        while self._waiters:
            _, _, fut = heapq.heappop(self._waiters)
            if not fut.done():
                fut.set_result(None)  # ownership passes directly; stays locked
                return
        self._locked = False
