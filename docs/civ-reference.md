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

## IC-705, IC-9700

Not yet read. Scheduled for P4. No entries are copied from the IC-7300.

### Inconsistencies inside the IC-7300 manual (as printed)

- p. 19-7 says 1C 00 status is output automatically when "CI-V Output (for ANT) (Command: 1A 05 0157)" is ON; the table lists that setting as `1A 05 0073` and 0157 as something else.
- p. 19-7, `27 00`: needs "Scope data output (Command: 27 20)" ON; the table lists scope data output as `27 11`.
- p. 19-7 footnote *4 puts 115200 on the "CI-V Baud Rate" screen; only "CI-V USB Baud Rate" (p. 12-9) offers 115200.
- p. 19-5 prints USB Echo Back as 00=ON, 01=OFF; on the maintainer's radio the item read 00 with the menu both ON and OFF (H7300-5).
