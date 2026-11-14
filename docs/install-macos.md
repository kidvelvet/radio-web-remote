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
When you start the server from Terminal, macOS asks for Terminal (or your
terminal app). `doctor` warns "receive audio is pure silence" when this is
the problem.

TODO(verify): which name appears in the Microphone list when the server runs
as a LaunchAgent (below). If audio is silent there, run `radio-web-remote
doctor` from Terminal to check the radio side.

## Run it as a service

```bash
curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh -s -- --service
```

This adds a LaunchAgent (`~/Library/LaunchAgents/`) that starts at login and
restarts the server if it stops. Log: `~/Library/Logs/radio-web-remote.log`.
Stop it with `launchctl bootout gui/$(id -u)/io.github.kidvelvet.radio-web-remote`
(launchctl(1) on macOS).

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh -s -- --uninstall
```

Settings stay in `~/Library/Application Support/radio-web-remote`.
