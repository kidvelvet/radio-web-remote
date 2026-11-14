# radio-web-remote: v1 plan

Status: proposed, awaiting approval. Architecture is in [adr/0001-architecture.md](adr/0001-architecture.md).

## Status (2026-11-13)

- **P0: done.** CI green on Linux (3.11, 3.12, 3.13), Windows, macOS. IC-7300 hardware checks H7300-1 to -6 pass.
- **P1: done.** 194 unit and integration tests, 36 browser tests (Chromium, Firefox, WebKit), CI green. IC-7300 hardware: H7300-7, -8, -10, -11, -12 run on 2026-11-13 (all pass; -7 confirmed the radio stays keyed when USB is pulled). Carried forward: H7300-13 (phone network drop) to the P3 iPhone pass, and a carrier test of the Po/SWR/ALC meters.
- **P2: done.** IC-7300 scope and waterfall: parser built from the manual and settled against real frames (H7300-14), simulator, browser UI with tap-to-tune and scope settings; 216 unit and integration tests, 42 browser tests; on-radio check H7300-15 passed.
- **P3a: done.** Audio over WSS (16 kHz PCM), remote/host/monitor routing, mic and speaker pickers, TX level; IC-7300 checks H7300-16, -17, -18 pass.
- **P3b: done.** WebRTC compared and set aside for v1 (ADR 0002; spike on branch `spike/webrtc`). iPhone and iPad pass in a Safari tab and as a home-screen app, including H7300-13 (IOS-1 to -5). Adaptive receive buffer and per-client audio diagnostics added after iPad Wi-Fi testing. "Resume audio" after a relaunch: done in P5.
- **P4: done.** IC-705 and IC-9700 tables from Icom's own current PDFs (download agreement accepted by the maintainer); simulator profiles; MAIN/SUB select and swap with the scope following the selected band; the TX limit check reads the radio's transmit frequency (1C 03), so split, the IC-9700 MAIN/SUB arrangement and satellite mode cannot transmit outside a user's bands. Band shortcuts follow each model's documented coverage. IC-705 rows H705-1 to -8 pass (firmware 1.32) and IC-9700 rows H9700-1 to -10 pass (firmware 1.50), 2026-11-13. **P4: done.** **P5 next:** packaging, doctor, docs, release.

## 0. Decisions recorded (2026-11-13)

- Name: radio-web-remote (Python package `radio_web_remote`). Other makers' radios may be added later, so the core is vendor-neutral.
- License: MIT.
- Commits authored as Steve Whitaker <kvmw@kidvelvet.net> (set locally in this repo). The GitHub repo stays private, or unpushed, until the maintainer agrees to make it public.
- Hardware on hand for testing: IC-7300, IC-705, IC-9700 (maintainer).
- Toolchain: no admin rights on the dev Mac. uv runs from a project-local venv (see ADR D14); Docker is not used for development.

## 1. Facts still to verify

Rule: a fact is used in code or docs only after it is checked against the source in the right column. Until then it appears as `TODO(verify)`. Every CI-V entry lands in `docs/civ-reference.md` (function, bytes, models, document, page).

| # | Fact | Primary source | Needed by |
|---|------|----------------|-----------|
| F1 | 47 CFR 97.213 current wording and heading | eCFR (ecfr.gov), Title 47 Part 97 | Done 2026-11-13: heading "Telecommand of an amateur station"; text in docs/regulatory.md |
| F2 | IC-7300 CI-V commands for every P1/P2 control: frequency, mode/filter, DATA mode, AF/RF/squelch, RF power, preamp, ATT, AGC, NB, NR, notch, RIT/XIT, split, meters (S, Po, SWR, ALC), PTT, scope `27 xx` | Icom IC-7300 Full Manual, CI-V section (icomjapan.com / icomamerica.com downloads) | P1, P2 |
| F3 | IC-7300 scope data cadence and whether Transceive affects scope output | IC-7300 Full Manual | P2 |
| F4 | IC-705 scope over USB: division count, waveform length, value range | IC-705 CI-V Reference Guide, `27 00` section. Other models list 689 points (IC-7610, IC-7760); not assumed for IC-705 | P4 |
| F5 | IC-9700 scope over USB, main vs sub selection, division count, length, range | IC-9700 CI-V Reference Guide | P4 |
| F6 | IC-705 and IC-9700 commands for every exposed control, incl. IC-9700 main/sub band selection and satellite mode interactions | Each model's CI-V Reference Guide | P4 |
| F7 | Radio menu settings and paths per model: CI-V baud, CI-V USB Port link/unlink, CI-V Transceive, USB Echo Back, DATA OFF MOD / DATA MOD, USB MOD level, USB SEND | Each model's Full/Advanced Manual | P1 (7300), P4 |
| F8 | Radio TX time-out timer menu, per model, if any | Each model's manual | P1 (7300), P4 |
| F9 | Which items each model broadcasts with CI-V Transceive ON | CI-V Reference Guides; confirm on hardware | P1, P4 |
| F10 | USB codec sample rates accepted per radio | Icom manuals; confirm with `doctor` on hardware | P3 |
| F11 | USB VID/PID and serial-number behavior per radio (to match ports and audio by topology) | Observed on hardware via `doctor`; Icom driver docs | P1, P3 |
| F12 | Icom Windows USB driver download page and install steps | Icom support site | Done 2026-11-13 (icomjapan.com driver pages, docs/install-windows.md); Device Manager port names TODO(verify) |
| F13 | iOS/iPadOS steps to install a profile and enable full trust for a root certificate; Apple's TLS cert requirements for user-installed roots | Apple Support / Apple Platform Deployment docs | P1 |
| F14 | DTR/RTS state on port close and on process death, per OS | pyserial docs, OS docs; confirm on hardware | P1 |
| F15 | aiortc/aioice: fixed UDP port or range support; mDNS candidate handling | aiortc docs and source | P3 spike |
| F16 | Safari (iOS) AudioWorklet, getUserMedia in home-screen mode, Wake Lock support | WebKit release notes, MDN compat data; confirm on device | P3 |
| F17 | Current stable versions and licenses of every dependency | PyPI pages, project LICENSE files | P0 |
| F18 | Regulatory note links: FCC Part 97, and a pointer that operation follows the rules of the country where the radio is | eCFR; national regulator sites as linked, no claims beyond the source | Done for the US (97.5, 97.107); the general rule has only a secondary source (ARRL), TODO(verify) in docs/regulatory.md |

## 2. Repo layout

```
radio-web-remote/
  pyproject.toml              # uv-managed, console script "radio-web-remote"
  config.example.yaml         # every key, documented default
  LICENSE  README.md  CONTRIBUTING.md  SECURITY.md  CHANGELOG.md  THIRD_PARTY_NOTICES.md
  src/radio_web_remote/
    __main__.py  cli.py        # P0: config check, drivers, sim, probe. Later: serve, init, user, doctor
    sources.py                 # Source citation type
    config.py                  # pydantic models for config.yaml
    paths.py                   # per-OS data dir
    transport/                 # protocol-agnostic byte links
      base.py                  # priority queue + urgent (PTT) path
      serial_port.py           # threaded serial reader, DTR/RTS low before open
      loopback.py              # in-process pair for the simulator
    protocols/
      civ/                     # Icom CI-V; future yaesu_cat/, kenwood/ are siblings
        frame.py               # parser/encoder, echo + collision handling
        bcd.py                 # frequency and level encodings
        commands.py            # typed command builders
        scope.py               # division reassembly per model
    radios/
      base.py                  # vendor-neutral types + RadioDriver interface
      registry.py              # "icom.ic7300" -> driver; entry points later
      icom/
        model.py               # capability table schema (each entry needs a source)
        civ_driver.py          # RadioDriver over CI-V, driven by a table
        ic7300.py  ic705.py  ic9700.py
    session/
      radio_session.py         # per-radio owner of transport, driver, audio, tx
      tx_controller.py         # PTT state machine, all fail-safes
      poller.py                # transceive + round-robin polling
    audio/
      host_io.py               # sounddevice streams to/from radio codec
      routing.py               # remote / host / monitor
      transport_ws.py          # PCM over WSS
      transport_webrtc.py      # P3 spike
    devices/
      discover.py              # serial + audio enumeration
      topology_linux.py  topology_macos.py  topology_windows.py
    web/
      app.py                   # FastAPI app factory, CSP, static
      auth.py  sessions.py  csrf.py  ratelimit.py
      ws_control.py            # control + scope WSS
      setup_page.py            # /setup, CA download
    security/
      tls.py                   # CA + leaf generation, renewal
      users.py                 # SQLite, Argon2id, roles, limits
      audit.py                 # JSON Lines, rotation
    sim/
      icom.py                  # fake radio: pure core, loopback and PTY runners
    static/                    # client SPA, no build step
      index.html  setup.html
      js/  (app.js, ws.js, ptt.js, tuning.js, waterfall.js, audio-*.js, worklets/)
      css/
  tests/
    unit/  integration/  e2e/  (Playwright)
    fixtures/                  # recorded/constructed CI-V frames per model
  packaging/
    systemd/  launchd/  windows/  docker/  udev/
    install.sh  install.ps1
  docs/
    adr/  plan.md  civ-reference.md  radio-setup.md
    install-linux.md  install-macos.md  install-windows.md
    vpn.md  ios.md  security.md  troubleshooting.md  hardware-test.md  regulatory.md
  .github/workflows/ci.yml
```

## 3. Phases and acceptance criteria

Each phase ends with a summary: what works, what ran in simulation vs on hardware, open `TODO(verify)` items, what I need from you.

### P0: scaffold, config, CI, CI-V library, simulator

- Repo, license, pyproject, uv lockfile, lint (ruff), type check (mypy or pyright), pytest.
- `config.py` loads and validates `config.yaml`; every brief parameter is a config key; no hard-coded port, club name or addresses.
- Vendor-neutral `radios/base.py` and registry; `protocols/civ/frame.py`, `protocols/civ/bcd.py`; `transport/serial_port.py` with the priority queue.
- `scripts/bootstrap.sh` and `scripts/bootstrap.ps1`: create a project-local `.tools/` venv with the system Python, `pip install uv` into it, then `uv sync`. No admin rights needed.
- Simulator: answers frequency/mode/PTT for an IC-7300 profile in-process and over PTY.

Accept when: CI green on Linux, Windows, macOS; frame parser and BCD codec have unit plus Hypothesis tests (round trip, junk, truncated frames, collisions, echoes); simulator round trip of read/set frequency passes in CI on all three OSes; `THIRD_PARTY_NOTICES.md` lists every dependency with license and version.

### P1: IC-7300 control, PTT fail-safe, auth, TLS, minimal UI

- F2, F7, F8, F13, F14 verified for the IC-7300 and recorded.
- `init` CLI: creates admin, CA, server cert. `/setup` page with CA download and per-platform steps.
- Login, sessions, CSRF, rate limit, lockout, roles, audit log.
- Control WSS; UI for frequency (direct entry, steps, bands, drag tuning), mode/filter/DATA, levels, meters, PTT (hold and toggle, spacebar), TX-held indicator.
- `TxController` with every trigger from ADR D6.

Accept when: one automated test per PTT trigger proves unkey (release, WSS close, heartbeat miss at configured interval, hidden tab, TX timeout, out-of-limit frequency/power change, holder logout) and proves TX off is the first command after start and after reconnect; config cannot raise TX timeout above 180 s (test); unauthenticated requests to everything except `/setup` and static assets get 401/redirect (test); CSP header present and strict (test); Playwright drives tune and PTT against the simulator in Chromium, Firefox, WebKit. Hardware check on your IC-7300: read/set frequency and mode, PTT key/unkey into a dummy load, fail-safe by killing the browser while keyed.

### P2: IC-7300 scope and waterfall

- F3 verified. Scope on/off, data output, center/fixed, span, edges, reference level, speed.
- Division reassembly (11 divisions, header first, 475 points, 0 to 160) with unit tests on constructed frames.
- Waterfall canvas with adjustable color scale; click/tap to tune.

Accept when: reassembly tests cover in-order, missing, duplicated and interleaved divisions; simulator scope renders in all three Playwright browsers; waterfall sustains the radio's update rate on a mid-range phone without dropping control messages (measured); hardware check on IC-7300 with "Unlink from [REMOTE]" and 115200 baud.

### P3: audio both directions, three routing modes, iOS

- F10, F15, F16 verified. Device topology matching (Linux, macOS, Windows) plus manual pin.
- WSS PCM transport, then WebRTC spike. ADR 0002 records measured latency, loss behavior, CPU, iOS results, and the chosen default.
- Remote, host and monitor routing; TX lock binds the audio source; browser processing toggles default off; TX level meter.

Accept when: simulator echo path proves audio arrives in both directions in Chromium and Firefox with fake media devices (WebKit where Playwright supports it, otherwise noted); a second client cannot send TX audio while not holding the lock (test); iPhone and iPad pass in Safari tab and home-screen mode, mic and RX audio, PTT drops when backgrounded (manual, recorded in hardware-test.md).

### P4: IC-705 and IC-9700

- F4, F5, F6, F7, F8, F9, F11 verified for both models. Capability tables with sources; any undocumented control left out.
- IC-9700 main/sub frequency, mode and scope selection.

Accept when: each new command has a builder test against the documented bytes; simulator profiles for both models; UI hides controls a model lacks; hardware checks run by the maintainer on an IC-705 and an IC-9700 (control, PTT fail-safe, scope, audio, IC-9700 main/sub), recorded in hardware-test.md.

### P5: packaging, doctor, docs, release

- `doctor`: lists ports and audio devices, matches them to radios, reads frequency, prints fixes.
- Install scripts, systemd unit, LaunchAgent, Task Scheduler option, udev examples, Docker (Linux, optional).
- All docs from the brief, regulatory note, SECURITY.md, CHANGELOG, screenshots.

Accept when: a clean install on Pi OS 64-bit, Windows 11 and macOS completes with the one-line script and `doctor` reports the radio; docs reviewed against the source table; `v0.1.0` tag with release notes.

## 4. Commit practice

Small commits, tests in the same commit as the code they cover, conventional prefixes (`civ:`, `tx:`, `web:`, `docs:`). Nothing is pushed to a public GitHub repo until the maintainer agrees. `.tools/`, `.venv/`, certificates, keys, user databases and logs are in `.gitignore` from the first commit.
