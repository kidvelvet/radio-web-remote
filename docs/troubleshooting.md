# Troubleshooting

Start with:

```bash
radio-web-remote doctor
```

It checks the config, accounts, certificates, serial ports, audio devices and
each radio (sending TX off first), and ends with a "What to fix" list. Stop
the running server first so doctor can talk to the radio.

## The radio does not answer

- **Wrong CI-V address.** The radio's address is a menu setting and a PRESET
  can change it (IC-705). doctor finds the real address with a broadcast and
  tells you what to put in `civ_address`, or set `auto_detect_address: true`.
  One maintainer's IC-705 was set to 94, the IC-7300's address (H705-1).
- **Wrong port.** The IC-705 and IC-9700 each have two serial ports; only
  port A carries CI-V. Match the radio by `usb_serial` and the server picks
  port A.
- **IC-7300 or IC-9700: CI-V USB settings.** See
  [radio-setup.md](radio-setup.md): CI-V USB Port "Unlink from [REMOTE]",
  CI-V USB Baud Rate 115200 for the scope.
- **Linux: permission denied** opening `/dev/ttyUSB0` or `/dev/ttyACM0`: add
  yourself to `dialout` ([install-linux.md](install-linux.md)).
- **Windows: no COM port** for the radio: install Icom's USB driver
  ([install-windows.md](install-windows.md)).

## No receive audio, or silence

- **macOS: silence even from the radio's codec.** The program running the
  server needs Microphone permission ([install-macos.md](install-macos.md)).
  doctor reports "receive audio is pure silence".
- **The page plays to the wrong speaker.** Pick the speaker in the Audio
  panel (where the browser allows it). On a Mac, the default output may be a
  mixer or interface rather than the built-in speakers.
- **iPhone or iPad: no sound after reopening the app.** iOS starts audio only
  from a tap. Tap **Resume audio** at the top of the page.
- **Choppy audio over Wi-Fi.** One position in a room can be enough to cause
  clusters of gaps (seen on an iPad, IOS-5). Move closer to the access point.
  The server logs `audio receive at <user>` lines describing late or lost
  frames.

## Transmit audio does not reach the radio

The radio picks its modulation source per mode. For voice modes set **DATA
OFF MOD** to include USB (IC-7300: USB; IC-705: MIC,USB, its default;
IC-9700: MIC,USB, not its default MIC,ACC). Otherwise the radio sends a
silent carrier from the page (seen on the IC-9700, H9700-10). Paths in
[radio-setup.md](radio-setup.md).

## PTT is refused

The message says why:

- **Outside your bands or above your power limit**, as set by an admin. The
  server checks the frequency the radio will actually transmit on, which can
  differ from the display: split, the IC-9700's selected band, satellite
  uplink, or a repeater shift.
- **Held by another operator.** One transmitter at a time; the bar shows who.
- **Release first** after the TX timeout (180 s at most).
- **Your account cannot transmit**: listeners, and accounts with no
  callsign.

## The IC-9700 will not select SUB

The SUB receiver (Dualwatch) is off. Tap **Dualwatch** first.

## The browser says the connection is not private

The device does not trust the local CA yet, or you typed an address the
certificate does not cover. Install the CA from `/setup`, and see
[vpn.md](vpn.md) for VPN addresses.

## The radio stays keyed after the server died

A key sent over CI-V survives the server process dying (H7300-4 C). Restart
the server (it sends TX off first), or press the radio's TRANSMIT key. Run it
as a service so it restarts by itself, and turn on the radio's Time-Out Timer
([radio-setup.md](radio-setup.md)).
