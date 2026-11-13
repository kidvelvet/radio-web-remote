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
| 7300-FM | Icom IC-7300 Full Manual, section 19 "Control Command" (pp. 19-1 to 19-10) | manualslib.com manual 1106166 (pages 154-163); libble.eu manual 828087 (pp. 19-7, 19-8), read 2026-11-13 | No. TODO(verify) against the Icom PDF from icomjapan.com/support |

## Framing (all Icom CI-V models so far)

| Item | Bytes | Source | Notes |
|------|-------|--------|-------|
| Frame | `FE FE <to> <from> <Cn> [Sc] [data] FD` | 7300-FM p. 19-2 | |
| OK reply | `FB` | 7300-FM p. 19-2 | |
| NG reply | `FA` | 7300-FM p. 19-2 | |
| Collision/jam | `FC` | not found in 7300-FM | TODO(verify). Parser drops any frame containing FC; no value we encode can contain it. |
| Transceive destination | `00`? | not found | TODO(verify). Driver accepts 00 or the controller address. |

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
| Frequency data | 5 bytes, BCD; byte 1 = 10 Hz/1 Hz, byte 2 = 1 kHz/100 Hz, byte 3 = 100 kHz/10 kHz, byte 4 = 10 MHz/1 MHz, byte 5 = 1000 MHz/100 MHz (fixed 0) | 7300-FM p. 19-8 | Higher digit in the high nibble, from the digit-label order. No worked example in the manual. TODO(verify) on hardware. |
| Mode codes | LSB 00, USB 01, AM 02, CW 03, RTTY 04, FM 05, CW-R 07, RTTY-R 08 | 7300-FM p. 19-8 | 06 not listed |
| Filter codes | FIL1 01, FIL2 02, FIL3 03 | 7300-FM pp. 19-8, 19-9 | |
| CI-V Transceive setting | `1A 05 0071`, 00 OFF / 01 ON | 7300-FM p. 19-5 | not used yet |
| CI-V Output (for ANT) | `1A 05 0073` | 7300-FM p. 19-5 | p. 19-7 cites `1A 05 0157` for this, which the table lists as something else. Internal inconsistency; TODO(verify). |
| CI-V USB port (link/unlink REMOTE) | `1A 05 0074`, read only | 7300-FM p. 19-5 | not used yet |
| USB Echo Back | `1A 05 0075`, 00 ON / 01 OFF | 7300-FM p. 19-5 | Note inverted polarity as read. TODO(verify). Not used yet. |

## IC-705, IC-9700

Not yet read. Scheduled for P4. No entries are copied from the IC-7300.
