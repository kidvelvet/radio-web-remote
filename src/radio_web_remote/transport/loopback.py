"""In-process transport pair, for running the simulator without a serial port.

Bytes written to one end arrive at the other end's on_data on the next event
loop turn, so code under test sees the same asynchrony as with a real port.
"""

from __future__ import annotations

import asyncio

from radio_web_remote.transport.base import ByteTransport


class LoopbackEnd(ByteTransport):
    def __init__(self) -> None:
        super().__init__()
        self.peer: LoopbackEnd | None = None
        self.written: list[bytes] = []  # everything this end wrote, for tests

    async def _open_impl(self) -> None:
        pass

    async def _close_impl(self) -> None:
        pass

    async def _write_impl(self, data: bytes) -> None:
        self.written.append(data)
        peer = self.peer
        if peer is not None and peer.is_open:
            asyncio.get_running_loop().call_soon(peer._deliver, data)
        await asyncio.sleep(0)


def loopback_pair() -> tuple[LoopbackEnd, LoopbackEnd]:
    a, b = LoopbackEnd(), LoopbackEnd()
    a.peer, b.peer = b, a
    return a, b
