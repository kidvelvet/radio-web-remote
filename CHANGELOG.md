# Changelog

## 0.1.2 (2026-11-14)

Found while testing two radios (IC-7300 and IC-9700) on one Mac.

- Several radios on one computer (macOS, Linux): `auto` audio picks the codec
  on the same USB hub as the radio's serial port. All Icom codecs have the
  same name, and device numbers change when other devices come and go (an
  iPhone offered as a microphone, for one). doctor says which hub it matched.
- A radio switched off with its USB cable in refuses every command. The page
  and the log now say it is probably switched off, once, instead of blaming
  the CI-V address every few seconds; the log says when it is back. doctor
  says to switch it on.
- An audio device written as a quoted number (`input_device: "3"`) is an
  index. It was read as part of a name, so "3" could pick "CalDigit
  Thunderbolt 3 Audio", and doctor reported it as fine.
- Docs: set the radio's USB AF SQL to ON so FM receive audio goes quiet when
  squelched (radio-setup.md, troubleshooting.md).

## 0.1.1 (2026-11-14)

Fixes from a six-round code review made before the repository went public
(184 fixes, each listed in [docs/review-changelog.md](docs/review-changelog.md)),
and a radio menu.

### Added

- With more than one radio, a menu at the top of the page switches between
  them in place: audio and the scope keep running, and PTT is released before
  the switch. The page, and the home-screen app, open on the radio used last.
- Scripts and style sheets are checked with the server on every load, so a
  browser never runs a cached script against a newer page after an upgrade.

### Fixed

- Transmit safety: each PTT press carries an id, so a late refusal of an
  earlier press never drops a newer one, and a press can be cancelled until
  it keys. A release sent while the server was still reading the transmit
  frequency (IC-705, IC-9700) no longer arrives after the key.
- A stop by another operator, or a second tab on the same account, no longer
  leaves a page showing PTT released while the radio still transmits.
- TX off is resent after 0.15 s when the radio does not confirm it, and is
  confirmed in time even when another command is stuck on the radio's bus.
- `doctor` now fails a damaged user database or a broken local CA, the same
  problems the server refuses to start with.
- The Docker build no longer copies private keys kept in subfolders.
- Install scripts: an upgrade restarts a running Linux service, no longer
  deletes the program under a running server, and finds PortAudio on Debian;
  on Windows the scheduled task is stopped before an upgrade and started
  again afterwards, also when the upgrade fails, and the user PATH keeps its
  variables.

## 0.1.0 (2026-11-14)

First release. Browser remote for Icom transceivers over USB, for the LAN or
a VPN.

### Radios

- IC-7300, IC-705 and IC-9700 over USB, each from its own Icom documents and
  checked on hardware (IC-705 firmware 1.32, IC-9700 firmware 1.50;
  [docs/hardware-test.md](docs/hardware-test.md)).
- Frequency, mode, filter, DATA mode, AF and RF gain, squelch, RF power;
  S, Po, SWR and ALC meters.
- Spectrum scope and waterfall with tap to tune, all scope modes the model
  documents.
- Finds a radio whose CI-V address was changed (broadcast read ID; on by
  default, `auto_detect_address`).
- IC-9700: MAIN and SUB selection, swap, Dualwatch, satellite mode
  indicator; the scope follows the selected band.

### Transmit safety (enforced on the server)

- Unkey on PTT release, closed connection, missed heartbeats (2 s), hidden
  page, TX timeout (180 s at most, can only be lowered), logout, radio error;
  TX off is the first command after every connect.
- One transmitter at a time; per-user band and power limits checked against
  the frequency the radio reports it will transmit on (split, IC-9700 band,
  satellite uplink, repeater shift).

### Audio

- Receive and transmit audio in the browser over the same HTTPS port (16 kHz
  PCM, adaptive jitter buffer), or on the radio computer's own mic and
  speaker; iPhone and iPad in Safari and as a home-screen app, with one-tap
  Resume audio.

### Security

- HTTPS always, with a local CA (name-constrained) and a /setup page;
  Argon2id accounts with admin, operator and listener roles; login rate
  limits and lockout; CSRF protection, strict CSP, origin checks; audit log.

### Setup

- One-line install scripts for Linux, macOS and Windows, with a service
  option (systemd user service, a Terminal login item on macOS, Task
  Scheduler); `radio-web-remote doctor`;
  udev and Docker examples.
