"""`radio-web-remote doctor`: check this computer, the config and each radio, and say what to fix.

Every check adds a Finding (ok, warn or fail) with a one-line fix where there
is one. Checks that need hardware take their inputs (port and audio device
lists, a transport factory) as arguments so the tests can run them without a
radio. The radio check sends TX off first, like every connect (ADR 0001 D6).
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import platform
import socket
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from radio_web_remote import __version__
from radio_web_remote.audio.devices import CODEC_NAME, AudioDevice, AudioDeviceError, resolve
from radio_web_remote.config import Config, RadioConfig
from radio_web_remote.devices.serial_ports import (
    DRIVER_FOR,
    PortInfo,
    PortNotFound,
    model_for_driver,
    model_of,
    radio_key,
    resolve_port,
)
from radio_web_remote.transport.base import ByteTransport

Level = Literal["ok", "warn", "fail", "info"]


@dataclass
class Finding:
    level: Level
    topic: str
    text: str
    fix: str | None = None


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: Level, topic: str, text: str, fix: str | None = None) -> None:
        self.findings.append(Finding(level, topic, text, fix))

    @property
    def failed(self) -> bool:
        return any(f.level == "fail" for f in self.findings)

    def render(self) -> str:
        tag = {"ok": "  ok  ", "warn": " WARN ", "fail": " FAIL ", "info": "      "}
        lines, topic = [], None
        for f in self.findings:
            if f.topic != topic:
                lines.append(f"\n{f.topic}")
                topic = f.topic
            lines.append(f"[{tag[f.level]}] {f.text}")
        fixes = [f for f in self.findings if f.fix and f.level in ("warn", "fail")]
        if fixes:
            lines.append("\nWhat to fix")
            lines += [f"  - {f.fix}" for f in fixes]
        else:
            lines.append("\nNothing to fix.")
        return "\n".join(lines).lstrip("\n")


# -- host and config -------------------------------------------------------------


def check_host(report: Report) -> None:
    t = "This computer"
    py = sys.version_info
    ok = py >= (3, 11)
    report.add(
        "ok" if ok else "fail",
        t,
        f"Python {py.major}.{py.minor}.{py.micro} on {platform.system()} {platform.release()}",
        None if ok else "Install Python 3.11 or newer.",
    )
    report.add("info", t, f"radio-web-remote {__version__}")


def check_config(
    report: Report, cfg: Config | None, source: Path | None, error: str | None
) -> None:
    t = "Configuration"
    if error:
        report.add(
            "fail",
            t,
            f"config does not load: {error}",
            "Fix the config file, then run `radio-web-remote config check`.",
        )
        return
    assert cfg is not None
    report.add("ok", t, f"loaded from {source}" if source else "no config file: built-in defaults")
    if not cfg.radios:
        report.add(
            "warn",
            t,
            "no radios configured",
            'Add the radio under `radios:` (see "Not configured yet").',
        )
    from radio_web_remote.radios import registry

    known = set(registry.names())
    for rc in cfg.radios:
        if rc.driver not in known:
            report.add(
                "fail",
                t,
                f"radio {rc.id!r}: unknown driver {rc.driver!r}",
                f"Use one of: {', '.join(sorted(known))}.",
            )


def check_data(report: Report, cfg: Config, data_dir: Path) -> None:
    t = "Accounts and certificates"
    from radio_web_remote.security.tls import (
        _pair_problem,
        needs_new_server_cert,
        tls_dir,
        uncovered_by_ca,
        wanted_sans,
    )

    db = data_dir / "users.db"
    if not db.exists():
        report.add(
            "fail",
            t,
            f"no user database in {data_dir}",
            "Run `radio-web-remote init` to create the admin account and certificates.",
        )
        return
    from radio_web_remote.security.users import UserStore

    try:
        store = UserStore(db)
        users, admins = store.list_users(), store.count_admins()
    except (sqlite3.Error, OSError) as exc:
        report.add(
            "fail",
            t,
            f"cannot read the user database {db}: {exc}",
            "Check its permissions; if it is damaged, restore it from a backup.",
        )
        return
    report.add(
        "ok" if admins else "fail",
        t,
        f"{len(users)} account(s), {admins} admin(s)",
        None if admins else "Create an admin: `radio-web-remote user add <name> --role admin`.",
    )
    if cfg.server.tls.cert_file and cfg.server.tls.key_file:
        problem = _own_certificate_problem(cfg.server.tls.cert_file, cfg.server.tls.key_file)
        if problem:
            report.add(
                "fail", t, f"your certificate: {problem}", "Fix server.tls.cert_file and key_file."
            )
        else:
            report.add("ok", t, f"using your own certificate {cfg.server.tls.cert_file}")
        return
    d = tls_dir(data_dir)
    sans = wanted_sans(cfg)
    reason = needs_new_server_cert(d / "server.crt", sans)
    if reason:
        report.add("warn", t, f"server certificate will be re-issued at start: {reason}")
    else:
        report.add("ok", t, f"server certificate covers {sans.describe()}")
    ca = [(d / n).exists() for n in ("ca.crt", "ca.key")]
    ca_problem = (
        _pair_problem(d / "ca.crt", d / "ca.key")
        if all(ca)
        else "file missing"
        if any(ca)
        else None
    )
    if ca_problem:  # serve refuses to start with this
        report.add(
            "fail",
            t,
            f"local CA: {ca_problem}",
            "Restore ca.crt and ca.key from a backup, or run `radio-web-remote cert reset` "
            "and reinstall the CA on each device from /setup.",
        )
    elif all(ca):
        missing = uncovered_by_ca(d / "ca.crt", sans)
        if missing:
            report.add(
                "fail",
                t,
                "the local CA is not allowed to cover: " + ", ".join(missing),
                "Run `radio-web-remote cert reset`, then reinstall the CA on each device "
                "from /setup.",
            )


def _own_certificate_problem(cert_file: str, key_file: str) -> str | None:
    import datetime as dt

    from radio_web_remote.security.tls import _pair_problem

    problem = _pair_problem(Path(cert_file), Path(key_file))
    if problem:
        return problem
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(Path(cert_file).read_bytes())
    if cert.not_valid_after_utc < dt.datetime.now(dt.UTC):
        return f"expired on {cert.not_valid_after_utc:%Y-%m-%d}"
    return None


def check_listen_port(report: Report, cfg: Config) -> bool:
    """True if the HTTPS port is free (no server running)."""
    t = "Network"
    host, port = cfg.server.bind_address, cfg.server.https_port
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
        if os.name != "nt":  # as uvicorn does: a just-stopped server's TIME_WAIT is not "in use"
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
                report.add(
                    "warn",
                    t,
                    f"port {port} on {host} is in use (is the server already running?)",
                    "Stop the running server so doctor can test the radio, or change https_port.",
                )
                return False
            report.add(
                "fail",
                t,
                f"cannot listen on {host}:{port}: {exc.strerror or exc}",
                "Check server.bind_address (an address of this computer, or 0.0.0.0) "
                "and https_port (above 1023 unless run as root).",
            )
            return True  # the server could not start either, but the radio can be checked
    report.add("ok", t, f"https_port {port} is free on {host}")
    return True


# -- serial ports and audio ----------------------------------------------------------


def check_ports(report: Report, ports: list[PortInfo]) -> None:
    t = "Serial ports"
    radios = [p for p in ports if model_of(p)]
    if not radios:
        report.add(
            "warn",
            t,
            f"no radio serial port found ({len(ports)} port(s) in total)",
            "Check the USB cable and that the radio is on; on Windows, install Icom's USB driver.",
        )
    for p in ports:
        ids = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None and p.pid is not None else "-"
        model = model_of(p)
        what = f"{model} " if model else ""
        report.add("ok" if model else "info", t, f"{p.device}  {what}{ids}  {p.description}")


def check_audio(report: Report, devices: list[AudioDevice]) -> None:
    t = "Audio devices"
    codecs = [d for d in devices if d.name == CODEC_NAME]
    report.add(
        "ok" if codecs else "warn",
        t,
        f"{len(codecs)} device(s) named {CODEC_NAME!r} (radio codecs) of {len(devices)}",
        None if codecs else "Connect the radio by USB; its codec appears as an audio device.",
    )
    for d in codecs:
        report.add("info", t, f"{d.index}: {d.name}  in {d.inputs}  out {d.outputs}  {d.host_api}")


def suggest_radios(report: Report, cfg: Config, ports: list[PortInfo]) -> None:
    """Config snippets for detected radios that no radio entry matches."""
    by_device = {p.device: p for p in ports}
    configured: set[str] = set()
    for rc in cfg.radios:
        with contextlib.suppress(PortNotFound):
            device = resolve_port(rc.match, ports, model_for_driver(rc.driver))
            if device in by_device:
                configured.add(radio_key(by_device[device]))
    seen: set[str] = set()
    for p in sorted(ports, key=lambda p: p.device):
        model = model_of(p)
        if not model or not p.serial_number:
            continue
        key = radio_key(p)
        if key in configured or key in seen:
            continue  # configured already, or the radio's second port
        serial = p.serial_number
        if model == "IC-9700" and not serial.endswith(" A"):
            continue  # IC-9700: port A carries CI-V (Basic Manual 8-16); wait for it
        seen.add(key)
        report.add(
            "info",
            "Not configured yet",
            f"{model} on {p.device}. Add to config.yaml:\n"
            f"    radios:\n"
            f"      - id: {model.lower().replace('-', '')}\n"
            f"        driver: {DRIVER_FOR.get(model, '?')}\n"
            f"        match:\n"
            f'          usb_serial: "{serial}"',
        )


# -- each configured radio -------------------------------------------------------------


TransportFactory = Callable[[str, int], ByteTransport]


def _serial_factory(port: str, baud: int) -> ByteTransport:
    from radio_web_remote.transport.serial_port import SerialTransport

    return SerialTransport(port, baud)


def _errno(exc: BaseException) -> int | None:
    """The OS error number behind an exception, following wrapped causes."""
    seen: BaseException | None = exc
    while seen is not None:
        code = getattr(seen, "errno", None)
        if isinstance(code, int):
            return code
        seen = seen.__cause__
    return None


def _permission_fix(port: str) -> str:
    if sys.platform == "win32":
        return f"{port} is in use or not permitted; close other radio programs."
    import grp

    try:
        group = grp.getgrgid(os.stat(port).st_gid).gr_name
    except (OSError, KeyError):
        return f"Your account cannot open {port}; check its permissions."
    return f"Add your account to the {group!r} group (it owns {port}), then log out and in."


async def check_radio(
    report: Report,
    rc: RadioConfig,
    ports: list[PortInfo],
    devices: list[AudioDevice],
    transport_factory: TransportFactory = _serial_factory,
    listen_audio: Callable[[int], float | None] | None = None,
) -> None:
    t = f"Radio {rc.id!r} ({rc.driver})"
    from radio_web_remote.radios import registry
    from radio_web_remote.radios.base import RadioError

    try:
        port = resolve_port(rc.match, ports, model_for_driver(rc.driver))
    except PortNotFound as exc:
        report.add(
            "fail",
            t,
            str(exc),
            "Turn the radio on and check the USB cable and `match:` in the config.",
        )
        return
    report.add("ok", t, f"serial port {port}")
    if rc.match.port and not (rc.match.usb_serial or rc.match.usb_location):
        found = next((p for p in ports if p.device == port), None)
        if found is not None and found.serial_number:
            report.add(
                "warn",
                t,
                "matched by port name, which can change when the cable moves",
                f'Match by serial number instead: usb_serial: "{found.serial_number}".',
            )
    options = registry.DriverOptions(
        address=rc.civ_address,
        controller_address=rc.controller_address,
        auto_detect_address=True,  # find it even if civ_address is wrong, then say so
    )
    driver = registry.create(rc.driver, transport_factory(port, rc.baud), options)
    expected = rc.civ_address
    try:
        await driver.connect()
        hz = await driver.get_frequency()
        mode = await driver.get_mode()
    except (RadioError, OSError) as exc:
        code = _errno(exc)
        if code in (errno.EACCES, errno.EPERM) or "Access is denied" in str(exc):
            report.add("fail", t, f"cannot open {port}: permission denied", _permission_fix(port))
            return
        if code in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EBUSY) or "exclusively lock" in str(exc):
            report.add(
                "fail",
                t,
                f"{port} is in use by another program",
                "Close the other program using the radio (a running server, logging or "
                "digital-mode software), then run doctor again.",
            )
            return
        report.add(
            "fail",
            t,
            f"no CI-V answer: {exc}",
            f"{rc.id}: check the radio is on, its CI-V Address matches civ_address, "
            "and the CI-V settings in docs/radio-setup.md.",
        )
        return
    finally:
        with contextlib.suppress(Exception):
            await driver.close()
    address = getattr(driver, "address", None)
    report.add("ok", t, f"CI-V answer: {hz / 1e6:.6f} MHz {mode.mode.value} (TX off sent first)")
    default = getattr(getattr(driver, "model", None), "default_address", None)
    wanted = expected if expected is not None else (default.value if default else None)
    if address is not None and wanted is not None and address != wanted:
        report.add(
            "warn",
            t,
            f"the radio answered at CI-V address {address:02X}, not {wanted:02X}",
            f'Set civ_address: "{address:02X}" for {rc.id} (auto_detect_address finds it '
            "at each start, but a wrong address costs a timeout first).",
        )
    if not rc.audio.enabled:
        report.add("info", t, "radio audio disabled in config")
        return
    for direction, ref in (("input", rc.audio.input_device), ("output", rc.audio.output_device)):
        try:
            index = resolve(ref, direction, devices)  # type: ignore[arg-type]
        except AudioDeviceError as exc:
            report.add(
                "fail",
                t,
                f"audio {direction}: {exc}",
                f"Set audio.{direction}_device for {rc.id} by index "
                "(see `radio-web-remote audio devices`).",
            )
            continue
        report.add("ok", t, f"audio {direction}: device {index}")
        if direction == "input" and index is not None and listen_audio is not None:
            peak = listen_audio(index)
            if peak is None:
                report.add("warn", t, "could not record from the radio codec")
            elif peak == 0.0:
                fix = (
                    "macOS: allow Microphone access for the app that runs the server "
                    "(System Settings > Privacy & Security > Microphone); a process without it "
                    "records silence even from the radio's codec (docs/hardware-test.md, H7300-16)."
                    if sys.platform == "darwin"
                    else "Check the radio's USB AF output level and that the codec is not muted."
                )
                report.add("warn", t, "receive audio is pure silence", fix)
            else:
                report.add("ok", t, f"receive audio present (peak {peak:.3f})")


def listen_peak(index: int, seconds: float = 0.5) -> float | None:
    """Peak level (0..1) recorded from an input device, or None on error."""
    try:
        import array

        import sounddevice as sd

        frames = int(48_000 * seconds)
        with sd.RawInputStream(samplerate=48_000, channels=1, dtype="int16", device=index) as st:
            data, _overflow = st.read(frames)
        samples = array.array("h", bytes(data))
        return max((abs(v) for v in samples), default=0) / 32768 if samples else None
    except Exception:
        return None


# -- entry point ---------------------------------------------------------------


async def run(
    cfg_path: str | None,
    data_dir: Path,
    *,
    skip_radio: bool = False,
    ports: list[PortInfo] | None = None,
    devices: list[AudioDevice] | None = None,
    transport_factory: TransportFactory = _serial_factory,
    listen_audio: Callable[[int], float | None] | None = listen_peak,
    config_dir: Path | None = None,
) -> Report:
    """config_dir: the --data-dir the user named, if any; config.yaml is looked
    up exactly as `serve` does."""
    from pydantic import ValidationError

    from radio_web_remote.config import find_config_file, load_config

    report = Report()
    check_host(report)
    source = find_config_file(cfg_path, config_dir)
    cfg: Config | None = None
    error = None
    try:
        cfg = load_config(cfg_path, config_dir)
    except (ValidationError, ValueError, OSError) as exc:
        error = str(exc)
    check_config(report, cfg, source, error)
    if cfg is None:
        return report
    check_data(report, cfg, data_dir)
    port_free = check_listen_port(report, cfg)
    if ports is None:
        from radio_web_remote.devices.serial_ports import list_serial_ports

        ports = list_serial_ports()
    check_ports(report, ports)
    if devices is None:
        try:
            from radio_web_remote.audio.devices import list_devices

            devices = list_devices()
        except Exception as exc:  # PortAudio missing or broken
            report.add(
                "fail",
                "Audio devices",
                f"cannot list audio devices: {exc}",
                "Reinstall; PortAudio comes with the sounddevice package.",
            )
            devices = []
    check_audio(report, devices)
    for rc in cfg.radios:
        if skip_radio or not port_free:
            report.add("info", f"Radio {rc.id!r} ({rc.driver})", "radio check skipped")
            continue
        await check_radio(report, rc, ports, devices, transport_factory, listen_audio)
    suggest_radios(report, cfg, ports)
    return report


def main(
    cfg_path: str | None, data_dir: Path, skip_radio: bool, config_dir: Path | None = None
) -> int:
    report = asyncio.run(run(cfg_path, data_dir, skip_radio=skip_radio, config_dir=config_dir))
    print(report.render())
    return 1 if report.failed else 0


__all__ = ["Finding", "Report", "check_radio", "main", "model_of", "run"]
