# Third-party notices

radio-web-remote is MIT licensed. It contains no code copied from other
projects. wfview (GPL-3.0) and Hamlib (LGPL-2.1 library, GPL utilities) may
be read as behavioral references only; nothing from them is copied.

Versions are the ones pinned in `uv.lock` as of 2026-11-13 (end of P1).
Licenses are as declared on each package's PyPI page for that version.
Every runtime license below is compatible with distributing this project
under MIT.

## Runtime dependencies (installed with the server)

| Package | Version | License | Why |
|---------|---------|---------|-----|
| fastapi | 0.162.7 | MIT | HTTP and WebSocket app |
| starlette | 1.10.0 | BSD-3-Clause | via fastapi |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI server with TLS |
| websockets | 17.4 | BSD-3-Clause | WebSocket protocol for uvicorn |
| h11 | 0.16.0 | MIT | HTTP/1.1 for uvicorn |
| anyio | 4.16.0 | MIT | via starlette |
| click | 8.5.1 | BSD-3-Clause | via uvicorn |
| idna | 3.20 | BSD-3-Clause | via anyio |
| annotated-doc | 0.0.6 | MIT | via fastapi |
| opentelemetry-api | 1.45.2 | Apache-2.0 | via fastapi (API only; nothing is exported) |
| pydantic | 2.14.3 | MIT | config and message validation |
| pydantic_core | 2.50.4 | MIT | via pydantic |
| annotated-types | 0.8.0 | MIT | via pydantic |
| typing-extensions | 4.16.0 | PSF-2.0 | via pydantic |
| typing-inspection | 0.4.5 | MIT | via pydantic |
| PyYAML | 6.0.3 | MIT | config file |
| pyserial | 3.5 | BSD-3-Clause | serial ports. Last release 2020-11; stable but quiet. |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | local CA and certificates |
| cffi | 2.1.1 | MIT-0 | via cryptography, argon2-cffi-bindings |
| pycparser | 3.12 | BSD-3-Clause | via cffi |
| argon2-cffi | 25.1.0 | MIT | password hashing |
| argon2-cffi-bindings | 26.2.0 | MIT | via argon2-cffi |

## Development and test only (not installed with the server)

| Package | Version | License |
|---------|---------|---------|
| pytest | 9.2.0 | MIT |
| pytest-asyncio | 1.5.0 | Apache-2.0 |
| hypothesis | 6.170.15 | MPL-2.0 |
| sortedcontainers | 2.4.0 | Apache-2.0 |
| playwright | 1.65.1 | Apache-2.0 (browser engines it downloads carry their own licenses) |
| pytest-playwright | 0.10.0 | Apache-2.0 |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| python-slugify | 9.1.3 | MIT |
| text-unidecode | 1.3 | Artistic License (via python-slugify; test tooling only, never shipped) |
| httpx | 0.28.1 | BSD-3-Clause |
| httpcore | 1.0.9 | BSD-3-Clause |
| certifi | 2026.10.23 | MPL-2.0 |
| ruff | 0.17.5 | MIT |
| mypy | 2.4.1 | MIT |
| mypy-extensions | 1.2.0 | MIT |
| librt | 0.16.0 | MIT |
| ast-serialize | 0.13.1 | MIT |
| pathspec | 1.1.1 | MPL-2.0 |
| types-PyYAML | 6.0.12.20261113 | Apache-2.0 |
| iniconfig | 2.3.1 | MIT |
| pluggy | 1.7.0 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| Pygments | 2.21.0 | BSD-2-Clause |
| colorama | 0.4.6 | BSD |
| uv (bootstrap tool, in .tools/) | 0.13.8 | MIT OR Apache-2.0 (TODO(verify)) |
| hatchling (build backend) | 1.33.0 | MIT |

## Planned for later phases

| Package | Latest checked | License | Phase |
|---------|----------------|---------|-------|
| sounddevice | 0.5.6 | MIT | P3 |
| numpy | 2.5.4 | BSD-3-Clause and others | P3. Requires Python 3.12+, but the floor is 3.11 (Pi OS Bookworm): pin older or avoid. |
| aiortc | 1.16.0 | BSD-3-Clause | P3 spike only |

GitHub Actions used in CI, pinned by commit SHA: actions/checkout v7.0.1
(MIT), actions/setup-python v7.1.0 (MIT).
