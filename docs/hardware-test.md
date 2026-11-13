# Hardware test checklist

Mark each item with the date, radio firmware, host OS and result. "Not run"
means it has only been exercised against the simulator.

Do every transmit test into a dummy load, on a frequency you are licensed
for, at low power.

## IC-7300

Radio settings: CI-V Baud 115200, CI-V USB Port "Unlink from [REMOTE]".

Maintainer's radio: firmware **1.42** (reported by the operator 2026-11-13). Results marked "firmware not recorded" below are from this radio and firmware.

| # | Test | How | Result |
|---|------|-----|--------|
| H7300-1 | Frequency byte order | `radio-web-remote probe --port <port>`; the MHz shown must equal the front panel | 2026-11-13, macOS, firmware not recorded: probe read 14.336000 MHz USB FIL1 RX (raw reply `03 00 60 33 14 00`). Operator confirmed the front panel showed 14.336.00. **Pass.** |
| H7300-2 | Mode and filter read | change mode/filter on the front panel while polling `04` | 2026-11-13, macOS: **Pass.** All eight modes read back with the documented codes (LSB 00, USB 01, AM 02, CW 03, RTTY 04, FM 05, CW-R 07, RTTY-R 08); FIL1/2/3 read back as 01/02/03 in USB. |
| H7300-3 | Port open does not key | set USB SEND to DTR, then RTS; open the port with SerialTransport and hold 3 s; radio must stay in RX. Control: raise the line for 1 s, radio must key | 2026-11-13, macOS: **Pass.** DTR and RTS both: RX with the port open; control key went TX on line high and RX on line low, so the setting was live. |
| H7300-4 | TX off on connect | (B) key with front-panel TRANSMIT, send CI-V TX off; (C) a process keys over CI-V and crashes, then the next `connect()` must unkey | 2026-11-13, macOS: **Pass after fix.** First run read FAIL: the status read came 17 ms after TX off, while the radio was still switching. Follow-up: (B) CI-V TX off clears a front-panel TX; RX on every read from about 50 ms. (C) TX **persisted** after the process died and the port closed; the next connect read TX at 14 ms and RX by 67 ms. The driver's unkey confirmation read too early and could falsely report failure; fixed to poll for up to 0.5 s. Rerun of the fixed loop on the radio, 3 cycles of key 0.3 s then `unkey()`: RX confirmed in 28 to 29 ms each time, one TX off frame per cycle, no resends. |
| H7300-5 | Echo Back on and off | connect, read frequency, mode, TX with USB Echo Back ON, then OFF | 2026-11-13, macOS: **Pass.** ON: 4 echoes seen and dropped, reads correct. OFF: 0 echoes, reads correct. But CI-V item `1A 05 0075` read `00` with the menu both ON and OFF, so that item number does not match this radio's firmware (version not recorded). |
| H7300-6 | Transceive destination address | with CI-V Transceive ON, tune the VFO and capture frames; record the `to` address (00 or E0) | 2026-11-13, macOS: **Pass.** CI-V Transceive read as ON (`1A 05 0071` = 01). Every unsolicited frame was addressed to **00h**: command 01 for mode/filter changes, 00 for frequency. |
| H7300-7 | Unplug while keyed | key from the browser, pull USB, replug | 2026-11-13, macOS: **Radio stayed in TX** after USB was pulled (CI-V PTT). The server saw the serial error at once and dropped the holder; its TX off could not reach the radio. It retried, and after replug reconnected and confirmed RX: about 43 s keyed with no control link. Backstops: radio Time-Out Timer (CI-V), set to 3 min on this radio, and the operator. Since then, every client shows a TX-off-not-confirmed warning and the audit log records `tx_off_unconfirmed` / `tx_off_confirmed` with the delay. |
| H7300-8 | Level byte order | read `14 01`, `14 02`, `14 03`, `14 0A` and compare with the knobs | 2026-11-13, macOS: **Pass.** AF `00 01`, RF gain `02 55` (knob at max), squelch `00 45`, RF power `00 39` = 15% with the radio set to about 15 W. Most-significant byte first, as assumed. Also read: Time-Out Timer (CI-V) `01` = 3 min, AGC `01`, preamp `00`, ATT `00`, DATA `00 00`. |
| H7300-9 | Time-Out Timer (CI-V) | set it to 3 min in the radio menu; key with `radio-web-remote probe`-style raw CI-V (no server, so no server-side timeout) into a dummy load; confirm the radio drops TX on its own at 3 min | Not run (optional) |
| H7300-10 | Browser control on the real radio | `serve` with the radio configured; from a browser: tune, turn VFO, mode/filter/DATA, RF power | 2026-11-13, macOS, Chromium: **Pass.** Read 14.300.000 USB matched; +100 Hz followed; front-panel VFO 14.300.100 -> 14.304.000 reached the browser; LSB/FIL2 and back; DATA on/off; RF power 39 -> 26 -> 39 (restored). Operator confirmed each on the radio. |
| H7300-11 | Browser PTT into dummy load | hold PTT in the browser for 2 s | 2026-11-13: **Pass.** ON AIR shown 0.06 s after press, Receive 0.06 s after release; radio keyed and unkeyed (operator confirmed). USB with no audio, so Po 0%, SWR 1.0, ALC 0%: a carrier test for meter readings is still to do. |
| H7300-12 | Fail-safe: browser killed while keyed | key from the browser (toggle), SIGKILL the browser | 2026-11-13: **Pass.** Server unkeyed 0.06 s after the kill (the OS closed the socket; audit reason `connection_closed`); operator confirmed RX. The heartbeat-timeout path is covered by H7300-13 and the automated tests. |
| H7300-13 | Fail-safe: network drop while keyed | key from a phone on Wi-Fi, turn on airplane mode: radio unkeys within about 2 s (heartbeat timeout) | Not run: needs a phone with the CA installed; do with the P3 iPhone pass |
| H7300-14 | Scope frame format | turn on 27 11 for 3 s and capture; capture one header in each scope mode; restore settings | 2026-11-13, macOS: **Pass, and settled the manual's gaps.** Division numbers are BCD (max arrives as 11h); 9 divisions of 50 points then 25 (475); one byte per point, so binary; about 4 sweeps/s (45 frames/s) to E0. Header is mode + two 5-byte frequencies + out-of-range: Center gave center 14.304 MHz and span 50 kHz (half-width: edges 14.254 to 14.354); Fixed gave edges 14.100 to 14.350. Modes 02 and 03 (not in the manual) also carry edges. Settings must be read with the 00 selector (bare 27 14 gets NG). Radio left in mode 02 with data output off, as found. Real frames are the unit-test fixture tests/fixtures/ic7300_scope.json. |
| H7300-15 | Scope in the browser on the real radio | serve with the radio; scope on; compare with the radio's screen; tap to tune; span, mode, ref, speed; 60 s soak | 2026-11-13, macOS, Chromium: **Pass.** Edges matched (14.254 to 14.354 MHz in mode 02); Center +/-25 kHz gave 14.279 to 14.329 on both; tap at 3/4 tuned to 14.317.000 (14.279 + 0.75 x 0.050, 1 kHz step); ref -10 dB and SLOW shown on the radio. Soak: 4.7 scope lines/s to the browser for 60 s while the VFO was spun (42 frequencies followed), no timeouts or link drops. Original scope settings restored afterwards. |

### Observed on hardware

- 2026-11-13, macOS: the IC-7300 enumerates as a Silicon Labs CP2102, USB VID:PID 10C4:EA60, serial number string "IC-7300 <radio serial>", port `/dev/cu.usbserial-<location>`. The serial string names the model, which helps auto-detect (plan F11).
- 2026-11-13: two commands written back to back get only one answer; with 2 ms or more between them, both are answered. The driver therefore keeps one command on the wire at a time, and the simulator drops commands that arrive before its reply (about 3 ms).
- 2026-11-13: CI-V Baud was already 115200; no reply at 57600 or below.
- 2026-11-13, later: after the H7300-5 follow-up (settings restored by the operator), the radio stopped answering CI-V at every baud rate while the USB serial port stayed present. Likely the radio was switched off (its USB chip stays powered) or a CI-V setting changed. Recheck before H7300-8.
- 2026-11-13 (H7300-6): a fast VFO spin produced about 10 transceive frequency frames per second (every 30 to 40 ms), 0 junk bytes and 0 collisions over about 2,000 frames. The UI must coalesce these, not redraw on each.
- 2026-11-13 (H7300-4): TX off is acknowledged at once, but the radio keeps reporting TX for roughly 15 to 65 ms. Anything that confirms an unkey must poll, not read once. The simulator models this (tx_release_s, 40 ms).
- 2026-11-13 (H7300-4 C): a key made over CI-V survives the controlling process dying and the serial port closing. Only a new TX off clears it. This confirms the residual risk in ADR 0001 D6: the service manager must restart the server, and the radio's own time-out timer should be on.
- 2026-11-13 (H7300-5): `1A 05` menu item numbers from the manual cannot be trusted on this firmware; see docs/civ-reference.md.
- 2026-11-13 (H7300-2): one front-panel mode change (RTTY to USB) was seen by polling but no transceive frame for it arrived, most likely because it coincided with a poll. Transceive alone is not enough; keep the slow poll (ADR D4).

## IC-705, IC-9700

Added in P4.

## iPhone, iPad

Added in P3.
