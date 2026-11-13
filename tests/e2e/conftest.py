"""Run the real server (simulated radio) in a subprocess for browser tests.

Certificate checks are relaxed only inside these test browser contexts: the
server's local CA is generated fresh for each run and installing it into
three browser engines is out of scope for a test. Real clients install the
CA from /setup.
"""

import contextlib
import secrets
import socket
import subprocess
import sys
import time

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cli(*args: str, stdin: str | None = None) -> None:
    subprocess.run(
        [sys.executable, "-m", "radio_web_remote", *args],
        input=stdin,
        text=True,
        check=True,
        capture_output=True,
        timeout=120,
    )


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("rwr-e2e")
    port = _free_port()
    cfg = d / "config.yaml"
    cfg.write_text(
        f"server:\n  bind_address: 127.0.0.1\n  https_port: {port}\n"
        "ptt:\n  heartbeat_interval_s: 0.25\n  heartbeat_timeout_s: 1.0\n"
    )
    pw = secrets.token_urlsafe(16)
    common = ["--config", str(cfg), "--data-dir", str(d)]
    _cli(
        "init",
        *common,
        "--username",
        "admin",
        "--callsign",
        "N0ADM",
        "--password-stdin",
        stdin=pw + "\n",
    )
    _cli(
        "user",
        *common,
        "add",
        "op",
        "--role",
        "operator",
        "--callsign",
        "N0OP",
        "--password-stdin",
        stdin=pw + "\n",
    )
    _cli("user", *common, "add", "lee", "--role", "listener", "--password-stdin", stdin=pw + "\n")
    proc = subprocess.Popen(
        [sys.executable, "-m", "radio_web_remote", "serve", "--simulate", *common],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
            break
        if proc.poll() is not None:
            raise RuntimeError(proc.stdout.read())
        time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("server did not start")
    yield {"url": f"https://localhost:{port}", "password": pw, "dir": d}
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def browser_context_args(browser_context_args, server):
    return {**browser_context_args, "base_url": server["url"], "ignore_https_errors": True}
