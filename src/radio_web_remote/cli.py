"""Command line entry point.

P0 commands:
  config check   validate a config file and print the effective settings
  drivers        list registered radio drivers
  sim            run a simulated radio on a pseudo-terminal (Linux, macOS)
  probe          read frequency, mode and TX state from a radio (sends TX off first)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
