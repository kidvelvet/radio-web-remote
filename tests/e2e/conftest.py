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
        "radios:\n"
        "  - id: sim-ic7300\n    driver: icom.ic7300\n"
        "  - id: sim-ic705\n    driver: icom.ic705\n"
        "  - id: sim-ic9700\n    driver: icom.ic9700\n"
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
    _cli(
        "user",
        *common,
        "add",
        "twenty",
        "--role",
        "operator",
        "--callsign",
        "N0TW",
        "--password-stdin",
        stdin=pw + "\n",
    )
    _cli("user", *common, "limits", "twenty", "--bands", "14000000-14350000")
    # To a file, not a pipe: nothing reads a pipe while the tests run, and a
    # full one (64 KB) blocks the server's event loop on its next log line.
    log = open(d / "server.log", "w")  # noqa: SIM115  (open for the whole session)
    proc = subprocess.Popen(
        [sys.executable, "-m", "radio_web_remote", "serve", "--simulate", *common],
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline, started = time.monotonic() + 30, False
    while not started and proc.poll() is None and time.monotonic() < deadline:
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
            started = True
        time.sleep(0.1)
    if not started:
        proc.kill()
        proc.wait(10)
        log.close()
        raise RuntimeError("server did not start:\n" + (d / "server.log").read_text())
    yield {"url": f"https://localhost:{port}", "password": pw, "dir": d}
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


@pytest.fixture
def browser_context_args(browser_context_args, server):
    return {**browser_context_args, "base_url": server["url"], "ignore_https_errors": True}


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, browser_name):
    """Fake microphones: Chromium plays a beep, Firefox a tone. WebKit has no fake mic."""
    args = dict(browser_type_launch_args)
    if browser_name == "chromium":
        args["args"] = [
            *args.get("args", []),
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
        ]
    elif browser_name == "firefox":
        args["firefox_user_prefs"] = {
            "media.navigator.streams.fake": True,
            "media.navigator.permission.disabled": True,
            "media.autoplay.default": 0,
        }
    return args
