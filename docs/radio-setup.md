# Radio setup

Menu settings each radio needs for radio-web-remote. Paths and values are as
printed in the manufacturer's manual; page numbers are the manual's own.

## IC-7300

Source: Icom IC-7300 Full Manual, section 12 "Set mode" (pages below), read
2026-11-13 from third-party reproductions (manualslib 1106166, libble 828087).
TODO(verify) against Icom's own PDF.

All items are under **MENU > SET > Connectors** unless noted.

| Setting | Set it to | Why | Default | Page |
|---------|-----------|-----|---------|------|
| CI-V USB Port | **Unlink from [REMOTE]** | USB gets its own CI-V port; needed for scope data and 115200 baud | Link to [REMOTE] | 12-8 |
| CI-V USB Baud Rate | **115200** (Auto also worked on the maintainer's radio) | Scope data output needs 115200 (p. 19-7 footnote *4) | Auto | 12-9 |
| CI-V Address | leave at **94h**, or match `civ_address` in config.yaml | The driver default is 94h | 94h | 12-8 |
| CI-V Transceive | **ON** | The radio reports front-panel tuning at once | ON | 12-8 |
| CI-V USB Echo Back | either | The driver ignores echoes; tested both ways (H7300-5) | OFF | 12-9 |
| USB SEND | **OFF** for CI-V PTT (default); DTR or RTS only if you set `ptt_method` to match | See "PTT method" below | OFF | 12-9 |
| DATA OFF MOD | **USB** to transmit browser or host audio in SSB/AM/FM voice modes | Otherwise the radio takes TX audio from the microphone | MIC,ACC | 12-8 |
| DATA MOD | **USB** to transmit browser or host audio in DATA modes | | ACC | 12-8 |
| USB MOD Level | start at **50%**, then adjust using the ALC meter | Too high over-drives ALC and distorts SSB | 50% | 12-7 |
| ACC/USB Output Select | **AF** | Receive audio over USB | AF | 12-7 |
| ACC/USB AF Output Level | **50%**, adjust to taste | Receive audio level to the computer | 50% | 12-7 |
| **MENU > SET > Function > Time-Out Timer (CI-V)** | **3 min** (recommended) | A second, radio-side limit on CI-V or [TRANSMIT] transmissions. It covers the one case the server cannot: the server dying while keyed (H7300-4 C showed the key survives that). | OFF | 12-5 |

Setting DATA OFF MOD to USB means the radio's own microphone no longer
transmits in voice modes. Change it back for local operation.

### PTT method

- **CI-V (default).** The server keys with CI-V command 1C 00. If the server
  process dies while keyed, the radio stays keyed until the restarted server
  sends TX off (tested, H7300-4). Turn on Time-Out Timer (CI-V) as a backstop.
- **DTR or RTS** keying is not implemented in 0.1 (`ptt_method` accepts only
  `civ`). Leave USB SEND at OFF. The server holds both lines low from the
  moment it opens the port anyway (tested, H7300-3).

### On the computer (macOS)

Grant the server Microphone access: System Settings > Privacy & Security >
Microphone, and turn on the app that runs the server (Terminal). Without it
macOS hands the server silence from the radio's codec, so receive audio is
silent even in listen-only mode (hardware test H7300-16). A LaunchAgent
cannot get this permission at all; see [install-macos.md](install-macos.md).

In the browser's Audio panel, pick the microphone and speaker you mean to
use. The system default may be another device (on the maintainer's Mac it
was an audio mixer). The radio's own "USB Audio CODEC" is listed but
disabled, because using it as the browser mic would loop receive audio into
transmit.

## IC-705

Source: Icom IC-705 Basic Manual A7560D-1EX-7, section 8 (Icom's PDF), and
the CI-V Reference Guide A7560-8EX-6. Checked on firmware 1.32
(docs/hardware-test.md, H705 rows).

Menu paths are under **MENU > SET > Connectors** unless noted.

| Setting | Set it to | Why | Default | Page |
|---------|-----------|-----|---------|------|
| CI-V > CI-V Address | **A4h**, or match `civ_address` (the server also finds a changed address by itself, `auto_detect_address`, on by default) | The maintainer's radio was set to 94h (the IC-7300's address, a tip in the Advanced Manual for older software) and did not answer at A4h | A4h | 8-16 |
| CI-V > CI-V Transceive | **ON** | Front-panel changes reach the page at once | ON | 8-16 |
| CI-V > CI-V USB Echo Back | either | The driver ignores echoes | OFF | 8-16 |
| MOD Input > DATA OFF MOD | **MIC,USB** (default) or USB | Browser or host audio transmits in voice modes; MIC,USB keeps the radio's own mic working too | MIC,USB | 8-15 |
| MOD Input > DATA MOD | **USB** (default) | Audio in DATA modes | USB | 8-15 |
| MOD Input > USB MOD Level | start at **50%**, adjust with the ALC meter | | 50% | 8-15 |
| USB SEND/Keying > USB SEND | **OFF** for CI-V PTT | | OFF | 8-16 |
| **MENU > SET > Function > Time-Out Timer** | **3 min** (recommended) | Radio-side limit if the server dies while keyed (a CI-V key survives that, H705-4) | 5 min | 8-4 |

The IC-705 has no CI-V baud rate or "USB port link/unlink" item. Over USB it
shows two serial ports: use **Serial Port A** (CI-V). Port B can carry GPS
sentences or other data (USB (B) Function, 8-16). With `usb_serial` matching,
the server picks port A; set `match.port` to override.

A PRESET memory can change the CI-V address and echo back (Advanced Manual
9-9). If the page stops answering after loading a preset, check the address.

## IC-9700

Source: Icom IC-9700 Basic Manual A7508H-1EX-7, sections 8 and 3 (Icom's
PDF), and the CI-V Reference Guide A7508-3EX-4. Checked on firmware 1.50
(docs/hardware-test.md, H9700 rows).

| Setting | Set it to | Why | Default | Page |
|---------|-----------|-----|---------|------|
| CI-V > CI-V USB Port | **Unlink from [REMOTE]** (default) | USB gets its own CI-V port; needed for scope data | Unlink from [REMOTE] | 8-16 |
| CI-V > CI-V USB Baud Rate | **115200** | Scope data over USB needs 115200 (CI-V guide p. 12 footnote) | Auto | 8-16 |
| CI-V > CI-V Address | **A2h**, or match `civ_address` | | A2h | 8-16 |
| CI-V > CI-V Transceive | **ON** | | ON | 8-16 |
| CI-V > CI-V USB Echo Back | either | | OFF | 8-16 |
| MOD Input > DATA OFF MOD | **MIC,USB** (or USB) | Browser or host audio transmits in voice modes. At the default MIC,ACC the radio sends a silent carrier from the page (seen in H9700-10) | MIC,ACC | 8-15 |
| MOD Input > DATA MOD | **USB** | Audio in DATA modes | ACC | 8-15 |
| MOD Input > USB MOD Level | start at **50%** | | 50% | 8-15 |
| USB SEND/Keying > USB SEND | **OFF** for CI-V PTT | | OFF | 8-15 |
| **MENU > SET > Function > Time-Out Timer** | **3 min** (recommended) | As for the IC-7300 | OFF | 8-3 |

Two virtual COM ports appear: **USB (A)** carries CI-V (Basic Manual 8-16).
"Inhibit Timer at USB Connection" (default ON, 8-16) holds off transmit for a
few seconds after the USB link comes up, so the first key-up after the
server connects may be refused by the radio.

The page's MAIN and SUB buttons choose the band the controls act on, and
on the IC-9700 that is also the band that transmits (seen on hardware,
H9700-5); in satellite mode SUB is the uplink (Basic Manual 7-1). The server
reads the radio's own transmit frequency before keying (this also covers a
repeater shift), so band limits apply to where the radio really transmits.

SUB can only be selected while the SUB receiver (Dualwatch) is on; the page's
Dualwatch button turns it on or off.
