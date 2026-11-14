# Changelog

## 0.1.0 (unreleased)

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
  option (systemd, launchd, Task Scheduler); `radio-web-remote doctor`;
  udev and Docker examples.
