# Third-party notices

radio-web-remote is MIT licensed. It contains no code copied from other
projects. wfview (GPL-3.0) and Hamlib (LGPL-2.1 library, GPL utilities) may
be read as behavioral references only; nothing from them is copied.

Versions are the ones pinned in `uv.lock` as of 2026-11-13. Licenses are as
declared on each package's PyPI page (license expression or classifier).

## Runtime dependencies

| Package | Version | License | Notes |
|---------|---------|---------|-------|
| pydantic | 2.14.3 | MIT | config validation |
| pydantic-core | 2.50.4 | MIT | via pydantic |
| annotated-types | 0.8.0 | MIT | via pydantic |
| typing-extensions | 4.16.0 | PSF-2.0 | via pydantic |
| typing-inspection | 0.4.5 | MIT | via pydantic |
| PyYAML | 6.0.3 | MIT | config file |
| pyserial | 3.5 | BSD-3-Clause | serial ports. Last release 2020-11; stable but quiet. Revisit if a platform breaks. |

## Development and test only (not shipped)

| Package | Version | License |
|---------|---------|---------|
| pytest | 9.2.0 | MIT |
| pytest-asyncio | 1.5.0 | Apache-2.0 |
| hypothesis | 6.170.15 | MPL-2.0 |
| sortedcontainers | 2.4.0 | Apache-2.0 |
| iniconfig | 2.3.1 | MIT |
| pluggy | 1.7.0 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| Pygments | 2.21.0 | BSD-2-Clause |
| colorama | 0.4.6 | BSD (classifier) |
| ruff | 0.17.5 | MIT |
| mypy | 2.4.1 | MIT |
| mypy-extensions | 1.2.0 | MIT |
| librt | 0.16.0 | MIT |
| ast-serialize | 0.13.1 | MIT |
| pathspec | 1.1.1 | MPL-2.0 (classifier) |
| types-PyYAML | 6.0.12.20261113 | Apache-2.0 |
| uv (bootstrap tool) | 0.13.8 | MIT OR Apache-2.0 (TODO(verify)) |
| hatchling (build backend) | 1.33.0 | MIT |

## Planned for later phases

Checked 2026-11-13 on PyPI; added to the tables above when first used.

| Package | Latest | License | Phase |
|---------|--------|---------|-------|
| fastapi | 0.162.6 | MIT | P1 |
| uvicorn | 0.54.0 | BSD-3-Clause | P1 |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | P1 |
| argon2-cffi | 25.1.0 | MIT | P1 |
| httpx | 0.28.1 | BSD-3-Clause | P1 (tests) |
| playwright | 1.65.1 | Apache-2.0 | P1 (tests) |
| pytest-playwright | 0.10.0 | Apache-2.0 | P1 (tests) |
| sounddevice | 0.5.6 | MIT | P3 |
| numpy | 2.5.4 | BSD-3-Clause and others | P3. Note: requires Python 3.12+, but the project floor is 3.11 (Pi OS Bookworm). Pin an older numpy for 3.11 or avoid numpy. |
| aiortc | 1.16.0 | BSD-3-Clause | P3 spike only. Note: requires Python 3.11+. |

GitHub Actions used in CI, pinned by commit SHA: actions/checkout v7.0.1
(MIT), actions/setup-python v7.1.0 (MIT).
