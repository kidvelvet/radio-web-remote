"""IC-705 table and driver against the simulator: bytes as printed in the IC-705 CI-V guide."""

import pytest

from radio_web_remote.protocols.civ.frame import Frame
from radio_web_remote.radios import registry
from radio_web_remote.radios.base import Feature, Mode, ModeSetting, RadioRejected, Unsupported
from radio_web_remote.radios.icom import IC705, IC7300, MODELS
from radio_web_remote.sim.icom import IcomSimulator, attach_loopback
from radio_web_remote.transport.loopback import loopback_pair


async def _rig(model):
    host, radio_end = loopback_pair()
    sim = IcomSimulator(model)
    attach_loopback(sim, radio_end)
    await radio_end.open()
    driver = registry.create(
        model.driver_name, host, registry.DriverOptions(response_timeout_s=0.2)
    )
    await driver.connect()
    return driver, sim, radio_end


@pytest.fixture
async def rig():
    driver, sim, end = await _rig(IC705)
    yield driver, sim
    await driver.close()
    await end.close()


def test_registered_with_documented_basics():
    assert "icom.ic705" in registry.names()
    m = MODELS["icom.ic705"]
    assert m.default_address.value == 0xA4  # CI-V guide p. 2
    assert m.modes[Mode.WFM].value == 0x06 and m.modes[Mode.DV].value == 0x17  # p. 17
    assert {
        Feature.FREQUENCY,
        Feature.MODE,
        Feature.PTT,
        Feature.TX_FREQUENCY,
        Feature.SCOPE,
        Feature.DATA_MODE,
        Feature.LEVEL_RF_POWER,
        Feature.METER_SWR,
    } <= m.features
    assert Feature.TX_FREQUENCY not in MODELS["icom.ic7300"].features  # not in its table


def test_every_ic705_source_is_icoms_own_pdf():
    for src in IC705.commands.values():
        assert src.verified_primary and "IC-705" in src.document


async def test_addresses_and_first_frame(rig):
    _, sim = rig
    assert sim.received[0] == Frame(0xA4, 0xE0, b"\x1c\x00\x00")


async def test_extra_modes_on_the_wire(rig):
    driver, sim = rig
    await driver.set_mode(ModeSetting(Mode.WFM, 1))
    assert sim.received[-1].payload == b"\x06\x06\x01"
    await driver.set_mode(ModeSetting(Mode.DV))
    assert sim.received[-1].payload == b"\x06\x17"
    assert (await driver.get_mode()).mode is Mode.DV


async def test_430_mhz_but_not_500(rig):
    driver, sim = rig
    await driver.set_frequency(432_100_000)
    assert sim.received[-1].payload == b"\x05\x00\x00\x10\x32\x04"
    with pytest.raises(ValueError):
        await driver.set_frequency(500_000_000)  # 100 MHz digit only 0 ~ 4 (p. 17)


async def test_tx_frequency_read(rig):
    driver, sim = rig
    sim.state.split_tx_hz = 14_210_000
    assert await driver.get_tx_frequency() == 14_210_000
    assert sim.received[-1].payload == b"\x1c\x03"


async def test_scroll_modes_and_fourth_edge(rig):
    driver, sim = rig
    assert driver.scope_modes() == ("center", "fixed", "scroll-c", "scroll-f")
    await driver.set_scope_setting("mode", "scroll-c")
    assert sim.received[-1].payload == b"\x27\x14\x00\x02"
    assert await driver.get_scope_setting("mode") == "scroll-c"
    await driver.set_scope_setting("edge", 4)
    assert sim.received[-1].payload == b"\x27\x16\x00\x04"
    with pytest.raises(ValueError):
        await driver.set_scope_setting("edge", 5)


async def test_ic7300_refuses_what_only_the_705_documents():
    driver, sim, end = await _rig(IC7300)
    try:
        with pytest.raises(ValueError):
            await driver.set_scope_setting("mode", "scroll-c")
        with pytest.raises(ValueError):
            await driver.set_scope_setting("edge", 4)
        with pytest.raises(Unsupported):  # not in the IC-7300 table
            await driver.get_tx_frequency()
        with pytest.raises(Unsupported):
            await driver.set_mode(ModeSetting(Mode.DV))
        assert all(f.payload[:2] != b"\x1c\x03" for f in sim.received)
    finally:
        await driver.close()
        await end.close()


async def test_sim_refuses_undocumented_scope_codes(rig):
    driver, _ = rig
    from radio_web_remote.radios.icom.civ_driver import IcomCivDriver

    assert isinstance(driver, IcomCivDriver)
    with pytest.raises(RadioRejected):
        await driver._transact(
            driver._SCOPE_SETTINGS["mode"], b"\x27\x14\x00\x04", False
        )  # 04: not in 27 14 0000 ~ 0003
