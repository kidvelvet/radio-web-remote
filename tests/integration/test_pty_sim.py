"""Real SerialTransport + pyserial against the simulator on a pseudo-terminal."""

import asyncio
import sys

import pytest

from radio_web_remote.radios.base import Mode, ModeSetting, StateEvent
from radio_web_remote.radios.icom import IC7300, IcomCivDriver
from radio_web_remote.sim.icom import IcomSimulator, PtyRunner
from radio_web_remote.transport.serial_port import SerialTransport

pytestmark = [
    pytest.mark.pty,
    pytest.mark.skipif(sys.platform == "win32", reason="no pseudo-terminals on Windows"),
]


async def test_roundtrip_over_pty():
    sim = IcomSimulator(IC7300, echo_back=True)
    with PtyRunner(sim) as runner:
        driver = IcomCivDriver(IC7300, SerialTransport(runner.path, 115200), response_timeout_s=1.0)
        await driver.connect()
        try:
            assert await driver.get_frequency() == 14_074_000
            await driver.set_frequency(10_136_000)
            assert await driver.get_frequency() == 10_136_000
            await driver.set_mode(ModeSetting(Mode.RTTY, 3))
            assert await driver.get_mode() == ModeSetting(Mode.RTTY, 3)

            events: list[StateEvent] = []
            driver.add_listener(events.append)
            runner.inject(sim.front_panel_tune(18_100_000))
            for _ in range(50):
                if events:
                    break
                await asyncio.sleep(0.02)
            assert StateEvent("frequency", 18_100_000) in events

            assert await driver.key() is True
            assert await driver.get_ptt() is True
            await driver.unkey()
            assert sim.state.transmitting is False
        finally:
            await driver.close()
        assert sim.received[0].payload == b"\x1c\x00\x00"
        assert sim.received[-1].payload == b"\x1c\x00\x00"
