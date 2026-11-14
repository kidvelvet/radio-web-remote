# Install on Linux (including Raspberry Pi OS 64-bit)

Needs Python 3.11 or newer (Raspberry Pi OS Bookworm ships 3.11) and the
radio on USB. The install itself needs no admin rights; the two `sudo` lines
below are one-time system setup.

## 1. System packages and groups (once)

```bash
sudo apt install python3-venv libportaudio2
```

```bash
sudo usermod -a -G dialout,audio $USER
```

Log out and back in after `usermod`. On Debian and Raspberry Pi OS, USB
serial ports (`/dev/ttyUSB*`, `/dev/ttyACM*`) belong to the `dialout` group
and sound devices to `audio` (upstream systemd rule `50-udev-default`;
[Debian SystemGroups](https://wiki.debian.org/SystemGroups);
[Raspberry Pi: grant user access](https://www.raspberrypi.com/documentation/computers/configuration.html#grant-user-access)).
`libportaudio2` is the audio library python-sounddevice uses on Linux.

## 2. Install

```bash
curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh
```

This puts the program in `~/.local/opt/radio-web-remote` and the command in
`~/.local/bin`. Run it again to upgrade. Prefer pipx? `pipx install <release wheel URL>`
gives you the same command; add the service yourself from
[packaging/systemd/](../packaging/systemd/).

## 3. First run

```bash
radio-web-remote init
```

```bash
radio-web-remote doctor
```

`init` creates the admin account and the local certificate authority.
`doctor` finds the radio, checks the CI-V link and audio, and prints a
`radios:` entry to paste into `~/.local/share/radio-web-remote/config.yaml`
(see [config.example.yaml](../config.example.yaml)). Set up the radio's menus
first: [radio-setup.md](radio-setup.md).

## 4. Run it as a service

```bash
curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh -s -- --service
```

This adds a systemd user service that starts at login and restarts the
server if it stops:

```bash
systemctl --user status radio-web-remote
```

```bash
journalctl --user -u radio-web-remote
```

On a headless Pi, let it run without anyone logged in:

```bash
sudo loginctl enable-linger $USER
```

## Stable device names (optional)

radio-web-remote finds the radio by USB serial number, so `/dev/ttyUSB0`
changing does not matter to it. For other programs or Docker, see the example
rules in [packaging/udev/](../packaging/udev/).

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh -s -- --uninstall
```

Your settings stay in `~/.local/share/radio-web-remote`.

Docker is optional: [packaging/docker/README.md](../packaging/docker/README.md).
