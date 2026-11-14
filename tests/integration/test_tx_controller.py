"""PTT fail-safe: one test per trigger in ADR 0001 D6, against the real driver and simulator."""

import asyncio
import contextlib

import pytest

from radio_web_remote.config import PttConfig
from radio_web_remote.radios.icom import IC7300, IcomCivDriver
from radio_web_remote.sim.icom import IcomSimulator, attach_loopback
from radio_web_remote.station.tx_controller import (
    DenyReason,
    Operator,
    Role,
    StopReason,
    TxController,
    TxDenied,
    TxLimits,
)
from radio_web_remote.transport.loopback import loopback_pair

ALICE = Operator("sess-alice-0001", "alice", "N0CALL", Role.OPERATOR)
BOB = Operator("sess-bob-00002", "bob", "N1CALL", Role.OPERATOR)
LISTENER = Operator("sess-lis-00003", "lee", "N2CALL", Role.LISTENER)
NO_CALL = Operator("sess-nocall-04", "nc", "", Role.OPERATOR)
TWENTY_ONLY = Operator(
    "sess-carol-005",
    "carol",
    "N3CALL",
    Role.OPERATOR,
    TxLimits(bands_hz=((14_000_000, 14_350_000),)),
)


class Rig:
    def __init__(self, ptt: PttConfig) -> None:
        self.host, self.radio_end = loopback_pair()
        self.sim = IcomSimulator(IC7300)
        attach_loopback(self.sim, self.radio_end)
        self.driver = IcomCivDriver(IC7300, self.host, response_timeout_s=0.2)
        self.audit: list[dict] = []
        self.freq = 14_200_000
        self.tx = TxController(
            "ic7300",
            self.driver,
            ptt,
            frequency=lambda: self.freq,
            audit=self.audit.append,
            watchdog_interval_s=0.02,
        )

    async def start(self) -> None:
        await self.radio_end.open()
        await self.driver.connect()
        await self.tx.start()

    async def close(self) -> None:
        await self.tx.close()
        await self.driver.close()
        await self.radio_end.close()

    @property
    def on_air(self) -> bool:
        return self.sim.state.transmitting

    def stops(self) -> list[str]:
        return [a["reason"] for a in self.audit if a["event"] == "tx_stop"]


@pytest.fixture
async def rig():
    r = Rig(PttConfig(heartbeat_interval_s=0.1, heartbeat_timeout_s=0.5, tx_timeout_s=180))
    await r.start()
    yield r
    await r.close()


async def keep_alive(rig: Rig, op: Operator, seconds: float) -> None:
    end = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < end:
        rig.tx.heartbeat(op)
        await asyncio.sleep(0.05)


# -- each stop trigger -------------------------------------------------------


async def test_release_unkeys(rig):
    await rig.tx.ptt_down(ALICE)
    assert rig.on_air and rig.tx.status.holder_callsign == "N0CALL"
    await rig.tx.ptt_up(ALICE)
    assert not rig.on_air and not rig.tx.status.transmitting
    assert rig.stops() == ["released"]


async def test_connection_close_unkeys(rig):
    await rig.tx.ptt_down(ALICE)
    await rig.tx.client_gone(ALICE.session_id, StopReason.CONNECTION_CLOSED)
    assert not rig.on_air
    assert rig.stops() == ["connection_closed"]


async def test_missed_heartbeats_unkey_within_timeout(rig):
    await rig.tx.ptt_down(ALICE)
    await keep_alive(rig, ALICE, 0.6)  # heartbeats keep it keyed past the timeout length
    assert rig.on_air
    rig.tx.heartbeat(ALICE)  # the last heartbeat, so elapsed is measured from it
    t0 = asyncio.get_running_loop().time()
    while rig.on_air:
        await asyncio.sleep(0.01)
        assert asyncio.get_running_loop().time() - t0 < 1.0, "never unkeyed"
    elapsed = asyncio.get_running_loop().time() - t0
    assert 0.45 <= elapsed <= 0.75  # heartbeat_timeout_s = 0.5, watchdog 20 ms
    assert rig.stops() == ["heartbeat_lost"]


async def test_hidden_page_unkeys(rig):
    await rig.tx.ptt_down(ALICE)
    await rig.tx.client_gone(ALICE.session_id, StopReason.HIDDEN)
    assert not rig.on_air
    assert rig.stops() == ["hidden"]


async def test_tx_timeout_unkeys_and_latches():
    r = Rig(PttConfig(heartbeat_interval_s=0.1, heartbeat_timeout_s=0.5, tx_timeout_s=0.3))
    await r.start()
    try:
        await r.tx.ptt_down(ALICE)
        await keep_alive(r, ALICE, 0.5)  # heartbeats alone must not keep TX past the timeout
        assert not r.on_air
        assert r.stops() == ["timeout"]
        # Still holding the button: a new press is refused until released.
        with pytest.raises(TxDenied) as e:
            await r.tx.ptt_down(ALICE)
        assert e.value.reason is DenyReason.RELEASE_FIRST
        await r.tx.ptt_up(ALICE)
        await r.tx.ptt_down(ALICE)  # release then press: a new timer starts
        assert r.on_air
        await r.tx.ptt_up(ALICE)
    finally:
        await r.close()


async def test_frequency_leaving_band_limits_unkeys(rig):
    await rig.tx.ptt_down(TWENTY_ONLY)
    await rig.tx.frequency_changed(14_300_000)
    assert rig.on_air
    await rig.tx.frequency_changed(14_400_000)
    assert not rig.on_air
    assert rig.stops() == ["out_of_limits"]


async def test_logout_unkeys(rig):
    await rig.tx.ptt_down(ALICE)
    await rig.tx.client_gone(ALICE.session_id, StopReason.LOGGED_OUT)
    assert not rig.on_air
    assert rig.stops() == ["logged_out"]


async def test_shutdown_unkeys(rig):
    await rig.tx.ptt_down(ALICE)
    await rig.tx.close()
    assert not rig.on_air
    assert rig.stops() == ["shutdown"]


# -- who may transmit --------------------------------------------------------


async def test_single_transmitter_lock(rig):
    await rig.tx.ptt_down(ALICE)
    with pytest.raises(TxDenied) as e:
        await rig.tx.ptt_down(BOB)
    assert e.value.reason is DenyReason.HELD_BY_OTHER and e.value.detail == "N0CALL"
    # Bob cannot release Alice's transmission either.
    await rig.tx.ptt_up(BOB)
    await rig.tx.client_gone(BOB.session_id, StopReason.CONNECTION_CLOSED)
    assert rig.on_air
    await rig.tx.ptt_up(ALICE)
    await rig.tx.ptt_down(BOB)
    assert rig.tx.status.holder_callsign == "N1CALL"
    await rig.tx.ptt_up(BOB)


@pytest.mark.parametrize("op", [LISTENER, NO_CALL], ids=["listener", "no-callsign"])
async def test_not_permitted(rig, op):
    with pytest.raises(TxDenied) as e:
        await rig.tx.ptt_down(op)
    assert e.value.reason is DenyReason.NOT_PERMITTED
    assert not rig.on_air and rig.sim.tx_history == [False]  # only the connect-time TX off


async def test_out_of_band_key_refused(rig):
    rig.freq = 7_074_000
    with pytest.raises(TxDenied) as e:
        await rig.tx.ptt_down(TWENTY_ONLY)
    assert e.value.reason is DenyReason.OUT_OF_LIMITS
    assert not rig.on_air


async def test_unknown_frequency_refused_when_limited(rig):
    rig.freq = None
    with pytest.raises(TxDenied):
        await rig.tx.ptt_down(TWENTY_ONLY)


# -- races and radio failures ------------------------------------------------


async def test_release_during_key_ends_unkeyed(rig):
    down = asyncio.create_task(rig.tx.ptt_down(ALICE))
    await asyncio.sleep(0)
    await rig.tx.ptt_up(ALICE)
    with contextlib.suppress(TxDenied):
        await down
    await asyncio.sleep(0.1)
    assert not rig.on_air
    assert not rig.tx.status.transmitting


async def test_radio_error_on_key_is_reported_and_safe(rig):
    rig.sim.silent = True
    with pytest.raises(TxDenied) as e:
        await rig.tx.ptt_down(ALICE)
    assert e.value.reason is DenyReason.RADIO_ERROR
    assert not rig.tx.status.transmitting
    assert rig.tx.unkey_unconfirmed  # it could not confirm RX either
    assert rig.tx.status.unkey_unconfirmed  # shown to every client
    assert any(a["event"] == "tx_off_unconfirmed" for a in rig.audit)
    rig.sim.silent = False
    for _ in range(100):  # watchdog keeps sending TX off until confirmed
        if not rig.tx.unkey_unconfirmed:
            break
        await asyncio.sleep(0.05)
    assert not rig.tx.unkey_unconfirmed and not rig.on_air
    assert not rig.tx.status.unkey_unconfirmed
    confirmed = [a for a in rig.audit if a["event"] == "tx_off_confirmed"]
    assert confirmed and confirmed[0]["after_s"] > 0


async def test_audit_records_start_and_stop(rig):
    await rig.tx.ptt_down(ALICE)
    await asyncio.sleep(0.05)
    await rig.tx.ptt_up(ALICE)
    start, stop = rig.audit
    assert start["event"] == "tx_start" and start["callsign"] == "N0CALL"
    assert start["radio"] == "ic7300" and start["frequency_hz"] == 14_200_000
    assert stop["event"] == "tx_stop" and stop["reason"] == "released"
    assert stop["duration_s"] >= 0.05


async def test_status_listener_sees_holder(rig):
    seen = []
    rig.tx.add_listener(seen.append)
    await rig.tx.ptt_down(ALICE)
    await rig.tx.ptt_up(ALICE)
    assert seen[0].transmitting and seen[0].holder_callsign == "N0CALL"
    assert not seen[-1].transmitting and seen[-1].last_stop_reason is StopReason.RELEASED


# -- regressions found by the web tests (2026-11-13) --------------------------


async def test_cancelled_release_still_unkeys(rig):
    # A closing WebSocket cancels its handler while it is releasing PTT.
    await rig.tx.ptt_down(ALICE)
    rig.sim.reply_delay_s = 0.1  # a slow radio: a poll holds the bus for 100 ms
    poll = asyncio.create_task(rig.driver.get_frequency())
    await asyncio.sleep(0.01)
    task = asyncio.create_task(rig.tx.ptt_up(ALICE))
    await asyncio.sleep(0.01)  # unkey is now waiting for the bus; cancel it there
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await poll
    rig.sim.reply_delay_s = 0.003
    for _ in range(100):
        if not rig.sim.reported_tx():
            break
        await asyncio.sleep(0.02)
    assert not rig.sim.reported_tx()
    assert not rig.tx.status.transmitting


async def test_quick_rekey_is_not_killed_by_previous_unkey(rig):
    # Release then press again within the radio's 15-65 ms TX release time:
    # the first unkey's read-back must not send TX off over the new key.
    await rig.tx.ptt_down(ALICE)
    release = asyncio.create_task(rig.tx.ptt_up(ALICE))
    await asyncio.sleep(0.005)
    await rig.tx.ptt_down(ALICE)
    await release
    await asyncio.sleep(0.3)
    assert rig.tx.status.transmitting and rig.tx.status.holder_callsign == "N0CALL"
    assert rig.sim.state.transmitting, "the new transmission was unkeyed by the old unkey"
    await rig.tx.ptt_up(ALICE)
    assert not rig.sim.state.transmitting


# -- transmit frequency read from the radio (IC-705 table has 1C 03) ----------


@pytest.fixture
async def rig705():
    from radio_web_remote.radios.icom import IC705

    r = Rig(PttConfig(heartbeat_interval_s=0.1, heartbeat_timeout_s=0.5, tx_timeout_s=180))
    r.sim = IcomSimulator(IC705)
    r.host, r.radio_end = loopback_pair()
    attach_loopback(r.sim, r.radio_end)
    r.driver = IcomCivDriver(IC705, r.host, response_timeout_s=0.2)
    r.tx = TxController(
        "ic705",
        r.driver,
        r.tx.ptt,
        frequency=lambda: r.freq,  # displayed: 14.200 MHz, inside carol's band
        tx_frequency=r.driver.get_tx_frequency,
        audit=r.audit.append,
        watchdog_interval_s=0.02,
    )
    await r.start()
    yield r
    await r.close()


async def test_limits_use_the_radios_tx_frequency(rig705):
    rig705.sim.state.frequency_hz = 14_200_000
    rig705.sim.state.split_tx_hz = 7_150_000  # split: TX on 40 m
    with pytest.raises(TxDenied) as e:
        await rig705.tx.ptt_down(TWENTY_ONLY)
    assert e.value.reason is DenyReason.OUT_OF_LIMITS
    assert not rig705.on_air and True not in rig705.sim.tx_history


async def test_tx_frequency_moving_out_while_keyed_unkeys(rig705):
    rig705.sim.state.frequency_hz = 14_200_000
    await rig705.tx.ptt_down(TWENTY_ONLY)
    assert rig705.on_air
    await rig705.tx.recheck_frequency()  # still inside: stays keyed
    assert rig705.on_air
    rig705.sim.state.split_tx_hz = 7_150_000  # e.g. split switched on at the radio
    await rig705.tx.frequency_changed(14_200_000)  # displayed frequency did not move
    assert not rig705.on_air
    assert rig705.stops() == ["out_of_limits"]


async def test_unreadable_tx_frequency_refuses_to_key(rig705):
    rig705.sim.silent = True
    with pytest.raises(TxDenied) as e:
        await rig705.tx.ptt_down(ALICE)
    assert e.value.reason is DenyReason.RADIO_ERROR
    assert True not in rig705.sim.tx_history
