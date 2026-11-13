import asyncio

import pytest

from radio_web_remote.transport.base import ByteTransport, Priority, TransportClosed
from radio_web_remote.transport.serial_port import SerialTransport


class SlowTransport(ByteTransport):
    """Records writes; each write takes a moment so the queue can build up."""

    def __init__(self) -> None:
        super().__init__()
        self.wire: list[bytes] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def _open_impl(self) -> None:
        pass

    async def _close_impl(self) -> None:
        pass

    async def _write_impl(self, data: bytes) -> None:
        await self.gate.wait()
        await asyncio.sleep(0.01)
        self.wire.append(data)


async def test_priority_order():
    t = SlowTransport()
    await t.open()
    t.gate.clear()  # hold the writer so items queue up
    tasks = [
        asyncio.create_task(t.send(b"bulk", Priority.BULK)),
        asyncio.create_task(t.send(b"poll", Priority.POLL)),
        asyncio.create_task(t.send(b"ctl", Priority.CONTROL)),
    ]
    await asyncio.sleep(0.02)
    t.gate.set()
    await asyncio.gather(*tasks)
    # The first item may already be in flight when the gate closes; the rest obey priority.
    assert t.wire[-2:] == [b"poll", b"bulk"] or t.wire == [b"ctl", b"poll", b"bulk"]
    await t.close()


async def test_urgent_jumps_queue():
    t = SlowTransport()
    await t.open()
    t.gate.clear()
    queued = [asyncio.create_task(t.send(bytes([i]), Priority.POLL)) for i in range(5)]
    await asyncio.sleep(0.02)
    urgent = asyncio.create_task(t.send_urgent(b"UNKEY"))
    await asyncio.sleep(0)
    t.gate.set()
    await urgent
    # At most the one frame already being written may precede the urgent frame.
    assert t.wire.index(b"UNKEY") <= 1
    await asyncio.gather(*queued)
    await t.close()


async def test_urgent_writes_keep_call_order_and_guard():
    t = SlowTransport()
    await t.open()
    generation = {"n": 0}
    g0 = generation["n"]
    key = asyncio.create_task(t.send_urgent(b"KEY", guard=lambda: generation["n"] == g0))
    generation["n"] += 1  # unkey requested before key got the lock
    unkey = asyncio.create_task(t.send_urgent(b"UNKEY"))
    sent_key, _ = await asyncio.gather(key, unkey)
    assert sent_key is False
    assert t.wire == [b"UNKEY"]
    await t.close()


async def test_purge_drops_tagged_items():
    t = SlowTransport()
    await t.open()
    t.gate.clear()
    a = asyncio.create_task(t.send(b"a", tag="scope"))
    b = asyncio.create_task(t.send(b"b", tag="scope"))
    c = asyncio.create_task(t.send(b"c"))
    await asyncio.sleep(0.01)
    dropped = t.purge("scope")
    t.gate.set()
    await c
    assert dropped >= 1
    for task in (a, b):
        if not task.done():
            await asyncio.sleep(0.05)
    assert b"c" in t.wire
    await t.close()


async def test_send_when_closed_raises():
    t = SlowTransport()
    with pytest.raises(TransportClosed):
        await t.send(b"x")
    with pytest.raises(TransportClosed):
        await t.send_urgent(b"x")


async def test_close_fails_pending():
    t = SlowTransport()
    await t.open()
    t.gate.clear()
    first = asyncio.create_task(t.send(b"1"))
    second = asyncio.create_task(t.send(b"2"))
    await asyncio.sleep(0.01)
    await t.close()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert any(isinstance(r, (TransportClosed, asyncio.CancelledError)) for r in results)


class FakeSerial:
    """Records the order of attribute sets relative to open()."""

    def __init__(self) -> None:
        object.__setattr__(self, "log", [])
        object.__setattr__(self, "is_open", False)
        object.__setattr__(self, "in_waiting", 0)

    def __setattr__(self, name, value):
        self.log.append(("set", name, value, self.is_open))
        object.__setattr__(self, name, value)

    def open(self):
        self.log.append(("open",))
        object.__setattr__(self, "is_open", True)

    def close(self):
        object.__setattr__(self, "is_open", False)

    def read(self, n):
        import time

        time.sleep(0.01)
        return b""

    def write(self, data):
        return len(data)


async def test_serial_drops_dtr_rts_before_open(monkeypatch):
    fake = FakeSerial()
    monkeypatch.setattr(SerialTransport, "_make_serial", staticmethod(lambda: fake))
    t = SerialTransport("/dev/null-test", 115200)
    await t.open()
    await t.close()
    sets_before_open = {
        entry[1]: entry[2] for entry in fake.log if entry[0] == "set" and not entry[3]
    }
    assert sets_before_open["dtr"] is False
    assert sets_before_open["rts"] is False
    # No line was ever driven high.
    assert not any(e[0] == "set" and e[1] in ("dtr", "rts") and e[2] for e in fake.log)
