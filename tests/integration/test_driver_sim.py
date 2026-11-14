"""IcomCivDriver against the in-process simulator (runs on every OS)."""

import asyncio
import contextlib

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
    assert payloads.count(b"\x03") == 2  # one retry on timeout
    # Nothing but TX off, TX-state reads and the frequency read; never TX on.
    assert set(payloads) <= {b"\x1c\x00\x00", b"\x1c\x00", b"\x03"}
    assert payloads.count(b"\x1c\x00\x00") >= 3  # connect, unkey, at least one resend
    await host.close()


def test_sim_drops_back_to_back_commands_like_the_ic7300():
    # Regression for the first hardware probe (2026-11-13): TX off then a
    # frequency read, written back to back, got only one answer.
    sim = IcomSimulator(IC7300)
    tx_off = bytes.fromhex("FEFE94E01C0000FD")
    read = bytes.fromhex("FEFE94E003FD")
    assert sim.handle_bytes(tx_off, now=10.0) == bytes.fromhex("FEFEE094FBFD")
    assert sim.handle_bytes(read, now=10.001) == b""  # before the reply went out
    assert [f.payload for f in sim.dropped] == [b"\x03"]
    assert sim.handle_bytes(read, now=10.010) == bytes.fromhex("FEFEE09403" + "0040071400FD")


async def test_driver_never_overlaps_commands(rig):
    driver, sim, _ = rig
    # A burst of concurrent callers, PTT in the middle of it.
    jobs = [asyncio.create_task(driver.get_frequency()) for _ in range(5)]
    jobs.append(asyncio.create_task(driver.key()))
    jobs += [asyncio.create_task(driver.set_frequency(7_000_000 + i)) for i in range(5)]
    jobs.append(asyncio.create_task(driver.unkey()))
    await asyncio.gather(*jobs)
    assert sim.dropped == []
    assert sim.state.transmitting is False


def test_sim_reports_tx_until_release_delay():
    sim = IcomSimulator(IC7300, tx_release_s=0.05, drop_while_busy=False)
    read = bytes.fromhex("FEFE94E01C00FD")
    sim.handle_bytes(bytes.fromhex("FEFE94E01C0001FD"), now=1.0)
    sim.handle_bytes(bytes.fromhex("FEFE94E01C0000FD"), now=2.0)
    assert sim.handle_bytes(read, now=2.01).endswith(bytes.fromhex("1C0001FD"))  # still TX
    assert sim.handle_bytes(read, now=2.06).endswith(bytes.fromhex("1C0000FD"))  # now RX


async def test_unkey_waits_for_radio_to_release_tx():
    # Regression for H7300-4 (2026-11-13): a single immediate read-back saw TX
    # because the IC-7300 takes tens of ms to report RX after acking TX off.
    host, radio_end = loopback_pair()
    sim = IcomSimulator(IC7300, tx_release_s=0.08)
    attach_loopback(sim, radio_end)
    await radio_end.open()
    driver = IcomCivDriver(IC7300, host, response_timeout_s=0.2)
    await driver.connect()
    assert await driver.key() is True
    await driver.unkey()  # must not raise
    assert sim.reported_tx() is False
    # Patient, not frantic: at most a couple of TX off resends.
    assert sim.tx_history.count(False) <= 1 + 1 + 3
    await driver.close()
    await radio_end.close()


async def test_connect_clears_key_left_by_crashed_process():
    # H7300-4 case C: a process keyed over CI-V and died; the radio stayed in TX.
    host, radio_end = loopback_pair()
    sim = IcomSimulator(IC7300)
    sim.state.transmitting = True
    attach_loopback(sim, radio_end)
    await radio_end.open()
    driver = IcomCivDriver(IC7300, host, response_timeout_s=0.2)
    await driver.connect()
    assert sim.received[0].payload == b"\x1c\x00\x00"
    assert sim.reported_tx() is False
    assert await driver.get_ptt() is False
    await driver.close()
    await radio_end.close()


async def test_levels_meters_and_data_mode(rig):
    from radio_web_remote.radios.base import Level, Meter

    driver, sim, _ = rig
    assert await driver.get_level(Level.AF) == 128
    await driver.set_level(Level.RF_POWER, 64)
    assert sim.state.levels[0x0A] == 64 and await driver.get_level(Level.RF_POWER) == 64
    s = await driver.get_meter(Meter.S)
    assert s.raw == 60 and s.unit == "S"
    assert (await driver.get_meter(Meter.PO)).raw == 0  # receive: no output
    await driver.key()
    await asyncio.sleep(0.01)
    # raw 150 between the printed points 143 (50%) and 213 (100%): 50 + 50 * 7/70
    assert (await driver.get_meter(Meter.PO)).value == pytest.approx(55.0)
    assert (await driver.get_meter(Meter.SWR)).value == pytest.approx(1.31, abs=0.01)
    await driver.unkey()
    assert await driver.get_data_mode() is False
    await driver.set_data_mode(True, 2)
    assert sim.state.data_mode and sim.state.filter_code == 0x02
    await driver.set_data_mode(False)
    assert not sim.state.data_mode


async def test_scope_output_lines_and_settings(rig):
    from radio_web_remote.radios.base import ScopeLine, StateEvent

    driver, sim, _ = rig
    lines: list[ScopeLine] = []
    driver.add_listener(lambda e: lines.append(e.value) if e.kind == "scope" else None)
    sim.scope_interval_s = 0.05
    await driver.set_scope_output(True)
    for _ in range(100):
        if len(lines) >= 2:
            break
        await asyncio.sleep(0.02)
    assert len(lines) >= 2
    line = lines[-1]
    assert (line.low_hz, line.high_hz) == (14_074_000 - 50_000, 14_074_000 + 50_000)
    assert len(line.points) == 475 and max(line.points) >= 100  # the 14.074 test carrier
    # Control traffic still works while scope data streams.
    assert await driver.get_frequency() == 14_074_000
    assert await driver.get_scope_setting("span") == 50_000
    await driver.set_scope_setting("span", 25_000)
    await driver.set_scope_setting("mode", "fixed")
    await driver.set_scope_setting("ref", -10.5)
    await driver.set_scope_setting("speed", "slow")
    assert await driver.get_scope_setting("mode") == "fixed"
    assert await driver.get_scope_setting("ref") == -10.5
    assert await driver.get_scope_setting("speed") == "slow"
    assert sim.state.scope_span == 25_000
    await driver.set_scope_output(False)
    assert not sim.state.scope_output
    assert driver.scope.stats.dropped == 0
    _ = StateEvent


# -- review round 1 (docs/review-changelog.md) ---------------------------------


async def test_unkey_survives_an_ng_to_tx_off(rig):
    """Review A1: an FA (for example a late one) answering TX off must not skip
    the read-back and resend; the radio still ends up in RX."""
    driver, sim, _ = rig
    original = sim._handle
    first = {"ng": True}

    def ng_once(frame, now):
        if first["ng"] and frame.payload == b"\x1c\x00\x00":
            first["ng"] = False
            return bytes([0xFA])
        return original(frame, now)

    sim._handle = ng_once
    await driver.key()
    assert sim.state.transmitting
    assert await driver.unkey() is True
    assert not sim.state.transmitting and not first["ng"]


async def test_link_errors_are_radio_errors(rig):
    """Review A4: a closed transport surfaces as RadioError, which every caller handles."""
    from radio_web_remote.radios.base import RadioError

    driver, _, _ = rig
    await driver.transport.close()
    with pytest.raises(RadioError):
        await driver.get_frequency()


async def test_unkey_resend_never_unkeys_a_newer_key(rig):
    """Review A2-1: a key that lands while unkey's read-back is in flight must not
    be undone by that unkey's resend."""
    driver, sim, _ = rig
    sim.tx_release_s = 0.4  # a radio slow to report RX: resends are due
    await driver.key()
    task = asyncio.create_task(driver.unkey())
    await asyncio.sleep(0.3)
    assert await driver.key() is True
    assert await task is False  # superseded by the key
    await asyncio.sleep(0.5)  # anything a stale resend would have done is done
    assert sim.state.transmitting and sim.tx_history[-1] is True


async def test_malformed_reply_is_a_radio_error(rig):
    """Review A2-3: a well-framed reply in an unexpected form is RadioBadReply."""
    from radio_web_remote.radios.base import RadioBadReply

    driver, sim, _ = rig
    original = sim._handle
    sim._handle = lambda frame, now: (
        b"\x03\x00\xff" if frame.payload == b"\x03" else original(frame, now)
    )
    with pytest.raises(RadioBadReply):
        await driver.get_frequency()


# -- review round 3 ------------------------------------------------------------


async def test_unkey_treats_a_malformed_read_back_as_a_miss(rig):
    """Review A3-1: a garbled TX-state answer must not end the confirmation."""
    driver, sim, _ = rig
    original = sim._handle
    bad = {"left": 1}

    def garble_once(frame, now):
        if bad["left"] and frame.payload == b"\x1c\x00":
            bad["left"] -= 1
            return b"\x1c\x00\x02"
        return original(frame, now)

    sim._handle = garble_once
    await driver.key()
    assert await driver.unkey() is True
    assert not sim.state.transmitting and not bad["left"]


async def test_unkey_confirms_after_a_stuck_poll(rig):
    """Review A3-2: a poll whose reply was lost holds the bus until its timeout;
    the read-back after it must still have time to hear the radio say RX."""
    driver, sim, _ = rig
    driver.timeout = 0.5  # as long as the whole confirmation window
    original = sim._handle
    lost = {"left": 1}

    def lose_one_poll(frame, now):
        if lost["left"] and frame.payload == b"\x03":
            lost["left"] -= 1
            return None
        return original(frame, now)

    await driver.key()
    sim._handle = lose_one_poll
    poll = asyncio.create_task(driver.get_frequency())
    await asyncio.sleep(0.01)
    assert await driver.unkey() is True
    assert not sim.state.transmitting
    await poll


# -- review round 4 ------------------------------------------------------------


async def test_unkey_survives_a_lost_read_back_reply(rig):
    """Review A4-1: one lost read-back answer must not use up the whole window."""
    driver, sim, _ = rig
    driver.timeout = 0.5
    original = sim._handle
    lost = {"left": 1}

    def lose_one_read(frame, now):
        if lost["left"] and frame.payload == b"\x1c\x00":
            lost["left"] -= 1
            return None
        return original(frame, now)

    await driver.key()
    sim._handle = lose_one_read
    assert await driver.unkey() is True
    assert not lost["left"]


async def test_unkey_after_a_stuck_poll_gets_several_read_backs(rig):
    """Review A4-2: the stuck command gives up the bus at once, so a lost
    read-back answer after it is retried within the window."""
    driver, sim, _ = rig
    driver.timeout = 0.5
    original = sim._handle
    lost = {"poll": 1, "read": 1}

    def lose(frame, now):
        if lost["poll"] and frame.payload == b"\x03":
            lost["poll"] -= 1
            return None
        if lost["read"] and frame.payload == b"\x1c\x00":
            lost["read"] -= 1
            return None
        return original(frame, now)

    await driver.key()
    sim._handle = lose
    poll = asyncio.create_task(driver.get_frequency())
    await asyncio.sleep(0.01)
    assert await driver.unkey() is True
    assert not sim.state.transmitting
    assert await poll  # the overtaken poll is retried and answered


# -- review round 5 ------------------------------------------------------------


async def test_tx_off_keeps_its_place_ahead_of_later_urgent_reads(rig):
    """Review A5-1: an urgent read queued while TX off waits for a stuck command
    must not go out before it."""
    driver, sim, _ = rig
    driver.timeout = 0.5
    original = sim._handle
    lost = {"poll": 1}

    def lose(frame, now):
        if lost["poll"] and frame.payload == b"\x03":
            lost["poll"] -= 1
            return None
        return original(frame, now)

    await driver.key()
    sim._handle = lose
    start = len(sim.received)
    poll = asyncio.create_task(driver.get_frequency())
    await asyncio.sleep(0.01)
    unkey = asyncio.create_task(driver.unkey())
    await asyncio.sleep(0.02)  # inside unkey's wait for the stuck poll
    read = asyncio.create_task(driver.get_ptt())
    assert await unkey is True
    await read
    await poll
    sent = [f.payload for f in sim.received[start:]]
    assert sent.index(b"\x1c\x00\x00") < sent.index(b"\x1c\x00")


async def test_overtaken_command_retries_after_the_tx_off(rig):
    """Review A5-2: the overtaken command's retry must not hold the bus while
    the TX off is being confirmed."""
    driver, sim, _ = rig
    driver.timeout = 0.5
    original = sim._handle
    lost = {"poll": 2}

    def lose(frame, now):
        if lost["poll"] and frame.payload == b"\x03":
            lost["poll"] -= 1
            return None
        return original(frame, now)

    await driver.key()
    sim._handle = lose
    poll = asyncio.create_task(driver.get_frequency())
    await asyncio.sleep(0.01)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await driver.unkey() is True
    assert loop.time() - t0 < 0.3
    with contextlib.suppress(Exception):
        await poll
