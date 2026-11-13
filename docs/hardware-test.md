# Hardware test checklist

Mark each item with the date, radio firmware, host OS and result. "Not run"
means it has only been exercised against the simulator.

Do every transmit test into a dummy load, on a frequency you are licensed
for, at low power.

## IC-7300

Radio settings: CI-V Baud 115200, CI-V USB Port "Unlink from [REMOTE]".

| # | Test | How | Result |
|---|------|-----|--------|
| H7300-1 | Frequency byte order | `radio-web-remote probe --port <port>`; the MHz shown must equal the front panel | Not run |
| H7300-2 | Mode and filter read | change mode/filter on the front panel, probe again | Not run |
| H7300-3 | Port open does not key | set USB SEND to DTR, then RTS; open with `probe`; radio must stay in RX | Not run |
| H7300-4 | TX off on connect | key the radio from the front panel (into dummy load), run `probe`; radio must drop to RX | Not run |
| H7300-5 | Echo Back on and off | probe with USB Echo Back ON, then OFF; both must work | Not run |
| H7300-6 | Transceive destination address | with CI-V Transceive ON, tune the VFO and capture frames; record the `to` address (00 or E0) | Not run |
| H7300-7 | Unplug while keyed | P1: key over CI-V, pull USB; record whether the radio unkeys | Not run |

## IC-705, IC-9700

Added in P4.

## iPhone, iPad

Added in P3.
