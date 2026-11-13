# radio-web-remote

Self-hosted browser remote control for amateur radio transceivers over USB.
A small server runs on the computer cabled to the radio; any browser on the
LAN or VPN gets the controls, scope, receive audio and transmit.

Status: **P0, pre-alpha.** No web UI yet. What exists today is the CI-V
protocol library, a simulated radio, and a command-line probe. See
[docs/plan.md](docs/plan.md) for the roadmap and
[docs/adr/0001-architecture.md](docs/adr/0001-architecture.md) for the design.

Planned radios for v1: Icom IC-7300, IC-705, IC-9700 (USB only). The core is
vendor-neutral so other makers can be added.

## Developer setup (no admin rights needed)

Needs Python 3.11 or newer already installed.

macOS / Linux:

```sh
./scripts/bootstrap.sh
.tools/bin/uv run pytest
```

Windows (PowerShell):

```powershell
.\scripts\bootstrap.ps1
.tools\Scripts\uv.exe run pytest
```

The bootstrap puts `uv` in a project-local `.tools/` folder and the project
environment in `.venv/`. Nothing is installed system-wide.

## Try it without a radio (macOS / Linux)

```sh
.tools/bin/uv run radio-web-remote sim            # prints a /dev/ttys... path
.tools/bin/uv run radio-web-remote probe --port /dev/ttys004
```

## Try it with an IC-7300

Set CI-V Baud to 115200, then:

```sh
.tools/bin/uv run radio-web-remote probe --port <the radio's serial port>
```

`probe` sends TX off first, then reads frequency, mode and TX state. It never
keys the transmitter, and it holds DTR and RTS low.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
