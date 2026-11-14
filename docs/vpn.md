# Reaching the radio over a VPN

radio-web-remote is meant for the LAN and for devices that join that LAN
through a VPN (WireGuard, OpenVPN, a firewall's built-in VPN, or a mesh
VPN). It does not need anything VPN-specific: if a device on the VPN can open
`https://<radio computer>:8473/`, it works. This page is generic on purpose;
follow your VPN's own documentation to set it up.

## What has to be true

1. **The VPN device can reach the radio computer's address and port.** Either
   the VPN routes to the LAN the radio computer is on, or the radio computer
   is itself on the VPN and has a VPN address. The port is `server.https_port`
   (default 8473); one TCP port carries the page, control, scope and audio.
2. **The certificate covers the address you type.** The server certificate
   lists the computer's hostname, `<hostname>.local`, `localhost` and every
   address on its interfaces when the server starts. If you reach it through
   a VPN address it does not see as its own (for example the VPN's address
   for a router in front of it), add that address or name to the config and
   restart:

   ```yaml
   server:
     vpn_ips: ["10.8.0.5"]
     hostnames: ["radio.example.lan"]
   ```

   The local CA only issues for local names and private address ranges
   (10/8, 172.16/12, 192.168/16, 100.64/10, link-local and IPv6 unique-local)
   plus any address listed here. `radio-web-remote doctor` warns if the
   certificate does not cover a configured address.
3. **The device trusts the local CA.** Install it once per device from
   `https://<address>:8473/setup` ([ios.md](ios.md) for iPhone and iPad).

## Optional: listen only on the VPN

To accept connections only from the VPN, bind to the radio computer's VPN
address instead of all interfaces:

```yaml
server:
  bind_address: 10.8.0.5
```

## Audio over the VPN

Audio uses about 256 kbit/s each way per listener (16 kHz PCM, ADR 0002).
The receive buffer grows by itself on a jittery link, at the cost of a
little more delay. If audio breaks up, check the VPN link's loss and latency
first; the server logs `audio receive at <user>` lines when a client's link
is struggling.

## Not supported in v1

Opening the port to the internet without a VPN. See
[security.md](security.md) for what that would need.
