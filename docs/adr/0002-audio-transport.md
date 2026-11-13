# ADR 0002: Audio transport for v1

- Status: Accepted
- Date: 2026-11-13
- Supersedes: the "decide with a measured spike" part of ADR 0001, D8

## Decision

**v1 carries audio as 16 kHz 16-bit mono PCM over the existing control
WebSocket (WSS), played and captured with AudioWorklets.** WebRTC is not used
in v1. The spike is kept on branch `spike/webrtc` for reference.

## What was compared

1. **WSS PCM** (built in P3a): 20 ms frames with a 12-byte header on the same
   TLS port and authenticated socket as control. Browser jitter buffer: start
   at 80 ms, capped at 400 ms. Server transmit buffer: 60 ms prefill, 300 ms cap.
2. **WebRTC with aiortc 1.16.0** (spike): one peer per control socket, Opus,
   host candidates only, offer posted to an authenticated, CSRF-checked
   endpoint, transmit audio through the same router (holder-only rule unchanged).

## Measurements

Tool: `tools/bench_audio.py` (on main for WSS; with WebRTC rows on the spike
branch). A simulated radio echoes transmit audio into receive. A synthetic
microphone emits an 80 ms 1 kHz burst while PTT is held, and the time until
the burst comes out of the page's own audio output is measured: browser mic,
transport, server, radio, server, transport, browser output. Same machine
(Apple silicon Mac, macOS), headless browsers, 15 bursts each.

| Browser | Transport | Round trip, median | p90 | Server CPU, 1 client |
|---------|-----------|--------------------|-----|----------------------|
| Chromium | WSS PCM | 153 ms | 157 ms | 10 % |
| Firefox | WSS PCM | 127 ms | 142 ms | 10 % |
| WebKit | WSS PCM | 178 ms | 179 ms | 7 % |
| Chromium | WebRTC | not measured (burst never returned) | | 23 % |
| Firefox | WebRTC | not measured | | 6 % |
| WebKit | WebRTC | not measured | | 4 % |

Most of the WSS round trip is deliberate buffering (80 ms receive start plus
60 ms transmit prefill). On the real IC-7300 the operator described receive
audio in the browser as having "nearly no delay" (docs/hardware-test.md,
H7300-16).

WebRTC: the peer connected (ICE succeeded), RTP flowed both ways, and the
test burst reached the server at about -1 dBFS, but it never came back out
of the browser, so no latency figure was obtained. The cause was not found
within the spike's time box.

## Why WSS, beyond the numbers

- **One port.** aioice 0.13.0's `Connection` takes no port or port-range
  option (checked in its signature), and aiortc's ICE gatherer has none, so
  WebRTC media uses ephemeral UDP ports. The brief asks for one
  non-standard port that a member can later forward; WSS uses the HTTPS port.
- **VPN reachability.** In the spike, Chrome's host candidate arrived as an
  mDNS `.local` name that aioice could not resolve ("could not be resolved"
  in its log); only a peer-reflexive candidate rescued the connection. Over a
  VPN that rescue is not guaranteed. WSS works wherever HTTPS does.
- **One security surface.** WSS audio rides the session-authenticated,
  origin-checked socket. WebRTC adds a signalling endpoint and DTLS/SRTP
  stacks to keep patched.
- **Install weight.** aiortc pulls in PyAV (FFmpeg), pylibsrtp, pyOpenSSL
  and google-crc32c binary wheels; WSS needs nothing beyond the server.
- **CPU.** One Chromium client cost 23 % server CPU with WebRTC against 10 %
  with WSS (Opus encode and decode in Python-driven pipelines). Firefox and
  WebKit showed lower WebRTC CPU, but those runs carried no confirmed audio.

## Costs accepted

- **TCP head-of-line blocking.** On a lossy link a lost packet delays the
  frames behind it; the jitter buffer then trims to keep latency bounded
  (gaps are audible). Not measured: simulating loss needs root (`tc netem`,
  `dnctl`), unavailable on the maintainer's machine. TODO(verify) on a real
  lossy VPN or Wi-Fi link during the iPhone pass.
- **Bandwidth.** 256 kbit/s each way per listener, versus roughly 32 to
  64 kbit/s for Opus. Fine on LAN and typical VPNs; a phone on a weak
  cellular link may struggle. A compressed codec over the same socket
  (for example Opus via WebCodecs where available) is the upgrade path if
  members need it, and keeps the one-port property.

## Revisit when

- members report choppy audio over lossy links, or
- a member needs very low bandwidth, or
- aiortc/aioice gain fixed-port support.
