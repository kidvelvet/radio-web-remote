# Contributing

Thanks for helping. This program keys real transmitters, so the bar for
correctness is high and the process is a little strict.

## Setup (no admin rights needed)

macOS or Linux:

```bash
./scripts/bootstrap.sh
```

Windows (PowerShell):

```powershell
.\scripts\bootstrap.ps1
```

Then run everything CI runs:

```bash
.tools/bin/uv run ruff check . && .tools/bin/uv run ruff format --check .
.tools/bin/uv run mypy
.tools/bin/uv run pytest
.tools/bin/uv run playwright install chromium firefox webkit
.tools/bin/uv run pytest -m e2e --browser chromium --browser firefox --browser webkit
```

`radio-web-remote serve --simulate` runs the whole server against a
simulated radio, no hardware needed.

## Rules

1. **No invented radio facts.** Every CI-V command, value range, menu path
   and regulatory statement cites its source: the manufacturer's document
   and the page number printed on the page, or an observation on hardware
   recorded in [docs/hardware-test.md](docs/hardware-test.md). Each table
   entry in `src/radio_web_remote/radios/icom/*.py` carries a `Source`, and
   [docs/civ-reference.md](docs/civ-reference.md) lists it. If you cannot
   find a source, write `TODO(verify): ...` and leave the feature out for
   that model. Never copy an entry from one model to another.
2. **Do not copy manufacturer manuals** into the repository. Icom's download
   terms forbid redistribution; cite pages and state single facts in your
   own words.
3. **Do not copy code from GPL projects** (wfview, Hamlib utilities). Reading
   their docs and observing radio behaviour is fine. Record any third-party
   code in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
4. **Safety stays on the server.** Never rely on the browser to unkey. A
   change to PTT, the TX controller or the fail-safes needs a test per
   trigger (`tests/integration/test_tx_controller.py`).
5. **Hardware checks are recorded.** Before claiming something works on a
   radio, run it and add a dated row to docs/hardware-test.md: radio,
   firmware, host OS, what was done, what happened. Transmit tests go into a
   dummy load at low power; on an antenna use a clear simplex frequency,
   minimum power, and identify.
6. **Tests with the code.** Each commit carries the tests for what it
   changes. CI runs Linux (Python 3.11 to 3.13), Windows, macOS, three
   browsers and the install scripts.

## Adding a radio

Add a capability table (`radios/<vendor>/<model>.py`) built only from that
model's own documents, a simulator profile, builder tests against the
documented bytes, and hardware-test rows. The core in `radios/base.py` is
vendor-neutral; a new protocol goes in `protocols/<name>/` next to `civ/`.

## Style

Commit messages: a short prefix (`civ:`, `tx:`, `web:`, `ui:`, `docs:`,
`packaging:`), then what and why. In docs, plain sentences, no em dashes.
