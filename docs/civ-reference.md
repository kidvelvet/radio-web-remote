# CI-V reference used by this project

Every CI-V fact in the code is listed here with its source. The code carries
the same citation in `src/radio_web_remote/radios/icom/<model>.py`, and
`tests/unit/test_capabilities.py` fails if an entry has no source.

"Primary checked" means read in the manufacturer's own PDF (not a
third-party copy) or confirmed on hardware. Until then the row is a
`TODO(verify)`.

## Documents

| Short name | Document | Copy read | Primary checked |
|------------|----------|-----------|-----------------|
| 7300-FM | Icom IC-7300 Full Manual, section 19 "Control Command" (pp. 19-1 to 19-13) and section 12 "Set mode" | manualslib.com manual 1106166 (pages 154-163); libble.eu manual 828087 (pp. 19-7, 19-8), read 2026-11-13 | No. TODO(verify) against the Icom PDF from icomjapan.com/support |
| 705-CIV | Icom IC-705 CI-V Reference Guide, A7560-8EX-6, Jan. 2023 | Icom's PDF, icomjapan.com/support/manual/3064, read 2026-11-13 | Yes |
| 705-BM | Icom IC-705 Basic Manual, A7560D-1EX-7, Sep. 2023 | Icom's PDF, icomeurope.com, read 2026-11-13 | Yes |
| 705-AM | Icom IC-705 Advanced Manual, A7560-7EX-5, Apr. 2026 | Icom's PDF, icomjapan.com/support/manual/3063, read 2026-11-13 | Yes |
| 9700-CIV | Icom IC-9700 CI-V Reference Guide, A7508-3EX-4, Mar. 2023 | Icom's PDF, icomjapan.com/support/manual/2161, read 2026-11-13 | Yes |
| 9700-BM | Icom IC-9700 Basic Manual, A7508H-1EX-7, Jan. 2026 | Icom's PDF, icomjapan.com/support/manual/2156, read 2026-11-13 | Yes |
| 9700-AM | Icom IC-9700 Advanced Manual, A7508-2EX-3, May 2026 | Icom's PDF, icomjapan.com/support/manual/2166, read 2026-11-13 | Yes |

Icom's download terms allow individual use and forbid redistributing the
manuals or their contents. This project never stores the PDFs; it cites
page numbers and states individual facts (command bytes, value ranges) in
its own words. Get the PDFs from Icom to check an entry.

## Framing (all Icom CI-V models so far)

| Item | Bytes | Source | Notes |
|------|-------|--------|-------|
| Frame | `FE FE <to> <from> <Cn> [Sc] [data] FD` | 7300-FM p. 19-2 | |
| OK reply | `FB` | 7300-FM p. 19-2 | |
| NG reply | `FA` | 7300-FM p. 19-2 | |
| Collision/jam | `FC` | not found in 7300-FM | TODO(verify). Parser drops any frame containing FC; no value we encode can contain it. |
| Transceive destination | `00` | observed on IC-7300 hardware, 2026-11-13 (H7300-6) | Not in the manual pages read. Driver also accepts the controller address, in case other models differ. |
| Command pacing | one command at a time | observed on IC-7300 hardware, 2026-11-13 | Back-to-back commands: only one answered. Reply arrives about 3 ms after the command at 115200. |
| TX release time | TX state reads TX for ~15 to 65 ms after TX off is acked | observed on IC-7300 hardware, 2026-11-13 (H7300-4) | Unkey confirmation polls up to 0.5 s. |
| CI-V key after controller death | TX persists | observed on IC-7300 hardware, 2026-11-13 (H7300-4 C) | Only a new TX off clears it. |

## IC-7300

| Function | Bytes | Source | Notes |
|----------|-------|--------|-------|
| Default CI-V address | `94` | 7300-FM p. 19-2 (address used in the frame examples) | TODO(verify) menu default in the Icom PDF |
| Send frequency (transceive) | `00` + 5 freq bytes | 7300-FM p. 19-3, 19-8 | radio to controller |
| Send mode (transceive) | `01` + mode [+ filter] | 7300-FM p. 19-3, 19-8 | filter omitted means FIL1 (p. 19-8) |
| Read operating frequency | `03` | 7300-FM p. 19-3, 19-8 | reply `03` + 5 freq bytes |
| Read operating mode | `04` | 7300-FM p. 19-3, 19-8 | reply `04` + mode + filter |
| Set operating frequency | `05` + 5 freq bytes | 7300-FM p. 19-3, 19-8 | |
| Set operating mode | `06` + mode [+ filter] | 7300-FM p. 19-3, 19-8 | filter omitted means the mode's default filter |
| TX/RX status, read | `1C 00` | 7300-FM p. 19-7 | reply `1C 00 00` (RX) or `1C 00 01` (TX) |
| TX/RX status, set | `1C 00 00` RX, `1C 00 01` TX | 7300-FM p. 19-7 | 00/01 sit in the Data column |
| Frequency data | 5 bytes, BCD; byte 1 = 10 Hz/1 Hz, byte 2 = 1 kHz/100 Hz, byte 3 = 100 kHz/10 kHz, byte 4 = 10 MHz/1 MHz, byte 5 = 1000 MHz/100 MHz (fixed 0) | 7300-FM p. 19-8 | Higher digit in the high nibble, from the digit-label order. No worked example in the manual. Confirmed on IC-7300 hardware 2026-11-13 (H7300-1): reply `00 60 33 14 00` = 14.336.000 Hz, matching the front panel. |
| Mode codes | LSB 00, USB 01, AM 02, CW 03, RTTY 04, FM 05, CW-R 07, RTTY-R 08 | 7300-FM p. 19-8 | 06 not listed |
| Filter codes | FIL1 01, FIL2 02, FIL3 03 | 7300-FM pp. 19-8, 19-9 | |
| AF level | `14 01` + 2 bytes | 7300-FM p. 19-3 | 0000=min to 0255=max. Used. |
| RF gain | `14 02` + 2 bytes | 7300-FM p. 19-3 | 0000=min to 0255=max. Used. |
| Squelch | `14 03` + 2 bytes | 7300-FM p. 19-3 | 0000=min to 0255=max. Used. |
| RF power control | `14 0A` + 2 bytes | 7300-FM p. 19-3 | 0000=max CCW to 0255=max CW. A control position, not watts. Used, and for per-user power limits. |
| Level byte layout | 2 BCD bytes, most significant pair first | 7300-FM p. 19-2 (BCD), p. 19-3 ("0000 to 0255") | Byte order not stated in words. Confirmed on IC-7300 hardware 2026-11-13 (H7300-8): RF gain at max read `02 55`; RF power read `00 39` (15%) with the radio set to about 15 W. |
| S-meter | `15 02`, read | 7300-FM p. 19-3 | 0000=S0, 0120=S9, 0241=S9+60 dB. Between points the driver interpolates linearly (an approximation; the manual gives only these points). |
| Po meter | `15 11`, read | 7300-FM p. 19-3 | 0000=0%, 0143=50%, 0213=100%. Percent only; no watts given. |
| SWR meter | `15 12`, read | 7300-FM p. 19-3 | 0000=1.0, 0048=1.5, 0080=2.0, 0120=3.0. Above 0120 the driver reports "over 3.0" rather than a number. |
| ALC meter | `15 13`, read | 7300-FM p. 19-3 | 0000=Min. to 0120=Max. |
| DATA mode | `1A 06` + on/off + filter | 7300-FM p. 19-6 (row), p. 19-9 (layout) | 00=OFF, 01=ON, then 01-03 filter. "When 00 is set, also set 00" as the filter. Used. |
| Time-Out Timer (CI-V) | `1A 05 0029` | 7300-FM p. 19-4 | 00=OFF, 01=3 min, 02=5, 03=10, 04=20, 05=30 min. Read `01` on the maintainer's radio after it was set to 3 min (2026-11-13), so this item number does match that firmware, unlike 0075. Not sent by this app; set it in the menu (docs/radio-setup.md). |
| Not used yet (P1 later or P2+) | `11` ATT (00/20), `16 02` preamp (00-02), `16 12` AGC (00-03), `16 22` NB, `16 40` NR, `16 41` auto notch, `16 48` manual notch, `14 0D` notch position, `14 06` NR level, `14 12` NB level, `21 00/01/02` RIT freq / RIT on / ΔTX on, `0F` split, `07` VFO A/B, `1C 03` TX frequency, `25`/`26` selected/unselected VFO, `27 10/11` scope on / data output | 7300-FM pp. 19-3 to 19-11 | Collected 2026-11-13 with page citations; each gets a builder test before it is used. No XIT frequency command found, only ΔTX on/off. |
| CI-V Transceive setting | `1A 05 0071`, 00 OFF / 01 ON | 7300-FM p. 19-5 | Read `01` with Transceive ON (H7300-6), but not checked with it OFF. Same firmware caveat as Echo Back. Not used yet. |
| CI-V Output (for ANT) | `1A 05 0073` | 7300-FM p. 19-5 | p. 19-7 cites `1A 05 0157` for this, which the table lists as something else. Internal inconsistency; TODO(verify). |
| CI-V USB port (link/unlink REMOTE) | `1A 05 0074`, read only | 7300-FM p. 19-5 | not used yet |
| USB Echo Back | `1A 05 0075`, 00 ON / 01 OFF | 7300-FM p. 19-5 | **Does not match hardware.** On the maintainer's IC-7300 (firmware not recorded) it read `00` with the menu both ON and OFF (H7300-5). Menu item numbers may shift between firmware versions. Do not use any `1A 05` item until checked on the firmware in use. |

## IC-705

Read in Icom's own PDFs. Code: `src/radio_web_remote/radios/icom/ic705.py`.
Not yet checked on hardware (docs/hardware-test.md, IC-705 rows).

| Function | Bytes | Source | Notes |
|----------|-------|--------|-------|
| Default CI-V address | `A4` | 705-CIV p. 2; 705-BM 8-16 | |
| Frequency, mode (transceive, read, set) | `00`, `01`, `03`, `04`, `05`, `06` | 705-CIV pp. 3, 17 | Same command numbers as the IC-7300, read separately here |
| Frequency format | 5 bytes BCD; 1 GHz digit fixed 0, 100 MHz digit 0 to 4 | 705-CIV p. 17 | So the driver refuses 500 MHz and above |
| Mode codes | LSB 00, USB 01, AM 02, CW 03, RTTY 04, FM 05, WFM 06, CW-R 07, RTTY-R 08, DV 17 | 705-CIV pp. 17, 27 | |
| Filters | FIL1 to FIL3, `01` to `03` | 705-CIV pp. 17, 27 | |
| TX state / PTT | `1C 00` + 00 RX / 01 TX | 705-CIV p. 14 | |
| Read TX frequency | `1C 03` | 705-CIV pp. 14, 17 | Used by the TX limit check (split) |
| AF, RF gain, squelch, RF power | `14 01`, `14 02`, `14 03`, `14 0A`, 0000 to 0255 | 705-CIV pp. 3, 4 | Most significant pair first, confirmed on hardware (H705-3) |
| S, Po, SWR, ALC meters | `15 02`, `15 11`, `15 12`, `15 13` | 705-CIV p. 4 | Calibration points as printed there |
| DATA mode | `1A 06` + 00/01 + filter | 705-CIV pp. 14, 22 | |
| Scope data | `27 00`; 475 points, 0 to 160; 11 divisions over USB, 1 over WLAN | 705-CIV pp. 15, 28 | Hardware (H705-6): division numbers BCD; 9 x 50 + 25 points; Scroll-C header carries edges |
| Scope on, data output | `27 10`, `27 11`, no selector byte | 705-CIV p. 15 | |
| Scope mode | `27 14 00` + Center 00 / Fixed 01 / Scroll-C 02 / Scroll-F 03 | 705-CIV p. 15 | |
| Span, edge, hold | `27 15 00`, `27 16 00` + edge 01 to 04, `27 17 00` | 705-CIV pp. 15, 16, 29 | Four fixed edges |
| Reference level, speed | `27 19 00`, `27 1A 00` | 705-CIV pp. 16, 29 | Same ranges as listed for the IC-7300 |
| CI-V menu | Address, Transceive, USB Echo Back only | 705-BM 8-16 | No CI-V baud rate and no USB port link/unlink item |
| Time-Out Timer | `1A 05 0043`: OFF, 3, 5, 10, 20, 30 min; default 5 min | 705-CIV p. 6; 705-BM 8-4 | Read 02 (5 min) on firmware 1.32, matching the menu (H705-8) |
| Read ID | `19 00`, reply `19 00` + ID | 705-CIV p. 5 | Hardware: ID A4; answered a broadcast (to 00) from the radio's set address (H705-1). Used by `auto_detect_address` |
| Split, unselected VFO | `0F 00`/`0F 01`; `25 01` + frequency | 705-CIV pp. 3, 15, 27 | Used only in the hardware test (H705-5); not in the table |
| USB serial ports | "IC-705 Serial Port A (CI-V)" and "Serial Port B" | 705-CIV p. 2; 705-BM 8-16 | Port A carries CI-V |

USB (H705-1, H705-7, macOS): VID:PID 0C26:0036, two serial ports with the
same serial string (port A CI-V, port B GPS); the codec is two devices named
"USB Audio CODEC", one per direction, 48 kHz.

Open: FC collision code; transceive destination address; whether `1C 00`
is broadcast. A PRESET memory can change
the CI-V address and Echo Back (705-AM 9-9).

## IC-9700

Read in Icom's own PDFs. Code: `src/radio_web_remote/radios/icom/ic9700.py`.
Checked on firmware 1.50 (docs/hardware-test.md, H9700 rows).

| Function | Bytes | Source | Notes |
|----------|-------|--------|-------|
| Default CI-V address | `A2` | 9700-CIV p. 2; 9700-BM 8-16 | |
| Frequency, mode (transceive, read, set) | `00`, `01`, `03`, `04`, `05`, `06` | 9700-CIV pp. 4, 13 | Act on the selected band: not stated in the guide, confirmed on hardware (H9700-2) |
| Frequency format | 5 bytes BCD; byte 5 holds the 1 GHz digit (0, 1) and the 100 MHz digit (0 to 4) | 9700-CIV p. 13 | 1296.1 MHz encodes as `00 00 10 96 12` |
| Mode codes | LSB 00, USB 01, AM 02, CW 03, RTTY 04, FM 05, CW-R 07, RTTY-R 08, DV 17, DD 22 | 9700-CIV p. 13 | No 06. DD only on 1200 MHz and not in satellite mode |
| Filters | FIL1 to FIL3 | 9700-CIV p. 13 | None for DV and DD |
| Select MAIN / SUB, read selection | `07 D0`, `07 D1`, `07 D2` (+ 00/01) | 9700-CIV p. 4 | Read replies `07 D2 00`/`01` (H9700-3). `07 D1` is refused while Dualwatch is off (H9700-2) |
| Exchange MAIN and SUB | `07 B0` | 9700-CIV p. 4 | |
| Dualwatch, satellite mode | `16 59`, `16 5A`, 00/01 | 9700-CIV p. 5 | |
| Which band transmits | Manual: "only the Main band" except split; SUB in satellite mode | 9700-BM 3-2, 7-1 | **Hardware: the selected band transmits** (H9700-5). `1C 03` reported the real TX frequency for MAIN, SUB, satellite and a repeater shift, so the TX limit check reads it |
| TX state / PTT; read TX frequency | `1C 00`; `1C 03` | 9700-CIV pp. 11, 13 | |
| Levels and meters | `14 01/02/03/0A`, `15 02/11/12/13` | 9700-CIV pp. 4, 5 | Same calibration points as printed for the IC-705; AF and squelch act on the selected band (H9700-2) |
| Split / duplex | `0F` read: 00 split off, 01 on, 11 DUP-, 12 DUP+, 13 RPS; set 10 simplex | 9700-CIV p. 4 | Used only by the hardware test; the TX check relies on `1C 03`, which includes the shift (H9700-6) |
| DATA mode | `1A 06` | 9700-CIV pp. 11, 18 | |
| Scope data | `27 00`, byte 1 = 00 Main / 01 Sub scope; 475 points, 0 to 160; 11 divisions over USB | 9700-CIV pp. 12, 25 | |
| Scope band | `27 12` 00 Main / 01 Sub | 9700-CIV p. 12 | The driver sets it with the selected band and uses it as the 27 1x selector |
| Scope settings | `27 14` to `27 1A` with a 00/01 band selector; four modes, four edges | 9700-CIV pp. 12, 25, 26 | No single/dual command (27 13) is listed |
| Coverage, USA version | 144-148, 430-450, 1240-1300 MHz | 9700-BM 11-1 | For band shortcuts only |
| Time-Out Timer | `1A 05 0041`: OFF (default), 3 to 30 min | 9700-CIV p. 6; 9700-BM 8-3 | |

USB (H9700-1): two CP2102N bridges, 10C4:EA60, serial strings ending A and
B; A carries CI-V. The codec is two devices named "USB Audio CODEC" (one per
direction) on macOS.

Open: USB audio content with Dualwatch on; transceive destination address. Reading the SUB band without
selecting it is not possible (`25`/`26` are MAIN only, 9700-CIV p. 24), so the
page shows the selected band only.

### Inconsistencies inside the IC-7300 manual (as printed)

- p. 19-7 says 1C 00 status is output automatically when "CI-V Output (for ANT) (Command: 1A 05 0157)" is ON; the table lists that setting as `1A 05 0073` and 0157 as something else.
- p. 19-7, `27 00`: needs "Scope data output (Command: 27 20)" ON; the table lists scope data output as `27 11`.
- p. 19-7 footnote *4 puts 115200 on the "CI-V Baud Rate" screen; only "CI-V USB Baud Rate" (p. 12-9) offers 115200.
- p. 19-5 prints USB Echo Back as 00=ON, 01=OFF; on the maintainer's radio the item read 00 with the menu both ON and OFF (H7300-5).
