"""RadioStation: state cache, controls, transceive, link loss and reconnect."""

import asyncio

import pytest

from radio_web_remote.config import PttConfig, RadioConfig
from radio_web_remote.radios.base import Mode, ModeSetting
from radio_web_remote.radios.icom import IC7300
from radio_web_remote.sim.icom import IcomSimulator, attach_loopback, push_unsolicited
from radio_web_remote.station.radio_station import ControlDenied, RadioStation
from radio_web_remote.station.tx_controller import Operator, Role, TxLimits
from radio_web_remote.transport.loopback import loopback_pair

ALICE = Operator("sess-alice-0001", "alice", "N0CALL", Role.OPERATOR)
BOB = Operator("sess-bob-00002", "bob", "N1CALL", Role.OPERATOR)
LEE = Operator("sess-lee-00003", "lee", "", Role.LISTENER)
CAROL = Operator(
    "sess-carol-004",
    "carol",
    "N3CALL",
    Role.OPERATOR,
    TxLimits(bands_hz=((14_000_000, 14_350_000),)),
)


class Bench:
    """A simulator that survives reconnects; each connect gets a fresh loopback link."""

    def __init__(self, model=IC7300) -> None:
        self.sim = IcomSimulator(model)
        self.links: list[tuple] = []

    def factory(self):
        host, radio_end = loopback_pair()
        attach_loopback(self.sim, radio_end)
        radio_end._open = True  # the radio side is "always on"
        self.links.append((host, radio_end))
        self.mark = len(self.sim.received)
        return host

    @property
    def radio_end(self):
        return self.links[-1][1]

    @property
    def host(self):
        return self.links[-1][0]


async def wait_for(cond, timeout=2.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "condition not met in time"
        await asyncio.sleep(0.01)


@pytest.fixture
async def bench():
    b = Bench()
    st = RadioStation(
        RadioConfig(id="ic7300"),
        PttConfig(heartbeat_interval_s=0.1, heartbeat_timeout_s=0.5),
        transport_factory=b.factory,
        poll_interval_s=0.05,
        lost_after_timeouts=2,
        backoff_s=(0.05, 0.2),
        response_timeout_s=0.1,
        tx_watchdog_s=0.02,
    )
    seen = []
    st.add_listener(seen.append)
    await st.start()
    await wait_for(lambda: st.connected)
    b.station, b.seen = st, seen
    yield b
    await st.close()


async def test_connect_reads_state_and_notifies(bench):
    s = bench.station.state
    assert s.connected and s.model == "IC-7300"
    assert (s.frequency_hz, s.mode, s.filter) == (14_074_000, "USB", 1)
    assert "ptt" in s.features and "LSB" in s.modes
    assert bench.seen and bench.seen[-1].connected
    assert bench.sim.received[0].payload == b"\x1c\x00\x00"
    assert s.to_json()["tx"]["transmitting"] is False


async def test_front_panel_changes_reach_state(bench):
    push_unsolicited(bench.radio_end, bench.sim.front_panel_tune(7_030_000))
    await wait_for(lambda: bench.station.state.frequency_hz == 7_030_000)
    bench.sim.state.mode_code = 0x03  # changed without a transceive frame: the poll catches it
    await wait_for(lambda: bench.station.state.mode == "CW")


async def test_operator_controls(bench):
    await bench.station.set_frequency(ALICE, 10_136_000)
    assert bench.sim.state.frequency_hz == 10_136_000
    await bench.station.set_mode(ALICE, ModeSetting(Mode.CW, 2))
    assert (bench.sim.state.mode_code, bench.sim.state.filter_code) == (0x03, 0x02)


async def test_listener_cannot_control(bench):
    with pytest.raises(ControlDenied):
        await bench.station.set_frequency(LEE, 7_000_000)


async def test_only_tx_holder_controls_while_transmitting(bench):
    await bench.station.tx.ptt_down(ALICE)
    with pytest.raises(ControlDenied, match="N0CALL"):
        await bench.station.set_frequency(BOB, 7_000_000)
    await bench.station.set_frequency(ALICE, 14_100_000)
    await bench.station.tx.ptt_up(ALICE)
    await bench.station.set_frequency(BOB, 7_000_000)


async def test_tx_state_is_in_station_state(bench):
    await bench.station.tx.ptt_down(ALICE)
    assert bench.station.state.tx.holder_callsign == "N0CALL"
    await bench.station.tx.ptt_up(ALICE)
    assert not bench.station.state.tx.transmitting


async def test_front_panel_retune_out_of_band_unkeys(bench):
    await bench.station.tx.ptt_down(CAROL)
    assert bench.sim.state.transmitting
    push_unsolicited(bench.radio_end, bench.sim.front_panel_tune(7_100_000))
    await wait_for(lambda: not bench.sim.reported_tx())
    assert bench.station.state.tx.last_stop_reason.value == "out_of_limits"


async def test_serial_error_drops_tx_and_reconnects_with_tx_off_first(bench):
    await bench.station.tx.ptt_down(ALICE)
    old_host = bench.host
    old_host.on_error(OSError("cable unplugged"))
    await wait_for(lambda: not bench.station.state.tx.transmitting)
    assert bench.station.state.tx.last_stop_reason.value == "radio_error"
    await wait_for(lambda: len(bench.links) >= 2 and bench.station.connected)
    first_on_new_link = bench.sim.received[bench.mark]
    assert first_on_new_link.payload == b"\x1c\x00\x00"
    await wait_for(lambda: not bench.sim.reported_tx())


async def test_silent_radio_marks_disconnected_then_recovers(bench):
    bench.sim.silent = True
    await wait_for(lambda: not bench.station.state.connected)
    assert bench.station.state.error
    bench.sim.silent = False
    await wait_for(lambda: bench.station.state.connected, timeout=3.0)
    assert bench.station.state.error is None


async def test_controls_refused_while_disconnected(bench):
    bench.sim.silent = True
    await wait_for(lambda: not bench.station.state.connected)
    with pytest.raises(Exception, match="not connected"):
        await bench.station.set_frequency(ALICE, 7_000_000)


POWER_CAPPED = Operator(
    "sess-dave-0006", "dave", "N4CALL", Role.OPERATOR, TxLimits(max_power_pct=50.0)
)


async def test_state_has_levels_data_mode_and_meters(bench):
    await wait_for(lambda: "s" in bench.station.state.meters)
    st = bench.station.state
    assert st.levels == {"af": 128, "rf_gain": 255, "squelch": 0, "rf_power": 128}
    assert st.data_mode is False
    assert st.meters["s"]["raw"] == 60
    await bench.station.tx.ptt_down(ALICE)
    await wait_for(lambda: "swr" in bench.station.state.meters)
    assert bench.station.state.meters["po"]["raw"] == 150
    await bench.station.tx.ptt_up(ALICE)


async def test_level_and_data_controls(bench):
    from radio_web_remote.radios.base import Level

    await bench.station.set_level(ALICE, Level.AF, 200)
    assert bench.sim.state.levels[0x01] == 200 and bench.station.state.levels["af"] == 200
    await bench.station.set_data_mode(ALICE, True)
    assert bench.sim.state.data_mode and bench.station.state.data_mode
    with pytest.raises(ControlDenied):
        await bench.station.set_level(LEE, Level.AF, 10)


async def test_power_limit(bench):
    from radio_web_remote.radios.base import Level
    from radio_web_remote.station.tx_controller import TxDenied

    # Knob at 128/255 = 50.2% is above a 50% cap: keying is refused.
    with pytest.raises(TxDenied):
        await bench.station.tx.ptt_down(POWER_CAPPED)
    with pytest.raises(ControlDenied):
        await bench.station.set_level(POWER_CAPPED, Level.RF_POWER, 200)
    await bench.station.set_level(POWER_CAPPED, Level.RF_POWER, 100)
    await bench.station.tx.ptt_down(POWER_CAPPED)
    assert bench.sim.state.transmitting
    # Someone turns the RF power knob up on the front panel while keyed: the poll sees it.
    bench.sim.state.levels[0x0A] = 230
    await wait_for(lambda: not bench.sim.reported_tx(), timeout=3.0)
    assert bench.station.state.tx.last_stop_reason.value == "out_of_limits"


async def test_stale_poll_does_not_overwrite_newer_write(bench):
    # Regression (browser tests, 2026-11-13): a poll read the old frequency,
    # a tune landed, then the poll wrote its stale value back over it.
    st = bench.station
    driver = st._live_driver()
    bench.sim.reply_delay_s = 0.03  # widen the window
    poll = asyncio.create_task(st._poll_once(driver))
    await asyncio.sleep(0.005)  # the poll's frequency read is in flight
    await st.set_frequency(ALICE, 21_074_000)
    await poll
    bench.sim.reply_delay_s = 0.003
    assert st.state.frequency_hz == 21_074_000


async def test_idle_radio_loss_does_not_claim_tx_unconfirmed(bench):
    # Found 2026-11-13: with the radio off and nobody transmitting, every client
    # was shown "TX off not confirmed: the radio may still be transmitting".
    bench.sim.silent = True
    await wait_for(lambda: not bench.station.state.connected)
    await asyncio.sleep(0.3)
    assert not bench.station.state.tx.unkey_unconfirmed


async def test_radio_loss_while_keyed_still_warns(bench):
    await bench.station.tx.ptt_down(ALICE)
    bench.sim.silent = True
    await wait_for(lambda: bench.station.state.tx.unkey_unconfirmed, timeout=3.0)


@pytest.fixture
async def bench9700():
    from radio_web_remote.radios.icom import IC9700

    b = Bench(IC9700)
    st = RadioStation(
        RadioConfig(id="ic9700", driver="icom.ic9700"),
        PttConfig(heartbeat_interval_s=0.1, heartbeat_timeout_s=0.5),
        transport_factory=b.factory,
        poll_interval_s=0.05,
        lost_after_timeouts=2,
        backoff_s=(0.05, 0.2),
        response_timeout_s=0.1,
        tx_watchdog_s=0.02,
    )
    await st.start()
    await wait_for(lambda: st.connected)
    b.station = st
    yield b
    await st.close()


async def test_band_state_and_controls(bench9700):
    st, sim = bench9700.station, bench9700.sim
    await wait_for(lambda: st.state.band == "main" and st.state.satellite is False)
    assert st.state.coverage_hz[0] == (144_000_000, 148_000_000)
    await st.set_band(ALICE, "sub")
    assert (st.state.band, st.state.frequency_hz) == ("sub", 432_100_000)
    await st.swap_bands(ALICE)
    assert (st.state.band, st.state.frequency_hz) == ("sub", 144_200_000)
    with pytest.raises(ControlDenied):
        await st.set_band(LEE, "main")  # listeners cannot
    sim.state.selected_band = 0  # changed at the radio: a slow poll notices
    sim._swap_band_values()
    await wait_for(lambda: st.state.band == "main", timeout=3.0)


async def test_band_change_refused_while_transmitting(bench9700):
    st = bench9700.station
    await st.tx.ptt_down(ALICE)
    try:
        with pytest.raises(ControlDenied):
            await st.set_band(ALICE, "sub")  # not even the TX holder
        with pytest.raises(ControlDenied):
            await st.swap_bands(ALICE)
    finally:
        await st.tx.ptt_up(ALICE)
