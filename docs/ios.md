# iPhone and iPad

Safari only allows the microphone on pages it trusts (a "secure context"),
so each iPhone or iPad needs this server's local CA installed and trusted
once. The same steps are on the server's `/setup` page.

## Install and trust the certificate

1. In **Safari**, open `https://<server>:8473/setup` and tap **Download the
   CA certificate**. Allow the download.
2. Open **Settings**, tap **Profile Downloaded** near the top (or
   **General > VPN & Device Management**), then **Install**.
   This worked on an iPhone and an iPad (docs/hardware-test.md, IOS-1).
   TODO(verify): the exact menu wording, which is not on the Apple pages read.
3. **Required:** Settings > General > About > **Certificate Trust Settings**,
   and under "Enable full trust for root certificates" turn on
   **radio-web-remote local CA**. Apple: a manually installed certificate
   "isn't automatically trusted for SSL" until you do this.
   Source: support.apple.com/en-us/102390.

## What the server does to satisfy iOS

Apple's rules for TLS server certificates on iOS 13 / macOS 10.15 and later
(support.apple.com/en-us/103769), and how this project meets them:

| Apple requirement | This server |
|-------------------|-------------|
| RSA keys of 2048 bits or more, for the server and issuing CA | Server 2048-bit, CA 3072-bit |
| SHA-2 signature hash | SHA-256 |
| Server's DNS name in Subject Alternative Name | Hostname, hostname.local, localhost, configured names, plus IP SANs |
| ExtendedKeyUsage with id-kp-serverAuth | Yes |
| Validity of 825 days or fewer | 397 days by default, config cap 825 |

## Using it

- Tap **Start audio** and allow the microphone when asked. iOS needs this tap
  every time the page or app is opened.
- Works in a Safari tab and as a home-screen web app (both tested,
  docs/hardware-test.md IOS-2 and IOS-4).
- When the app goes to the background, the screen locks or the network drops,
  PTT drops: the page releases it, and the server unkeys within the heartbeat
  timeout anyway (H7300-13: about 1 s on air in each case).
- Receive audio over Wi-Fi: the buffer grows by itself on a jittery link, at
  the cost of a little more delay. If audio still breaks up, try another spot
  in the room or closer to the access point; in testing, one spot in a room
  caused clusters of gaps that moving the iPad fixed.
- After swiping the home-screen app away, iOS can keep playing the old
  instance's audio for a while, even on the login page. It stops by itself.
