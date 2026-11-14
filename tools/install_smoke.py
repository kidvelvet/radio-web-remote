"""Smoke-test an installed radio-web-remote: init, serve with the simulator, /setup, doctor.

Usage: python tools/install_smoke.py <path to the installed radio-web-remote command>
Used by CI after running packaging/install.sh or install.ps1. Uses only the
standard library, so it runs with any Python 3.11+.
"""

from __future__ import annotations

import os
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main(exe: str) -> int:
    home = Path(tempfile.mkdtemp(prefix="rwr-smoke-"))
    env = {**os.environ, "RADIO_WEB_REMOTE_HOME": str(home)}
    port = free_port()
    cfg = home / "config.yaml"
    cfg.write_text(f"server:\n  bind_address: 127.0.0.1\n  https_port: {port}\n")
    run = lambda *a, **k: subprocess.run([exe, *a], env=env, text=True, **k)  # noqa: E731
    print(run("--version", capture_output=True, check=True).stdout.strip())
    drivers = run("drivers", capture_output=True, check=True).stdout.split()
    assert {"icom.ic7300", "icom.ic705", "icom.ic9700"} <= set(drivers), drivers
    pw = secrets.token_urlsafe(16) + "\n"
    run(
        "init",
        "--username",
        "admin",
        "--callsign",
        "N0TST",
        "--password-stdin",
        input=pw,
        capture_output=True,
        check=True,
    )
    srv = subprocess.Popen(
        [exe, "serve", "--simulate"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    ctx = ssl.create_default_context(cafile=str(home / "tls" / "ca.crt"))
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(f"https://localhost:{port}/setup", context=ctx) as r:
                    assert r.status == 200 and b"setup" in r.read().lower()
                    break
            except OSError:
                if srv.poll() is not None or time.monotonic() > deadline:
                    out = srv.stdout.read() if srv.stdout else ""
                    raise SystemExit(f"server did not come up:\n{out}") from None
                time.sleep(0.5)
        print(f"/setup served over TLS with the local CA on port {port}")
    finally:
        srv.terminate()
        try:
            srv.wait(15)
        except subprocess.TimeoutExpired:
            srv.kill()
    doc = run("doctor", "--skip-radio", capture_output=True)
    print(doc.stdout)
    if doc.returncode != 0:
        print(doc.stderr, file=sys.stderr)
        return doc.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
