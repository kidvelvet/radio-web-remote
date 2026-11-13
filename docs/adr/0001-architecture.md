# ADR 0001: Architecture for radio-web-remote v1

- Status: Proposed (awaiting approval)
- Date: 2026-11-13
- Scope: v1 (IC-7300, IC-705, IC-9700 over USB only)

## Context

A server on the computer cabled to the radio speaks CI-V over the radio's USB virtual serial port, moves audio through the radio's USB audio codec, and serves a browser UI over HTTPS on a non-standard port (default 8473). Clients are on the LAN or reach it through a VPN. The LAN is treated as untrusted. iOS Safari must get full function, including mic transmit. Safety of the transmitter is the first priority, then correctness, then features.

## Decisions

### D1. Server runtime: Python 3.11+, asyncio, FastAPI on Uvicorn

Agree with the brief. FastAPI gives typed request/response models (pydantic) and WebSocket support on Starlette; Uvicorn is the ASGI server and does TLS natively, so there is no reverse proxy to install. 3.11 is the floor because Raspberry Pi OS Bookworm ships 3.11.

Rejected: Node (second toolchain for members; the maintainer's Mac has no Node), Go (fewer contributors in the club, weaker audio libraries).

### D2. Serial I/O: pyserial, one reader thread per port, bridged into asyncio

Small departure from "pyserial with an asyncio wrapper". `pyserial-asyncio` relies on event-loop file descriptor readiness, which does not work for serial handles on Windows' default Proactor loop. Instead each radio gets one dedicated reader thread doing blocking `read()` with a short timeout, pushing bytes to the loop with `call_soon_threadsafe`. Writes go through one async writer coroutine and a priority queue. The public interface is fully async; only the transport is threaded. This behaves the same on Linux, macOS and Windows.

The CI-V layer above the transport:

- Frame parser: byte stream to frames `FE FE <to> <from> <cmd> [sub] [data] FD`; discards junk, handles `FC` collision/jam bytes, drops our own echoes when USB Echo Back is on.
- Command queue with priority. "TX off" is highest priority, bypasses the queue, and is written directly by the TX controller (D6).
- Request/response matching with timeouts; `FB` (OK) / `FA` (NG) handling.

### D3. Radio abstraction: vendor-neutral core, protocol plugins, data-driven capability tables

The project is named radio-web-remote because radios from other makers may follow. Nothing above the driver layer knows about CI-V.

Three layers:

1. **Core model** (`radios/base.py`): vendor-neutral types (`Frequency` in Hz, `Mode`, `Level` normalized 0.0 to 1.0 plus the radio's native units, `Meter`, `ScopeLine`) and the `RadioDriver` interface (set/get frequency, mode, levels, PTT, scope control, meter reads, capability query). The web layer, `TxController`, poller and UI only use these.
2. **Protocol** (`protocols/civ/`): framing, encodings and transport for one wire protocol. A future Yaesu CAT or Kenwood protocol is a sibling package.
3. **Model** (`radios/icom/ic7300.py` etc.): a capability table plus small model-specific code. Each table entry lists which controls exist, their command bytes, data encoding, valid range, and a required `source` field (document title and page). A unit test fails the build if any command entry lacks a source.

Drivers register through a name-keyed registry (later via Python entry points, so a third-party package can add a radio without forking). Config names a radio by `driver: icom.ic7300`. The UI renders only the controls a driver's capability table declares, so a radio without a scope or without a DATA mode just shows less. Adding the IC-7610 or IC-905 is a new table, fixtures and tests; adding another maker is a new protocol package plus tables.

No command is copied from one model's table to another. Where a model's documentation has not been checked, the control is simply absent from that table.

### D4. Radio state sync: CI-V Transceive plus slow polling

Transceive ON gives unsolicited frequency and mode changes from the front panel. Other state (AF/RF gain, preamp, filter, etc.) is not guaranteed to be broadcast, so a round-robin poller reads non-transceive state at a low rate (about 1 Hz total) and meters at 5 to 10 Hz. Polling pauses while a write to the same control is in flight, to avoid UI flicker. TODO(verify): exactly which items each model broadcasts with Transceive ON.

### D5. Process model

One server process manages one or more radios. Per radio there is a `RadioSession` owning: the serial transport, the driver, the host audio streams, and its `TxController`. Clients connect to a radio by id. Radios are identified by USB serial number or USB topology path, never by COM number or `/dev/ttyUSBn`.

### D6. Transmit safety: a single server-side `TxController` state machine

Every path to transmit goes through `TxController` for that radio. It enforces:

1. One holder at a time (lock owned by a session id; others see "TX held by CALLSIGN").
2. Client heartbeat every 500 ms on the control WSS; unkey if none for `ptt.heartbeat_timeout` (default 2 s).
3. Unkey on WSS close, on client-reported `visibilitychange`/`pagehide`, on PTT release, and on loss of the holder's session (logout, admin disable).
4. TX timeout timer, default and maximum 180 s; config may only lower it. Releasing and re-pressing PTT starts a new timer.
5. Per-user band and power limits checked before keying and on any frequency or power change while keyed (a change out of limits unkeys).
6. On server start and on every radio (re)connect, send TX off before any other command.

The client never decides to unkey on its own behalf as the only path. Client-side unkey on hide is a fast path; the heartbeat timeout is the guarantee.

**Regulatory basis.** 47 CFR 97.213(b) as published in mirrors of the CFR reads: "Provisions are incorporated to limit transmission by the station to a period of no more than 3 minutes in the event of malfunction in the control link." Sources so far: [Cornell LII 47 CFR 97.213](https://www.law.cornell.edu/cfr/text/47/97.213), [hallikainen.org 2020 edition](https://hallikainen.org/org/FCC/FccRules/2020/97/213). TODO(verify): confirm current wording and section heading (mirrors show "Telecommand of an amateur station") on eCFR before docs ship.

The rule is about control link malfunction, which the 2 s heartbeat timeout already covers. The 180 s cap is therefore not strictly required for normal operation. I still recommend keeping it as a hard ceiling, because it is the only layer that catches failures where heartbeats keep arriving but no one is in control (stuck spacebar, toggle-mode PTT left on in an open tab, a client bug). Cost: transmissions longer than 3 minutes need a re-key. Admins can lower it, not raise or disable it.

**Residual risk: the server process itself dies while keyed.** With CI-V PTT, the radio stays in TX until something sends TX off. Confirmed on an IC-7300 on 2026-11-13: the key survived the process dying and the port closing; the next connect's TX off cleared it within 67 ms (docs/hardware-test.md, H7300-4). Mitigations, in order:

- The service manager (systemd, launchd, Task Scheduler) restarts the server, and startup sends TX off first (item 6). Target: unkey within seconds of a crash.
- DTR/RTS PTT ("USB SEND" set to DTR or RTS) has the opposite failure mode: the OS deasserts the line when the port handle closes, including on process death. Opening the port never keys (H7300-3, macOS); whether the line drops on process death is TODO(verify) per OS and on hardware. Docs will explain this trade-off; CI-V remains the default as briefed.
- The radio's own transmit time-out timer, where a model has one. TODO(verify) per model; if present, `docs/radio-setup.md` will recommend enabling it.
- Unplugging the USB cable while keyed: tested 2026-11-13 (H7300-7). The IC-7300 **stays in TX**; the server cannot reach it until the cable returns (about 43 s in the test). Mitigations added: every client shows "TX off not confirmed" while it lasts, and the audit log records when it started and when RX was finally confirmed. The radio's Time-Out Timer (CI-V) is the backstop that bounds it; docs/radio-setup.md recommends 3 min.

### D7. Control channel: one authenticated WSS per client

JSON messages for control and state, binary frames for scope lines. Message schema is versioned (`v` field). Authentication uses the session cookie on the upgrade request plus a strict `Origin` check against the configured origins.

### D8. Audio transport to the browser: build behind an interface, default to PCM over WSS, decide with a measured spike in P3

This is where I disagree with the starting recommendation, at least as a default.

WebRTC with aiortc (current release found: 1.15.0, July 2026, on a PyPI mirror; TODO(verify) on pypi.org) gives Opus, jitter buffering and loss concealment. Its costs for this project:

- Media rides on UDP ports negotiated by ICE, separate from the single HTTPS port. That breaks the "one non-standard port" model for firewalls and makes VPN reachability depend on ICE succeeding. TODO(verify): whether aiortc/aioice can be pinned to a fixed UDP port or range.
- Browsers hide host candidates behind mDNS names when no mic permission is granted (listener role). Over a VPN, mDNS names do not resolve. Peer-reflexive candidates usually rescue this, but it is a failure mode to test, not assume.
- Native dependencies (PyAV/FFmpeg, libopus, libvpx) make installs on Pi and Windows heavier.

PCM over the existing WSS, played and captured with an AudioWorklet:

- Same port, same TLS, same auth, works through any VPN that carries HTTPS.
- Works in iOS Safari (AudioWorklet and getUserMedia in a secure context).
- Costs: TCP head-of-line blocking on lossy links, and we write a small jitter buffer. Bandwidth at 16 kHz, 16-bit mono is 256 kbit/s each way; SSB voice needs under 3 kHz, so 12 kHz or 8 kHz with a codec is possible later.

Outcome (2026-11-13): decided in [ADR 0002](0002-audio-transport.md): WSS PCM only for v1. Original plan: implement an `AudioTransport` interface with a WSS PCM implementation first, then a WebRTC implementation in the P3 spike. Measure mouth-to-ear latency, glitch rate at 0, 1 and 3 percent simulated loss, CPU on a Pi 4, and behavior on iPhone Safari tab and home-screen app. Record results in `docs/adr/0002-audio-transport.md` and pick the default from data. If WebRTC wins, WSS PCM stays as the fallback for networks where UDP fails.

Host side uses `sounddevice` (PortAudio) for the radio's USB codec at 48 kHz, 16-bit mono, resampled for the network path. TODO(verify) the sample rates each radio's codec accepts.

### D9. Audio routing per session

`remote` (browser mic to TX, RX to browser), `host` (server bridges the host's chosen mic and speakers to the radio codec; browser only controls), `monitor` (RX to browser, no TX path). The TX audio source is bound to whoever holds the TX lock. Browser echo cancellation, noise suppression and auto gain are separate toggles, all off by default for TX. A TX level meter is computed client side before send and server side after receive.

### D10. Security

- TLS always. First run creates a local root CA and a server certificate with SANs for hostname, `hostname.local`, every LAN IP, and configured VPN IPs, using `cryptography`. Server cert lifetime at most 825 days (Apple's limit for TLS certs; TODO(verify) current Apple requirements for user-installed roots). The CA key is stored 0600 outside the repo. Re-issuing the server cert for a new IP does not require reinstalling the CA on clients. User-supplied cert and key are accepted instead.
- `/setup` is the only unauthenticated page besides static assets: serves the CA certificate (public part only) and per-platform install steps.
- Users in SQLite, Argon2id via `argon2-cffi`, server-side sessions, cookies `Secure; HttpOnly; SameSite=Strict`. CSRF token on all state-changing HTTP endpoints. Login rate limit per IP and per account, with lockout. Admin created from the CLI only.
- Roles: `admin`, `operator` (callsign required), `listener`.
- Strict CSP with no inline script, no third-party origins. All JS and fonts are served from the app.
- Audit log as JSON Lines with rotation: logins, failures, TX start/stop with user, callsign, radio, frequency, duration, end reason.
- Bind address configurable; documented option to bind only to a VPN interface IP.

### D11. Client

Vanilla JS ES modules served as-is, no build step to run (matches the maintainer's other apps). Mobile-first layout, large touch targets, drag/flick tuning. Waterfall on canvas 2D (scroll by self-copy, one new row per scope line); WebGL is an optional later renderer. "Start audio" button creates and resumes the AudioContext from the gesture. Screen Wake Lock where supported. `visibilitychange` and `pagehide` drop PTT.

### D12. Configuration and data

`config.yaml` validated by pydantic, every key documented with its default in `config.example.yaml`. Parameters from the brief (project name, port, club name, radios, bind address, VPN IPs, PTT timeouts) live there. State (users DB, certs, logs) lives in a per-OS data directory, not the install directory.

### D13. Testing

- pytest + pytest-asyncio; Hypothesis for the frame parser and BCD codec.
- CI-V simulator speaking the documented frame and scope formats, usable in-process (all OSes) and over a pseudo-terminal (Linux, macOS). Windows CI uses in-process mode.
- Playwright (Python package, so no Node toolchain) against Chromium, Firefox, WebKit with the simulator. Playwright WebKit is not iOS Safari; iPhone and iPad passes are manual and tracked in `docs/hardware-test.md`.
- GitHub Actions matrix: ubuntu, windows, macos.

### D14. Packaging

No step of install or development needs admin rights (Linux serial/audio group membership and udev rules are the documented exceptions). `uv` is never installed system-wide: the bootstrap and install scripts create a venv with the system Python (3.11+), `pip install uv` into it, and let uv manage the project environment from there. This was tested on the maintainer's Mac, which has Python 3.12 but no admin rights: `python3 -m venv` then `pip install uv` gave uv 0.13.8. The project is also a standard `pyproject.toml` package, so plain `python -m venv` plus `pip install .` works for anyone who prefers not to use uv at all; the lockfile is only for reproducible dev and CI.

One-line install scripts per OS, `radio-web-remote doctor`. Service templates: systemd (Linux), LaunchAgent not LaunchDaemon (macOS, because microphone access is granted per user session by TCC; confirmed 2026-11-13, H7300-16: without it the radio codec reads as silence), Task Scheduler at logon (Windows, same reason; a Windows service is documented as optional). Docker for Linux only, optional. Docker is not a development path on macOS or Windows: Docker Desktop runs containers in a Linux VM with no USB serial or USB audio passthrough, so it could run the simulator tests but never touch a radio. CI covers the Linux container case.

## Consequences

- Single port, single protocol for control and (by default) audio keeps the VPN and future port-forward story simple.
- Safety logic is concentrated in one testable class, with a test per trigger.
- Capability tables make "no invented commands" mechanically enforced.
- Device matching (serial port to its USB audio codec) needs OS-specific code: Linux sysfs, macOS IORegistry, Windows PnP container IDs. A manual pin in config is always available. This is the riskiest cross-platform piece and is scheduled early in P3 rather than left for `doctor` in P5.

## Licensing

Decided: MIT (2026-11-13). All candidate dependencies (FastAPI MIT, Uvicorn BSD-3, pyserial BSD-3, sounddevice MIT, PortAudio MIT, cryptography Apache-2.0/BSD, argon2-cffi MIT, aiortc BSD-3) are compatible with MIT. wfview (GPL-3.0) and Hamlib (LGPL-2.1 library, GPL utilities) are used only as behavioral references; no code is copied. TODO(verify) each license at P0 and record in `THIRD_PARTY_NOTICES.md`.
