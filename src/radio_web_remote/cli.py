"""Command line entry point.

P0 commands:
  config check   validate a config file and print the effective settings
  drivers        list registered radio drivers
  sim            run a simulated radio on a pseudo-terminal (Linux, macOS)
  probe          read frequency, mode and TX state from a radio (sends TX off first)
  doctor         check this computer, the config and each radio; print what to fix
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from radio_web_remote import __version__
from radio_web_remote.config import find_config_file, load_config


def _cmd_config_check(args: argparse.Namespace) -> int:
    path = find_config_file(args.config)
    try:
        cfg = load_config(args.config)
    except (ValidationError, ValueError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    from radio_web_remote.radios import registry

    known = set(registry.names())
    bad = [r for r in cfg.radios if r.driver not in known]
    for r in bad:
        print(f"config error: radio {r.id!r}: unknown driver {r.driver!r}", file=sys.stderr)
    print(f"# source: {path or 'built-in defaults'}")
    print(json.dumps(cfg.model_dump(mode="json"), indent=2))
    return 1 if bad else 0


def _cmd_drivers(_: argparse.Namespace) -> int:
    from radio_web_remote.radios import registry

    for name in registry.names():
        print(name)
    return 0


def _cmd_sim(args: argparse.Namespace) -> int:
    from radio_web_remote.radios.icom import MODELS
    from radio_web_remote.sim.icom import IcomSimulator, PtyRunner

    model = MODELS.get(args.driver)
    if model is None:
        print(f"no simulator for {args.driver!r}", file=sys.stderr)
        return 1
    sim = IcomSimulator(model, echo_back=args.echo_back)
    try:
        runner = PtyRunner(sim)
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    with runner:
        print(f"Simulated {model.name} at CI-V address {sim.addr:02X}h on {runner.path}")
        print("Ctrl-C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return 0


async def _probe(args: argparse.Namespace) -> int:
    from radio_web_remote.radios import registry
    from radio_web_remote.radios.base import RadioError
    from radio_web_remote.transport.serial_port import SerialTransport

    transport = SerialTransport(args.port, args.baud)
    opts = registry.DriverOptions(address=int(args.address, 16) if args.address else None)
    driver = registry.create(args.driver, transport, opts)
    try:
        await driver.connect()
        hz = await driver.get_frequency()
        mode = await driver.get_mode()
        tx = await driver.get_ptt()
    except (RadioError, OSError) as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await driver.close()
    flt = f" FIL{mode.filter}" if mode.filter else ""
    print(
        f"{driver.model_name}: {hz / 1e6:.6f} MHz  {mode.mode.value}{flt}  {'TX' if tx else 'RX'}"
    )
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    return asyncio.run(_probe(args))


# -- accounts, certificates, server ----------------------------------------


def _data_dir(args: argparse.Namespace) -> Path:
    from radio_web_remote import paths

    return Path(args.data_dir) if args.data_dir else paths.data_dir()


def _read_password(args: argparse.Namespace, prompt: str = "Password") -> str:
    if getattr(args, "password_stdin", False):
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass(f"{prompt}: ")
    if getpass.getpass(f"{prompt} again: ") != first:
        raise SystemExit("passwords do not match")
    return first


def _store(args: argparse.Namespace) -> Any:
    from radio_web_remote.security.users import UserStore
    from radio_web_remote.server import users_db

    return UserStore(users_db(_data_dir(args)))


def _cmd_init(args: argparse.Namespace) -> int:
    from radio_web_remote.security.tls import ensure_certificates, wanted_sans
    from radio_web_remote.security.users import UserError
    from radio_web_remote.station.tx_controller import Role

    cfg = load_config(args.config)
    if args.password_stdin and not args.username:
        print("error: --password-stdin needs --username", file=sys.stderr)
        return 1
    store = _store(args)
    if store.count_admins() == 0:
        username = args.username or input("Admin username: ").strip()
        callsign = (
            args.callsign if args.callsign is not None else input("Callsign (blank if none): ")
        )
        try:
            store.create_user(username, _read_password(args), Role.ADMIN, callsign)
        except UserError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Created admin {username!r}.")
    else:
        print("An admin account already exists; leaving accounts alone.")
    _files, changed = ensure_certificates(cfg, _data_dir(args))
    print(f"TLS: {changed or 'certificates already in place'}")
    print(f"Data folder: {_data_dir(args)}")
    port = cfg.server.https_port
    names = wanted_sans(cfg)
    print("Clients can reach this server at, for example:")
    for name in (
        sorted(names.dns - {"localhost"})[:3] + [str(i) for i in sorted(names.ips, key=str)][:4]
    ):
        host = f"[{name}]" if ":" in name else name
        print(f"  https://{host}:{port}/   (first time on a device: https://{host}:{port}/setup)")
    return 0


def _last_admin(store: Any, name: str) -> bool:
    """True if `name` is the only enabled admin (count_admins counts enabled ones)."""
    from radio_web_remote.station.tx_controller import Role

    user = store.get_user(name)
    return bool(
        user and user.role is Role.ADMIN and not user.disabled and store.count_admins() <= 1
    )


def _cmd_user(args: argparse.Namespace) -> int:
    from radio_web_remote.security.users import UserError
    from radio_web_remote.station.tx_controller import Role, TxLimits

    store = _store(args)
    try:
        if args.user_command == "list":
            for u in store.list_users():
                flags = " (disabled)" if u.disabled else ""
                bands = (
                    "any band" if u.limits.bands_hz is None else f"{len(u.limits.bands_hz)} band(s)"
                )
                print(f"{u.username:16} {u.role.value:9} {u.callsign or '-':10} {bands}{flags}")
        elif args.user_command == "add":
            store.create_user(args.name, _read_password(args), Role(args.role), args.callsign or "")
            print(f"Added {args.name}.")
        elif args.user_command == "passwd":
            store.set_password(args.name, _read_password(args, "New password"))
            print("Password changed; that user's sessions were ended.")
        elif args.user_command == "role":
            if Role(args.role) is not Role.ADMIN and _last_admin(store, args.name):
                raise UserError("refusing to demote the last admin")
            store.set_role(args.name, Role(args.role), args.callsign)
            print("Role changed.")
        elif args.user_command in ("disable", "enable"):
            if args.user_command == "disable" and _last_admin(store, args.name):
                raise UserError("refusing to disable the last admin")
            store.set_disabled(args.name, args.user_command == "disable")
            print(f"{args.name} {args.user_command}d.")
        elif args.user_command == "unlock":
            store.unlock(args.name)
            print("Unlocked.")
        elif args.user_command == "delete":
            if _last_admin(store, args.name):
                raise UserError("refusing to delete the last admin")
            store.delete_user(args.name)
            print("Deleted.")
        elif args.user_command == "limits":
            ranges: tuple[tuple[int, int], ...] | None = None
            if args.bands:
                ranges = tuple(
                    (int(lo), int(hi)) for lo, hi in (b.split("-") for b in args.bands.split(","))
                )
            store.set_limits(args.name, TxLimits(bands_hz=ranges))
            print("Limits set." if ranges else "Band limits cleared.")
    except (UserError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_audio_devices(_: argparse.Namespace) -> int:
    from radio_web_remote.audio.devices import CODEC_NAME, list_devices

    for d in list_devices():
        tag = "  <- radio codec?" if d.name == CODEC_NAME else ""
        print(f"{d.index:3}  in {d.inputs}  out {d.outputs}  {d.host_api:12}  {d.name}{tag}")
    return 0


def _cmd_cert(args: argparse.Namespace) -> int:
    from radio_web_remote.security.tls import cert_sans, ensure_certificates, tls_dir

    cfg = load_config(args.config)
    d = tls_dir(_data_dir(args))
    if args.cert_command == "reset":
        if not args.yes:
            print("This makes a NEW local CA. Every device must install it again from /setup.")
            if input("Type RESET to continue: ").strip() != "RESET":
                return 1
        for name in ("ca.crt", "ca.key", "server.crt", "server.key"):
            (d / name).unlink(missing_ok=True)
    files, changed = ensure_certificates(cfg, _data_dir(args))
    print(f"TLS: {changed or 'no change'}")
    print("Server certificate names:", cert_sans(files.cert_file).describe())
    if changed:
        print("Restart the server to use the new certificate.")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from radio_web_remote import doctor

    return doctor.main(args.config, _data_dir(args), args.skip_radio)


def _cmd_serve(args: argparse.Namespace) -> int:
    import logging

    from radio_web_remote.server import NoAdmin, serve

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        serve(load_config(args.config), _data_dir(args), simulate=args.simulate)
    except NoAdmin as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="radio-web-remote")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    cfg = sub.add_parser("config", help="configuration tools")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    check = cfg_sub.add_parser("check", help="validate config and print effective settings")
    check.add_argument("--config", help="path to config.yaml")
    check.set_defaults(func=_cmd_config_check)

    drv = sub.add_parser("drivers", help="list radio drivers")
    drv.set_defaults(func=_cmd_drivers)

    sim = sub.add_parser("sim", help="simulated radio on a pseudo-terminal (Linux, macOS)")
    sim.add_argument("--driver", default="icom.ic7300")
    sim.add_argument(
        "--echo-back", action="store_true", help="echo received frames, like USB Echo Back ON"
    )
    sim.set_defaults(func=_cmd_sim)

    probe = sub.add_parser("probe", help="read frequency/mode/TX state (sends TX off first)")
    probe.add_argument(
        "--port", required=True, help="serial port, e.g. /dev/cu.SLAB_USBtoUART or COM3"
    )
    probe.add_argument("--driver", default="icom.ic7300")
    probe.add_argument("--baud", type=int, default=115200)
    probe.add_argument("--address", help="CI-V address in hex, e.g. 94 (default: driver default)")
    probe.set_defaults(func=_cmd_probe)

    def common(sp: argparse.ArgumentParser, sub_level: bool = False) -> None:
        # On a sub-subcommand, SUPPRESS keeps a value given before it.
        kw: dict[str, Any] = {"default": argparse.SUPPRESS} if sub_level else {}
        sp.add_argument("--config", help="path to config.yaml", **kw)
        sp.add_argument("--data-dir", help="state folder (default: per-OS app data folder)", **kw)

    init = sub.add_parser("init", help="first run: create the admin account and certificates")
    common(init)
    init.add_argument("--username")
    init.add_argument("--callsign")
    init.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    init.set_defaults(func=_cmd_init)

    user = sub.add_parser("user", help="manage accounts")
    common(user)
    us = user.add_subparsers(dest="user_command", required=True)
    us.add_parser("list")
    add = us.add_parser("add")
    add.add_argument("name")
    add.add_argument("--role", choices=["admin", "operator", "listener"], default="operator")
    add.add_argument("--callsign")
    add.add_argument("--password-stdin", action="store_true")
    pw = us.add_parser("passwd")
    pw.add_argument("name")
    pw.add_argument("--password-stdin", action="store_true")
    role = us.add_parser("role")
    role.add_argument("name")
    role.add_argument("role", choices=["admin", "operator", "listener"])
    role.add_argument("--callsign")
    for verb in ("disable", "enable", "unlock", "delete"):
        us.add_parser(verb).add_argument("name")
    lim = us.add_parser(
        "limits", help="restrict TX to bands, in Hz: 14000000-14350000,7000000-7300000"
    )
    lim.add_argument("name")
    lim.add_argument("--bands", help="comma-separated low-high ranges in Hz; omit to clear")
    for sp in us.choices.values():
        common(sp, sub_level=True)
    user.set_defaults(func=_cmd_user)

    cert = sub.add_parser("cert", help="certificates")
    common(cert)
    cs = cert.add_subparsers(dest="cert_command", required=True)
    cs.add_parser("show", help="renew if needed and list the server certificate's names")
    reset = cs.add_parser("reset", help="make a new local CA (devices must reinstall it)")
    reset.add_argument("--yes", action="store_true", help="do not ask")
    for sp in cs.choices.values():
        common(sp, sub_level=True)
    cert.set_defaults(func=_cmd_cert)

    aud = sub.add_parser("audio", help="audio tools")
    aud_sub = aud.add_subparsers(dest="audio_command", required=True)
    aud_sub.add_parser("devices", help="list audio devices and their indexes").set_defaults(
        func=_cmd_audio_devices
    )

    doc = sub.add_parser(
        "doctor", help="check ports, audio, config and each radio; print what to fix"
    )
    common(doc)
    doc.add_argument("--skip-radio", action="store_true", help="do not talk to the radios")
    doc.set_defaults(func=_cmd_doctor)

    srv = sub.add_parser("serve", help="run the HTTPS server")
    common(srv)
    srv.add_argument("--simulate", action="store_true", help="use simulated radios (no hardware)")
    srv.set_defaults(func=_cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except (ValidationError, ValueError) as exc:  # config errors, bad values
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except EOFError:
        print("error: no terminal to ask on; use --username and --password-stdin", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return result
