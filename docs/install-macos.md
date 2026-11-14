# Install on macOS

Needs Python 3.11 or newer (from python.org) and the radio on USB. No admin
rights needed. macOS needs no extra driver for these radios: the IC-7300 and
IC-9700 show as `/dev/cu.usbserial-...` and the IC-705 as
`/dev/cu.usbmodem...` (seen on macOS, [hardware-test.md](hardware-test.md)).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh
```

Add `~/.local/bin` to your PATH if the script says so. Run it again to
upgrade.

## First run

```bash
radio-web-remote init
```

```bash
radio-web-remote doctor
```

`doctor` finds the radio and prints a `radios:` entry for
`~/Library/Application Support/radio-web-remote/config.yaml`. Set up the
radio's menus first: [radio-setup.md](radio-setup.md).

## Microphone permission (needed even to listen)

macOS gives a program **silence from every audio input, including the
radio's USB codec**, until that program is allowed to use the microphone
(seen on hardware, H7300-16). Allow it in **Apple menu > System Settings >
Privacy & Security > Microphone**
([Apple](https://support.apple.com/guide/mac-help/control-access-to-the-microphone-on-mac-mchla1b1e1fe/mac)).
When you start the server from Terminal, the permission belongs to Terminal
(or your terminal app). `doctor` warns "receive audio is pure silence" when
this is the problem.

**Do not run it as a LaunchAgent.** Tested 2026-11-14 on macOS 26 with an
IC-9700: under launchd the server received pure silence from the radio's
codec, macOS showed no prompt, and nothing appeared in the Microphone list to
switch on. The same server started from Terminal received audio.

## Run it at login

```bash
curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh -s -- --service
```

This writes `~/.local/opt/radio-web-remote/radio-web-remote.command` and opens
it: a Terminal window that runs the server and restarts it if it stops.
Allow Microphone access for Terminal if asked. To start it at every login,
add that file under **System Settings > General > Login Items > Open at
Login** (+). Close the window to stop the server. The Mac must stay logged
in; set it not to sleep while it serves the radio.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh -s -- --uninstall
```

Settings stay in `~/Library/Application Support/radio-web-remote`.
