"""IcomCivDriver against the in-process simulator (runs on every OS)."""

import asyncio

import pytest

from radio_web_remote.protocols.civ.commands import CivCommand
from radio_web_remote.protocols.civ.frame import Frame
from radio_web_remote.radios import registry
from radio_web_remote.radios.base import (
    Mode,
    ModeSetting,
    RadioRejected,
    RadioTimeout,
    StateEvent,
)
from radio_web_remote.radios.icom import IC7300, IcomCivDriver
from radio_web_remote.sim.icom import IcomSimulator, attach_loopback, push_unsolicited
from radio_web_remote.transport.loopback import loopback_pair


@pytest.fixture
async def rig():
    host, radio_end = loopback_pair()
    sim = IcomSimulator(IC7300)
    attach_loopback(sim, radio_end)
    await radio_end.open()
    driver = registry.create("icom.ic7300", host, registry.DriverOptions(response_timeout_s=0.2))
    await driver.connect()
    yield driver, sim, radio_end
    await driver.close()
    await radio_end.close()


async def test_first_frame_after_connect_is_tx_off(rig):
    _, sim, _ = rig
    assert sim.received[0] == Frame(0x94, 0xE0, b"\x1c\x00\x00")


async def test_frequency_roundtrip(rig):
    driver, sim, _ = rig
    assert await driver.get_frequency() == 14_074_000
    await driver.set_frequency(7_074_000)
    assert sim.state.frequency_hz == 7_074_000
    assert await driver.get_frequency() == 7_074_000


async def test_out_of_range_frequency_rejected_locally(rig):
    driver, _, _ = rig
    with pytest.raises(ValueError):
        await driver.set_frequency(144_200_000)  # IC-7300 encoding stops below 100 MHz


async def test_mode_roundtrip(rig):
    driver, sim, _ = rig
    assert await driver.get_mode() == ModeSetting(Mode.USB, 1)
    await driver.set_mode(ModeSetting(Mode.CW, 2))
    assert (sim.state.mode_code, sim.state.filter_code) == (0x03, 0x02)
    assert await driver.get_mode() == ModeSetting(Mode.CW, 2)
    await driver.set_mode(ModeSetting(Mode.LSB))
    assert sim.received[-1].payload == b"\x06\x00"  # filter byte omitted


async def test_ng_raises(rig):
    driver, _, _ = rig
    # Mode code 06 is not in the IC-7300 table (p. 19-8), so the radio answers NG.
    with pytest.raises(RadioRejected):
        await driver._transact(CivCommand.SET_MODE, b"\x06\x06", False)


async def test_key_and_unkey(rig):
    driver, sim, _ = rig
    assert await driver.key() is True
    await asyncio.sleep(0)
    assert await driver.get_ptt() is True
    await driver.unkey()
    assert sim.state.transmitting is False
    assert await driver.get_ptt() is False


async def test_unkey_overtakes_pending_key(rig):
    driver, sim, _ = rig
    key = asyncio.create_task(driver.key())
    unkey = asyncio.create_task(driver.unkey())
    await asyncio.gather(key, unkey)
    assert sim.state.transmitting is False
    assert sim.tx_history[-1] is False


async def test_unkey_jumps_ahead_of_polling(rig):
    driver, sim, _ = rig
    polls = [asyncio.create_task(driver.get_frequency()) for _ in range(10)]
    await asyncio.sleep(0)
    await driver.key()
    await driver.unkey(verify=False)
    await asyncio.gather(*polls)
    tx_frames = [f for f in sim.received if f.payload[:2] == b"\x1c\x00" and len(f.payload) == 3]
    assert tx_frames[-1].payload == b"\x1c\x00\x00"
    assert sim.state.transmitting is False


async def test_echo_back_frames_are_ignored():
    host, radio_end = loopback_pair()
    sim = IcomSimulator(IC7300, echo_back=True)
    attach_loopback(sim, radio_end)
    await radio_end.open()
    driver = IcomCivDriver(IC7300, host, response_timeout_s=0.2)
    await driver.connect()
    assert await driver.get_frequency() == 14_074_000
    await driver.set_frequency(3_573_000)
    assert driver.echoes_seen >= 3
    await driver.close()


async def test_junk_and_collisions_on_the_line(rig):
    driver, _, radio_end = rig
    push_unsolicited(radio_end, b"\x00\x13\xfe\xfe\xe0\x94\xfc\xfd\x55")
    assert await driver.get_frequency() == 14_074_000
    assert driver.parser.stats.collisions == 1


async def test_transceive_events(rig):
    driver, sim, radio_end = rig
    events: list[StateEvent] = []
    driver.add_listener(events.append)
    push_unsolicited(radio_end, sim.front_panel_tune(21_074_000))
    push_unsolicited(radio_end, sim.front_panel_mode(Mode.FM))
    await asyncio.sleep(0)
    assert StateEvent("frequency", 21_074_000) in events
    assert StateEvent("mode", ModeSetting(Mode.FM, 1)) in events


async def test_frames_for_other_addresses_ignored(rig):
    driver, _, radio_end = rig
    events: list[StateEvent] = []
    driver.add_listener(events.append)
    # From a different radio (A4) on the same bus.
    push_unsolicited(radio_end, bytes.fromhex("FEFE00A4000040071400FD"))
    await asyncio.sleep(0)
    assert events == []


async def test_silent_radio_times_out():
    host, radio_end = loopback_pair()
    sim = IcomSimulator(IC7300, silent=True)
    attach_loopback(sim, radio_end)
    await radio_end.open()
    driver = IcomCivDriver(IC7300, host, response_timeout_s=0.05)
    await driver.connect()  # TX off is fire-and-forget on connect
    with pytest.raises(RadioTimeout):
        await driver.get_frequency()
    with pytest.raises(RadioTimeout):
        await driver.unkey()  # verify loop gives up loudly
    payloads = [f.payload for f in sim.received]
    assert payloads[0] == b"\x1c\x00\x00"  # TX off first, even with no answers
    assert payloads[1] == b"\x03"
    # The unkey loop only ever sends TX off or reads TX state; never TX on.
    assert all(p in (b"\x1c\x00\x00", b"\x1c\x00") for p in payloads[2:])
    assert payloads.count(b"\x1c\x00\x00") >= 1 + 1 + 3  # connect, unkey, retries
    await host.close()
