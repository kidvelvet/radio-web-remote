# Security policy

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue:

- Use GitHub's private vulnerability reporting on this repository (the
  "Security" tab, "Report a vulnerability").
- Include what you found, how to reproduce it, and the version
  (`radio-web-remote --version`).

You should get a reply within a week. Fixes are released as soon as they are
ready, with credit if you want it.

## Scope

The server controls a radio transmitter, so the most serious problems are
ones that let someone transmit without permission, bypass the PTT
fail-safes, or keep a transmitter keyed. The threat model and what is in
place are in [docs/security.md](docs/security.md).

## Supported versions

Only the latest release gets fixes during v0.x.
