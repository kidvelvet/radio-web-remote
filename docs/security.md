# Security

Status: P1. Covers what exists now; audio (P3) and packaging (P5) will add to it.

## Threat model

- **The LAN is untrusted.** Every page and socket except `/setup`, its CA
  download, `/login` and static assets needs a logged-in session, over TLS.
- **Remote clients** reach the server over a VPN. Opening a firewall port to
  the internet is possible later without redesign, but see "Before exposing
  to the internet".
- **Assets protected:** the right to transmit (licensed, accountable,
  fail-safe), account credentials, and the CA private key.

## What is in place

| Area | Control |
|------|---------|
| Transport | TLS always (HTTPS and WSS on one port, default 8473). Local CA or your own certificate. |
| Passwords | Argon2id (argon2-cffi defaults). Minimum 10 characters. Hashing runs off the event loop. |
| Lockout | 5 failed logins lock the account for 15 minutes. Unknown usernames take the same time as real ones. |
| Rate limit | 10 failed logins per client address per 5 minutes; successful logins do not count. |
| Sessions | Random 256-bit token in a `Secure; HttpOnly; SameSite=Strict` cookie; only its SHA-256 is stored. 12 h idle, 7 days absolute. Ended on logout, password, role or limit change, or disable. |
| CSRF | Every state-changing request carries `X-CSRF-Token`: the session's token, or for login a pre-login double-submit token. |
| WebSocket | Session required, and `Origin` must equal `https://<Host>`; anything else is closed before accept. Messages are size-limited and validated. |
| Browser | Strict Content-Security-Policy (`'self'` only, no inline script or style, `frame-ancestors 'none'`), `nosniff`, `no-referrer`, HSTS, Permissions-Policy allowing only the microphone. No third-party origins. |
| Roles | admin, operator (needs a callsign), listener. Listeners cannot transmit or change settings. Only the TX holder can change settings while transmitting. |
| Limits | Per-user band ranges and RF power control limit, enforced on the server before keying and while keyed. |
| TX safety | All fail-safes on the server (docs/adr/0001-architecture.md D6). |
| Audit | JSON Lines log, rotated at 5 MB x 10: logins, failures, lockouts, logouts, TX start/stop with user, callsign, radio, frequency, duration and reason. Secret fields are dropped. |
| Keys | CA and server keys written with mode 0600 in the data folder, never in the repo. |
| Admin | The first admin is created from the command line only (`radio-web-remote init`). |

## Before exposing to the internet

Not recommended in v1. If a member does it anyway, at minimum:

1. Long unique passwords for every account; remove unused accounts.
2. Add a second factor (TOTP is not built yet).
3. Add address-based blocking for repeated failures (fail2ban or the
   firewall's own feature) in front of the per-address limit here.
4. Use a certificate from a public CA, or keep clients on the VPN.
5. Keep the server and the host OS updated; watch the audit log.
6. Bind to the VPN interface only (`server.bind_address`) if the port is
   meant for VPN clients.

## Reporting a vulnerability

See SECURITY.md (P5). Until then, contact the maintainer privately rather
than opening a public issue.
