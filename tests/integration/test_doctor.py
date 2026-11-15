"""doctor: findings and fixes, against the simulator (no hardware)."""

import socket

from radio_web_remote import doctor
from radio_web_remote.audio.devices import AudioDevice
from radio_web_remote.config import Config, RadioConfig, SerialMatch
from radio_web_remote.devices.serial_ports import PortInfo
from radio_web_remote.radios.icom import IC705
from radio_web_remote.sim.icom import IcomSimulator, attach_loopback
from radio_web_remote.transport.loopback import loopback_pair

# Port shapes as observed on hardware (docs/hardware-test.md), serials made up.
IC7300 = PortInfo(
    "/dev/cu.usbserial-1", "CP2102 USB to UART", "IC-7300 11111111", "0-1.1", 0x10C4, 0xEA60
)
IC705_A = PortInfo("/dev/cu.usbmodem101", "IC-705", "IC-705 22222222", "0-1.2", 0x0C26, 0x0036)
IC705_B = PortInfo("/dev/cu.usbmodem103", "IC-705", "IC-705 22222222", "0-1.2", 0x0C26, 0x0036)
IC9700_A = PortInfo(
    "/dev/cu.usbserial-20", "CP2102N", "IC-9700 33333333 A", "0-1.3", 0x10C4, 0xEA60
)
IC9700_B = PortInfo(
    "/dev/cu.usbserial-30", "CP2102N", "IC-9700 33333333 B", "0-1.4", 0x10C4, 0xEA60
)
OTHER = PortInfo("/dev/cu.Bluetooth-Incoming-Port", "n/a", None, None, None, None)
CODECS = [
    AudioDevice(3, "USB Audio CODEC", 0, 2, "Core Audio"),
    AudioDevice(4, "USB Audio CODEC", 2, 0, "Core Audio"),
    AudioDevice(6, "MacBook Pro Microphone", 1, 0, "Core Audio"),
]


def test_model_of_each_port_shape():
    assert [doctor.model_of(p) for p in (IC7300, IC705_A, IC9700_B, OTHER)] == [
        "IC-7300",
        "IC-705",
        "IC-9700",
        None,
    ]
    no_serial = PortInfo("COM6", "IC-705 Serial Port A (CI-V)", None, None, 0x0C26, 0x0036)
    assert doctor.model_of(no_serial) == "IC-705"


def test_suggests_one_entry_per_unconfigured_radio_on_its_ci_v_port():
    r = doctor.Report()
    doctor.suggest_radios(r, Config(), [IC705_B, IC705_A, IC9700_B, IC9700_A, OTHER])
    texts = [f.text for f in r.findings]
    assert len(texts) == 2
    assert 'usb_serial: "IC-705 22222222"' in texts[0] and "icom.ic705" in texts[0]
    assert 'usb_serial: "IC-9700 33333333 A"' in texts[1]  # port A, not B


def _sim_factory(sim):
    def make(port, baud):
        host, radio_end = loopback_pair()
        attach_loopback(sim, radio_end)
        radio_end._open = True
        return host

    return make


async def test_radio_at_a_changed_address_is_found_and_reported():
    """The maintainer's IC-705 was set to 94 (H705-1); doctor finds it and says what to set."""
    sim = IcomSimulator(IC705, address=0x94)
    rc = RadioConfig(
        id="ic705", driver="icom.ic705", match=SerialMatch(usb_serial="IC-705 22222222")
    )
    r = doctor.Report()
    await doctor.check_radio(
        r, rc, [IC705_B, IC705_A], CODECS, _sim_factory(sim), listen_audio=lambda i: 0.2
    )
    assert not r.failed
    assert any("answered at CI-V address 94, not A4" in f.text for f in r.findings)
    assert any(f.fix and 'civ_address: "94"' in f.fix for f in r.findings)
    assert any("serial port /dev/cu.usbmodem101" in f.text for f in r.findings)
    # At 94 the radio first hears the broadcast read ID, then TX off before anything else.
    assert [f.payload for f in sim.received[:2]] == [b"\x19\x00", b"\x1c\x00\x00"]


async def test_silent_codec_and_missing_radio():
    sim = IcomSimulator(IC705)
    rc = RadioConfig(
        id="ic705", driver="icom.ic705", match=SerialMatch(usb_serial="IC-705 22222222")
    )
    r = doctor.Report()
    await doctor.check_radio(
        r, rc, [IC705_A], CODECS, _sim_factory(sim), listen_audio=lambda i: 0.0
    )
    assert any(f.text == "receive audio is pure silence" and f.fix for f in r.findings)
    r2 = doctor.Report()
    await doctor.check_radio(r2, rc, [IC7300], CODECS, _sim_factory(sim))
    assert r2.failed and "no serial port with USB serial number" in r2.findings[0].text


async def test_unanswering_radio_fails_with_a_fix():
    sim = IcomSimulator(IC705, silent=True)
    rc = RadioConfig(id="ic705", driver="icom.ic705", match=SerialMatch(port="/dev/cu.usbmodem101"))
    r = doctor.Report()
    await doctor.check_radio(r, rc, [IC705_A], CODECS, _sim_factory(sim))
    assert r.failed
    fixes = [f.fix for f in r.findings if f.fix]
    assert any("usb_serial" in f for f in fixes)  # matched by port name: advise serial
    assert any("CI-V Address matches civ_address" in f for f in fixes)
    assert "What to fix" in r.render()


async def test_switched_off_radio_says_switch_it_on():
    """An IC-7300 switched off with USB in answers every command NG (2026-11-14)."""
    sim = IcomSimulator(IC705, powered_off=True)
    rc = RadioConfig(id="ic705", driver="icom.ic705", match=SerialMatch(port="/dev/cu.usbmodem101"))
    r = doctor.Report()
    await doctor.check_radio(r, rc, [IC705_A], CODECS, _sim_factory(sim))
    assert r.failed
    assert any("refuses commands" in f.text for f in r.findings)
    assert any(f.fix and "switch the radio on" in f.fix for f in r.findings)
    assert not any(f.fix and "CI-V Address matches" in f.fix for f in r.findings)


async def test_two_radios_auto_audio_by_usb_hub():
    """Both codecs are "USB Audio CODEC"; doctor picks the one on the radio's hub."""
    sim = IcomSimulator(IC705)
    port = PortInfo("/dev/cu.usbmodem101", "IC-705", "IC-705 22222222", "34-3.1", 0x0C26, 0x36)
    devices = [
        AudioDevice(1, "USB Audio CODEC", 0, 2, "Core Audio", "32-3.4"),
        AudioDevice(2, "USB Audio CODEC", 2, 0, "Core Audio", "32-3.4"),
        AudioDevice(3, "USB Audio CODEC", 0, 2, "Core Audio", "34-3.4"),
        AudioDevice(4, "USB Audio CODEC", 2, 0, "Core Audio", "34-3.4"),
    ]
    rc = RadioConfig(
        id="ic705", driver="icom.ic705", match=SerialMatch(usb_serial="IC-705 22222222")
    )
    r = doctor.Report()
    await doctor.check_radio(r, rc, [port], devices, _sim_factory(sim), listen_audio=lambda i: 0.2)
    assert not r.failed, r.render()
    texts = [f.text for f in r.findings]
    assert "audio input: device 4 (USB Audio CODEC on the radio's USB hub, 34-3)" in texts
    assert "audio output: device 3 (USB Audio CODEC on the radio's USB hub, 34-3)" in texts


def test_busy_https_port_is_reported():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        cfg = Config.model_validate({"server": {"bind_address": "127.0.0.1", "https_port": port}})
        r = doctor.Report()
        assert doctor.check_listen_port(r, cfg) is False
        assert r.findings[0].level == "warn"


def test_bind_failure_other_than_in_use_is_not_reported_as_running():
    """Review E1: an address that is not on this computer is a config problem."""
    cfg = Config.model_validate({"server": {"bind_address": "192.0.2.1", "https_port": 8473}})
    r = doctor.Report()
    assert doctor.check_listen_port(r, cfg) is True  # radio checks still run
    assert r.findings[0].level == "fail" and "in use" not in r.findings[0].text


def test_suggestions_identify_radios_not_ports():
    """Review E9: IC-9700 port B sorting first; and a configured IC-705 on port A
    must not get a second suggestion for its port B."""
    nine_b_first = PortInfo(
        "/dev/cu.a-10", "CP2102N", "IC-9700 33333333 B", "0-1.4", 0x10C4, 0xEA60
    )
    nine_a = PortInfo("/dev/cu.b-20", "CP2102N", "IC-9700 33333333 A", "0-1.3", 0x10C4, 0xEA60)
    r = doctor.Report()
    doctor.suggest_radios(r, Config(), [nine_b_first, nine_a])
    assert len(r.findings) == 1 and 'usb_serial: "IC-9700 33333333 A"' in r.findings[0].text
    cfg = Config.model_validate(
        {
            "radios": [
                {"id": "ic705", "driver": "icom.ic705", "match": {"usb_serial": "IC-705 22222222"}}
            ]
        }
    )
    r = doctor.Report()
    doctor.suggest_radios(r, cfg, [IC705_A, IC705_B])
    assert r.findings == []


def test_damaged_user_database_is_a_finding(tmp_path):
    """Review E3-5: doctor names the problem instead of stopping."""
    (tmp_path / "users.db").write_bytes(b"not a database at all, just text" * 10)
    report = doctor.Report()
    doctor.check_data(report, Config(), tmp_path)
    assert report.failed and "user database" in report.findings[-1].text


def test_doctor_reads_the_config_serve_reads(tmp_path, monkeypatch):
    """Review E4-1: without --data-dir, ./config.yaml wins for doctor too."""
    import asyncio

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RADIO_WEB_REMOTE_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text("server:\n  https_port: 9001\n")
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("server:\n  https_port: 9002\n")
    report = asyncio.run(doctor.run(None, data, skip_radio=True, ports=[], devices=[]))
    text = report.render()
    assert "9001" in text and "9002" not in text


def test_broken_local_ca_is_a_doctor_failure(tmp_path):
    """Review E4-2: what makes serve refuse to start fails doctor too."""
    from radio_web_remote.security import tls
    from radio_web_remote.security.users import UserStore

    UserStore(tmp_path / "users.db")
    tls.ensure_certificates(Config(), tmp_path)
    (tls.tls_dir(tmp_path) / "ca.key").unlink()
    report = doctor.Report()
    doctor.check_data(report, Config(), tmp_path)
    assert any(f.level == "fail" and "local CA" in f.text for f in report.findings)


def test_doctor_lists_ca_and_database_damage_under_what_to_fix(tmp_path):
    """Review E6-1, C6-3: a FAIL with nothing in "What to fix" read as healthy."""
    from radio_web_remote.security import tls
    from radio_web_remote.security.users import UserStore

    UserStore(tmp_path / "users.db")
    tls.ensure_certificates(Config(), tmp_path)
    (tls.tls_dir(tmp_path) / "ca.key").unlink()
    report = doctor.Report()
    doctor.check_data(report, Config(), tmp_path)
    text = report.render()
    assert "Nothing to fix" not in text and "Restore it from a backup" in text
    assert not any("re-issued" in f.text for f in report.findings)
