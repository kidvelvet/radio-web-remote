# Hardware test checklist

Mark each item with the date, radio firmware, host OS and result. "Not run"
means it has only been exercised against the simulator.

Do every transmit test into a dummy load, on a frequency you are licensed
for, at low power.

## IC-7300

Radio settings: CI-V Baud 115200, CI-V USB Port "Unlink from [REMOTE]".

| # | Test | How | Result |
|---|------|-----|--------|
| H7300-1 | Frequency byte order | `radio-web-remote probe --port <port>`; the MHz shown must equal the front panel | 2026-11-13, macOS, firmware not recorded: probe read 14.336000 MHz USB FIL1 RX (raw reply `03 00 60 33 14 00`). Operator confirmed the front panel showed 14.336.00. **Pass.** |
| H7300-2 | Mode and filter read | change mode/filter on the front panel, probe again | Not run |
| H7300-3 | Port open does not key | set USB SEND to DTR, then RTS; open with `probe`; radio must stay in RX | Not run |
| H7300-4 | TX off on connect | key the radio from the front panel (into dummy load), run `probe`; radio must drop to RX | Not run |
| H7300-5 | Echo Back on and off | probe with USB Echo Back ON, then OFF; both must work | Not run |
| H7300-6 | Transceive destination address | with CI-V Transceive ON, tune the VFO and capture frames; record the `to` address (00 or E0) | Not run |
| H7300-7 | Unplug while keyed | P1: key over CI-V, pull USB; record whether the radio unkeys | Not run |

### Observed on hardware

- 2026-11-13, macOS: the IC-7300 enumerates as a Silicon Labs CP2102, USB VID:PID 10C4:EA60, serial number string "IC-7300 <radio serial>", port `/dev/cu.usbserial-<location>`. The serial string names the model, which helps auto-detect (plan F11).
- 2026-11-13: two commands written back to back get only one answer; with 2 ms or more between them, both are answered. The driver therefore keeps one command on the wire at a time, and the simulator drops commands that arrive before its reply (about 3 ms).
- 2026-11-13: CI-V Baud was already 115200; no reply at 57600 or below.

## IC-705, IC-9700

Added in P4.

## iPhone, iPad

Added in P3.
