"""Audio round-trip latency and server CPU for the WebSocket transport (ADR 0002).

Runs the real server with a simulated radio whose receive audio echoes its
transmit audio (like the radio's TX monitor). A synthetic microphone emits
an 80 ms 1 kHz burst while PTT is held; the time until the burst comes out of
the page's own audio output is the round trip:

  browser mic -> transport -> server -> radio (simulated) -> server -> transport -> browser output

Usage: .tools/bin/uv run python tools/bench_audio.py [--browsers chromium,firefox,webkit] [--n 15]
The WebRTC rows of ADR 0002 came from the same tool on branch spike/webrtc.
"""

from __future__ import annotations

import argparse
import contextlib
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

INIT = """
(() => {
  const fake = async () => {
    const ctx = new AudioContext();
    await ctx.resume();
    const osc = new OscillatorNode(ctx, { frequency: 1000 });
    const g = new GainNode(ctx, { gain: 0 });
    const dst = new MediaStreamAudioDestinationNode(ctx);
    osc.connect(g).connect(dst);
    osc.start();
    window.__burst = () => {
      const t = ctx.currentTime;
      g.gain.setValueAtTime(0.8, t);
      g.gain.setValueAtTime(0, t + 0.08);
      return performance.now();
    };
    return dst.stream;
  };
  // WebKit ignores plain assignment; define it on the prototype as well.
  try {
    const desc = { value: fake, configurable: true };
    Object.defineProperty(MediaDevices.prototype, "getUserMedia", desc);
  } catch (e) {}
  try { navigator.mediaDevices.getUserMedia = fake; } catch (e) {}
  window.__measure = () => new Promise((resolve) => {
    const an = new AnalyserNode(window.rwrBench.ctx, { fftSize: 256 });
    window.rwrBench.out.connect(an);
    const buf = new Float32Array(256);
    const t0 = window.__burst();
    const iv = setInterval(() => {
      an.getFloatTimeDomainData(buf);
      let m = 0;
      for (const v of buf) m = Math.max(m, Math.abs(v));
      const dt = performance.now() - t0;
      if (m > 0.4 || dt > 3000) {
        clearInterval(iv); an.disconnect(); resolve(m > 0.4 ? dt : null);
      }
    }, 2);
  });
})();
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def cli(d: Path, cfg: Path, *args: str, stdin: str = "") -> None:
    cmd = [
        sys.executable,
        "-m",
        "radio_web_remote",
        args[0],
        "--config",
        str(cfg),
        "--data-dir",
        str(d),
    ]
    subprocess.run([*cmd, *args[1:]], input=stdin, text=True, check=True, capture_output=True)


def cpu_seconds(pid: int) -> float:
    out = subprocess.run(
        ["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    parts = [float(x) for x in out.replace("-", ":").split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def run(browsers: list[str], n: int) -> None:
    d = Path(tempfile.mkdtemp(prefix="rwr-bench-"))
    port = free_port()
    cfg = d / "config.yaml"
    cfg.write_text(f"server:\n  bind_address: 127.0.0.1\n  https_port: {port}\n")
    pw = secrets.token_urlsafe(16)
    cli(
        d,
        cfg,
        "init",
        "--username",
        "bench",
        "--callsign",
        "N0BEN",
        "--password-stdin",
        stdin=pw + "\n",
    )
    srv = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "radio_web_remote",
            "serve",
            "--simulate",
            "--config",
            str(cfg),
            "--data-dir",
            str(d),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    rows = []
    try:
        for _ in range(300):
            with contextlib.suppress(OSError):
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            time.sleep(0.1)
        url = f"https://localhost:{port}"
        with sync_playwright() as p:
            for bname in browsers:
                bt = getattr(p, bname)
                args = (
                    {"args": ["--autoplay-policy=no-user-gesture-required"]}
                    if bname == "chromium"
                    else {}
                )
                if bname == "firefox":
                    args = {
                        "firefox_user_prefs": {
                            "media.autoplay.default": 0,
                            "media.navigator.permission.disabled": True,
                        }
                    }
                browser = bt.launch(**args)
                for transport in ("websocket",):
                    page = browser.new_context(ignore_https_errors=True).new_page()
                    page.add_init_script(INIT)
                    q = "?bench=1" + ("&transport=webrtc" if transport == "webrtc" else "")
                    page.goto(f"{url}/login")
                    page.fill("#username", "bench")
                    page.fill("#password", pw)
                    page.click("#go")
                    expect(page.locator("#link")).to_have_text("Connected", timeout=15_000)
                    page.goto(f"{url}/{q}")
                    expect(page.locator("#link")).to_have_text("Connected", timeout=15_000)
                    page.select_option("#au-mode", "remote")
                    page.click("#au-start")
                    try:
                        expect(page.locator("#au-start")).to_have_text("Stop audio", timeout=15_000)
                    except AssertionError:
                        rows.append(
                            (
                                bname,
                                transport,
                                None,
                                None,
                                None,
                                page.locator("#au-status").inner_text(),
                            )
                        )
                        page.context.close()
                        continue
                    page.check("#toggle")
                    page.click("#ptt")
                    expect(page.locator("#txtext")).to_have_text(
                        __import__("re").compile("ON AIR"), timeout=8_000
                    )
                    time.sleep(1.5)
                    c0, t0 = cpu_seconds(srv.pid), time.monotonic()
                    lat = []
                    for _ in range(n):
                        v = page.evaluate("window.__measure()")
                        if v is not None:
                            lat.append(v)
                        time.sleep(0.6)
                    cpu = (cpu_seconds(srv.pid) - c0) / (time.monotonic() - t0) * 100
                    page.click("#ptt")
                    page.context.close()
                    med = statistics.median(lat) if lat else None
                    p90 = sorted(lat)[int(len(lat) * 0.9) - 1] if len(lat) >= 5 else None
                    rows.append(
                        (bname, transport, med, p90, cpu, f"{len(lat)}/{n} bursts detected")
                    )
                browser.close()
    finally:
        srv.terminate()
        with contextlib.suppress(Exception):
            srv.wait(10)
    head = f"{'browser':10} {'transport':10} {'median ms':>10} {'p90 ms':>8} {'server CPU %':>13}"
    print(head + "  note")
    for b, t, med, p90, cpu, note in rows:
        f = lambda v, w: f"{v:{w}.0f}" if v is not None else " " * (w - 1) + "-"  # noqa: E731
        print(f"{b:10} {t:10} {f(med, 10)} {f(p90, 8)} {f(cpu, 13)}  {note}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--browsers", default="chromium,firefox,webkit")
    ap.add_argument("--n", type=int, default=15)
    a = ap.parse_args()
    run(a.browsers.split(","), a.n)
