"""IC-9700 table, MAIN/SUB and transmit band, against the simulator.

Bytes as printed in the IC-9700 CI-V guide (A7508-3EX-4). The simulator's
MAIN/SUB behaviour follows the IC-9700 on hardware (firmware 1.50, H9700-2
and -5): 03 to 06 act on the selected band, the selected band transmits,
SUB cannot be selected with Dualwatch off; see docs/hardware-test.md.
"""

import pytest

from radio_web_remote.config import PttConfig
from radio_web_remote.protocols.civ.frame import Frame
from radio_web_remote.radios import registry
from radio_web_remote.radios.base import Feature, Mode, ModeSetting, RadioRejected
from radio_web_remote.radios.icom import IC9700, MODELS, IcomCivDriver
from radio_web_remote.sim.icom import IcomSimulator, attach_loopback
from radio_web_remote.station.tx_controller import (
    DenyReason,
    Operator,
    Role,
    TxController,
    TxDenied,
    TxLimits,
)
from radio_web_remote.transport.loopback import loopback_pair

SEVENTY_CM = Operator(
    "sess-uhf-00001",
    "uma",
    "N4CALL",
    Role.OPERATOR,
    TxLimits(bands_hz=((420_000_000, 450_000_000),)),
)


@pytest.fixture
async def rig():
    host, radio_end = loopback_pair()
    sim = IcomSimulator(IC9700)
    attach_loopback(sim, radio_end)
    await radio_end.open()
    driver = IcomCivDriver(IC9700, host, response_timeout_s=0.2)
    await driver.connect()
    yield driver, sim
    await driver.close()
    await radio_end.close()


def test_registered_with_documented_basics():
    assert "icom.ic9700" in registry.names()
    m = MODELS["icom.ic9700"]
    assert m.default_address.value == 0xA2
    assert m.modes[Mode.DV].value == 0x17 and m.modes[Mode.DD].value == 0x22  # p. 13
    assert Mode.WFM not in m.modes  # 06 is not listed
    assert {Feature.MAIN_SUB, Feature.SATELLITE, Feature.TX_FREQUENCY, Feature.SCOPE} <= m.features
    assert Feature.MAIN_SUB not in MODELS["icom.ic705"].features
    for src in m.commands.values():
        assert src.verified_primary and "IC-9700" in src.document


async def test_first_frame_and_1296_encoding(rig):
    driver, sim = rig
    assert sim.received[0] == Frame(0xA2, 0xE0, b"\x1c\x00\x00")
    await driver.set_frequency(1_296_100_000)
    assert sim.received[-1].payload == b"\x05\x00\x00\x10\x96\x12"  # 1 GHz digit in byte 5
    with pytest.raises(ValueError):
        await driver.set_frequency(1_500_000_000)


async def test_band_select_read_and_swap(rig):
    driver, sim = rig
    assert await driver.get_band() == "main"
    assert await driver.get_frequency() == 144_200_000
    await driver.set_band("sub")
    sent = [f.payload for f in sim.received[-2:]]
    assert sent == [b"\x07\xd1", b"\x27\x12\x01"]  # select SUB; the scope follows
    assert await driver.get_band() == "sub"
    assert await driver.get_frequency() == 432_100_000
    await driver.set_mode(ModeSetting(Mode.FM))
    await driver.swap_bands()
    assert sim.received[-1].payload == b"\x07\xb0"
    assert await driver.get_band() == "sub"  # the selection stays; the contents swap
    assert await driver.get_frequency() == 144_200_000
    await driver.set_band("main")
    assert await driver.get_frequency() == 432_100_000
    assert (await driver.get_mode()).mode is Mode.FM


async def test_scope_settings_use_the_scope_band_selector(rig):
    driver, sim = rig
    await driver.set_scope_setting("mode", "center")
    assert sim.received[-1].payload == b"\x27\x14\x00\x00"
    await driver.set_band("sub")
    await driver.set_scope_setting("mode", "fixed")
    assert sim.received[-1].payload == b"\x27\x14\x01\x01"  # 01 = SUB scope
    assert await driver.get_scope_setting("mode") == "fixed"


async def test_satellite_and_tx_frequency(rig):
    driver, sim = rig
    await driver.set_band("sub")
    assert await driver.get_tx_frequency() == 432_100_000  # hardware: the selected band transmits
    await driver.set_band("main")
    assert await driver.get_tx_frequency() == 144_200_000
    assert not await driver.get_satellite()
    sim.state.satellite = True
    assert await driver.get_satellite()
    assert await driver.get_tx_frequency() == 432_100_000  # satellite: SUB is the uplink


async def test_sub_needs_dualwatch(rig):
    driver, sim = rig
    assert await driver.get_dualwatch()
    await driver.set_dualwatch(False)
    assert sim.received[-1].payload == b"\x16\x59\x00"
    with pytest.raises(RadioRejected):  # as the radio did with Dualwatch off
        await driver.set_band("sub")
    await driver.set_dualwatch(True)
    await driver.set_band("sub")
    assert await driver.get_band() == "sub"


async def test_tx_limits_follow_the_transmitting_band(rig):
    driver, sim = rig
    await driver.set_band("main")  # 144.2 MHz selected: outside the 70 cm limit
    shown = 432_100_000  # e.g. the page still showing SUB from a moment ago
    tx = TxController(
        "ic9700",
        driver,
        PttConfig(heartbeat_interval_s=0.1, heartbeat_timeout_s=0.5, tx_timeout_s=180),
        frequency=lambda: shown,
        tx_frequency=driver.get_tx_frequency,
        watchdog_interval_s=0.02,
    )
    await tx.start()
    try:
        with pytest.raises(TxDenied) as e:  # the radio reports 144.2 as its TX frequency
            await tx.ptt_down(SEVENTY_CM)
        assert e.value.reason is DenyReason.OUT_OF_LIMITS
        assert True not in sim.tx_history
        sim.state.satellite = True  # now SUB (432.1) is the uplink, MAIN still selected
        await tx.ptt_down(SEVENTY_CM)
        assert sim.state.transmitting
        sim.state.satellite = False  # satellite turned off at the radio while keyed
        await tx.recheck_frequency()
        assert not sim.state.transmitting
    finally:
        await tx.close()
