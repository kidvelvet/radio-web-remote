import asyncio

import pytest

from radio_web_remote.asyncutil import PriorityLock


async def test_waiters_served_by_priority_then_fifo():
    lock = PriorityLock()
    order: list[str] = []

    async def worker(name: str, prio: int) -> None:
        async with lock.acquire(prio):
            order.append(name)
            await asyncio.sleep(0)

    async with lock.acquire(10):
        tasks = [
            asyncio.create_task(worker("poll-1", 20)),
            asyncio.create_task(worker("ctl", 10)),
            asyncio.create_task(worker("ptt-1", 0)),
            asyncio.create_task(worker("poll-2", 20)),
            asyncio.create_task(worker("ptt-2", 0)),
        ]
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert order == ["ptt-1", "ptt-2", "ctl", "poll-1", "poll-2"]
    assert not lock.locked


async def test_wait_timeout_leaves_lock_usable():
    lock = PriorityLock()
    async with lock.acquire(10):
        with pytest.raises(TimeoutError):
            async with lock.acquire(0, wait_s=0.01):
                pass
    async with lock.acquire(0, wait_s=0.01):
        assert lock.locked
    assert not lock.locked


async def test_cancelled_waiter_does_not_block_others():
    lock = PriorityLock()
    got: list[int] = []

    async def take(n: int) -> None:
        async with lock.acquire(n):
            got.append(n)

    async with lock.acquire(5):
        doomed = asyncio.create_task(take(0))
        other = asyncio.create_task(take(1))
        await asyncio.sleep(0)
        doomed.cancel()
    await asyncio.gather(doomed, other, return_exceptions=True)
    assert got == [1]
    assert not lock.locked
