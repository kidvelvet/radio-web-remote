"""Make the README screenshots from the simulator (no radio needed).

Usage: .tools/bin/uv run python tools/screenshots.py   (writes docs/screenshots/*.png)
"""

from __future__ import annotations

import contextlib
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> None:
    d = Path(tempfile.mkdtemp(prefix="rwr-shots-"))
    port = free_port()
    cfg = d / "config.yaml"
    cfg.write_text(
        f"server:\n  bind_address: 127.0.0.1\n  https_port: {port}\n"
        "radios:\n  - id: ic7300\n    driver: icom.ic7300\n"
        "  - id: ic9700\n    driver: icom.ic9700\n"
    )
    pw = secrets.token_urlsafe(16)
    base = [sys.executable, "-m", "radio_web_remote"]
    common = ["--config", str(cfg), "--data-dir", str(d)]
    subprocess.run(
        [*base, "init", *common, "--username", "demo", "--callsign", "N0CALL", "--password-stdin"],
        input=pw + "\n",
        text=True,
        check=True,
        capture_output=True,
    )
    srv = subprocess.Popen([*base, "serve", "--simulate", *common], stdout=subprocess.DEVNULL)
    try:
        for _ in range(300):
            with contextlib.suppress(OSError):
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            time.sleep(0.1)
        OUT.mkdir(parents=True, exist_ok=True)
        url = f"https://localhost:{port}"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for name, size, radio, scale in (
                ("desktop", {"width": 1280, "height": 900}, "ic7300", 1),
                ("phone", {"width": 390, "height": 844}, "ic7300", 2),
                ("ic9700-phone", {"width": 390, "height": 844}, "ic9700", 2),
            ):
                ctx = browser.new_context(
                    ignore_https_errors=True, viewport=size, device_scale_factor=scale
                )
                page = ctx.new_page()
                page.goto(f"{url}/login")
                page.fill("#username", "demo")
                page.fill("#password", pw)
                page.click("#go")
                expect(page.locator("#link")).to_have_text("Connected", timeout=15_000)
                page.goto(f"{url}/?radio={radio}")
                expect(page.locator("#link")).to_have_text("Connected", timeout=15_000)
                if radio == "ic7300":
                    page.click("#bands button:has-text('20m')")
                page.click("#scope-toggle")
                page.wait_for_timeout(4000)  # let the waterfall fill
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
                print("wrote", OUT / f"{name}.png")
                ctx.close()
            browser.close()
    finally:
        srv.terminate()
        with contextlib.suppress(Exception):
            srv.wait(10)


if __name__ == "__main__":
    main()
