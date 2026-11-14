"""Configuration: one YAML file, validated by pydantic.

Every default lives here and is mirrored, with comments, in
config.example.yaml. tests/unit/test_config.py checks that the example file
and these defaults agree, so the two cannot drift apart.
"""

from __future__ import annotations

import ipaddress
import os
import re
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from radio_web_remote import paths

# 97.213(b) limits transmission to 3 minutes if the control link malfunctions.
# The TX timeout may be lowered by an admin but never raised past this.
# See docs/adr/0001-architecture.md, decision D6.
TX_TIMEOUT_CEILING_S = 180.0

_RADIO_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectConfig(_Strict):
    name: str = "radio-web-remote"
    github_owner: str = ""
    license: str = "MIT"
    # Informational: shown on the /setup page. Not used to gate anything.
    supported_hosts: list[str] = Field(
        default_factory=lambda: [
            "Linux (incl. Raspberry Pi OS 64-bit)",
            "Windows 10/11",
            "macOS",
        ]
    )
    supported_clients: list[str] = Field(
        default_factory=lambda: [
            "Chrome",
            "Edge",
            "Firefox",
            "Safari (macOS)",
            "Safari on iOS/iPadOS",
        ]
    )


class SiteConfig(_Strict):
    club_name: str = "WWT Amateur Radio Club"


class HttpRedirectConfig(_Strict):
    """Optional plain-HTTP listener that only redirects to HTTPS. Off by default."""

    enabled: bool = False
    port: Annotated[int, Field(ge=1, le=65535)] = 8080


class TlsConfig(_Strict):
    """Leave cert_file/key_file empty to use the built-in local CA (created on first run)."""

    cert_file: str | None = None
    key_file: str | None = None
    # Apple rejects TLS server certificates valid for more than 825 days
    # (support.apple.com/en-us/103769). 397 leaves room under that.
    server_cert_days: Annotated[int, Field(ge=30, le=825)] = 397

    @field_validator("cert_file", "key_file", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _both_or_neither(self) -> TlsConfig:
        if (self.cert_file is None) != (self.key_file is None):
            raise ValueError("server.tls.cert_file and key_file must be set together")
        return self


_LABEL = r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(\.{_LABEL})*$")


def _looks_like_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


class ServerConfig(_Strict):
    bind_address: str = "0.0.0.0"  # noqa: S104  (all interfaces is the documented default)
    https_port: Annotated[int, Field(ge=1, le=65535)] = 8473
    http_redirect: HttpRedirectConfig = Field(default_factory=HttpRedirectConfig)
    # Extra names and addresses to put in the server certificate's SANs.
    hostnames: list[str] = Field(default_factory=list)
    vpn_ips: list[IPv4Address | IPv6Address] = Field(default_factory=list)
    tls: TlsConfig = Field(default_factory=TlsConfig)

    @field_validator("hostnames")
    @classmethod
    def _hostnames(cls, names: list[str]) -> list[str]:
        for n in names:
            if not _HOSTNAME.match(n) or _looks_like_ip(n):
                raise ValueError(
                    f"hostname {n!r}: use a DNS name (letters, digits, '-', dots); "
                    "put addresses in vpn_ips"
                )
        return names

    @model_validator(mode="after")
    def _ports_differ(self) -> ServerConfig:
        if self.http_redirect.enabled and self.http_redirect.port == self.https_port:
            raise ValueError("http_redirect.port must differ from https_port")
        return self


class PttConfig(_Strict):
    heartbeat_interval_s: Annotated[float, Field(ge=0.1, le=1.0)] = 0.5
    heartbeat_timeout_s: Annotated[float, Field(ge=0.5, le=10.0)] = 2.0
    tx_timeout_s: Annotated[float, Field(gt=0, le=TX_TIMEOUT_CEILING_S)] = TX_TIMEOUT_CEILING_S

    @model_validator(mode="after")
    def _timeout_covers_heartbeats(self) -> PttConfig:
        if self.heartbeat_timeout_s < 2 * self.heartbeat_interval_s:
            raise ValueError(
                "ptt.heartbeat_timeout_s must be at least twice ptt.heartbeat_interval_s"
            )
        return self


class SerialMatch(_Strict):
    """How to find the radio's CI-V serial port.

    Prefer usb_serial or usb_location: COM numbers and /dev/tty* names change
    when the cable moves to another USB port. All empty: the one connected
    radio of the configured model (radio-web-remote doctor names it).
    """

    usb_serial: str | None = None
    usb_location: str | None = None
    port: str | None = None


def _device_ref(v: Any) -> Any:
    if isinstance(v, bool):
        raise ValueError("audio device: give a name, an index from `audio devices`, or auto")
    if isinstance(v, int) and v < 0:
        raise ValueError("audio device index must be 0 or more")
    return v


DeviceRef = Annotated[str | int | None, BeforeValidator(_device_ref)]


class RadioAudioConfig(_Strict):
    """The radio's USB audio codec.

    "auto" picks the only device named "USB Audio CODEC" (how the IC-7300
    codec appears on macOS and, per the brief, Windows). With more than one
    radio on a host, name the device (a name substring) or give its index
    from `radio-web-remote audio devices`.
    """

    enabled: bool = True
    input_device: DeviceRef = "auto"  # radio -> computer (receive audio)
    output_device: DeviceRef = "auto"  # computer -> radio (transmit audio)


class HostAudioConfig(_Strict):
    """This computer's own microphone and speakers, for the "host" routing mode.

    Leave both null to disable host mode.
    """

    input_device: DeviceRef = None
    output_device: DeviceRef = None


class RadioConfig(_Strict):
    id: str
    driver: str = "icom.ic7300"
    match: SerialMatch = Field(default_factory=SerialMatch)
    baud: Literal[4800, 9600, 19200, 38400, 57600, 115200] = 115200
    # None means "use the driver's documented default address". Bounds are only
    # "a byte that cannot be confused with framing" (FC-FF are reserved, 00 is
    # broadcast); the radio menu decides which values it actually accepts.
    civ_address: Annotated[int, Field(ge=0x01, le=0xFB)] | None = None
    controller_address: Annotated[int, Field(ge=0x01, le=0xFB)] = 0xE0
    # Silent at civ_address? Find the radio by one broadcast read ID and use
    # the address that answers (a menu setting or PRESET can change it, H705-1).
    auto_detect_address: bool = True
    # Only CI-V PTT is implemented in 0.1; DTR/RTS keying is planned.
    ptt_method: Literal["civ"] = "civ"
    audio: RadioAudioConfig = Field(default_factory=RadioAudioConfig)

    @field_validator("id")
    @classmethod
    def _id_is_slug(cls, v: str) -> str:
        if not _RADIO_ID.match(v):
            raise ValueError("radio id must be lowercase letters, digits, '-' or '_' (max 32)")
        return v

    @model_validator(mode="after")
    def _distinct_addresses(self) -> RadioConfig:
        if self.civ_address is not None and self.civ_address == self.controller_address:
            raise ValueError("civ_address and controller_address must differ")
        return self

    @field_validator("ptt_method", mode="before")
    @classmethod
    def _ptt_method(cls, v: Any) -> Any:
        if v in ("dtr", "rts"):
            raise ValueError("ptt_method dtr/rts is not implemented yet; use civ")
        return v

    @field_validator("civ_address", "controller_address", mode="before")
    @classmethod
    def _parse_hex(cls, v: Any) -> Any:
        # Strings are always hex, as Icom writes addresses: "94", "0x94", "94h".
        if isinstance(v, str):
            return int(v.strip().removesuffix("h").removesuffix("H"), 16)
        return v


class Config(_Strict):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    site: SiteConfig = Field(default_factory=SiteConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    ptt: PttConfig = Field(default_factory=PttConfig)
    host_audio: HostAudioConfig = Field(default_factory=HostAudioConfig)
    radios: list[RadioConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_radio_ids(self) -> Config:
        ids = [r.id for r in self.radios]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate radio ids: {', '.join(dupes)}")
        return self


ENV_CONFIG = "RADIO_WEB_REMOTE_CONFIG"


class ConfigError(ValueError):
    """The configuration cannot be used (file unreadable, not YAML, invalid)."""


def _format_validation(exc: ValidationError) -> str:
    parts = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        parts.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return "; ".join(parts)


def find_config_file(
    explicit: str | os.PathLike[str] | None = None, data_dir: Path | None = None
) -> Path | None:
    """Return the config file to use, or None to run on defaults.

    Order: explicit argument, $RADIO_WEB_REMOTE_CONFIG, then ./config.yaml and
    <data dir>/config.yaml. A --data-dir given on the command line comes before
    ./config.yaml: naming the folder means its settings.
    """
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env)
    here = Path("config.yaml")
    candidates = (
        (here, paths.data_dir() / "config.yaml")
        if data_dir is None
        else (
            data_dir / "config.yaml",
            here,
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config(path: str | os.PathLike[str] | None = None, data_dir: Path | None = None) -> Config:
    """Load and validate configuration. A missing explicit path is an error.

    Every problem with the file raises ConfigError, with the file and the field."""
    file = find_config_file(path, data_dir)
    if file is None:
        return Config()
    try:
        with open(file, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"{file}: {exc.strerror or exc}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{file}: not valid YAML: {exc}") from None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{file}: top level must be a mapping")
    try:
        _reject_bare_int_addresses(file, raw)
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{file}: {_format_validation(exc)}") from None
    except ValueError as exc:
        raise ConfigError(f"{file}: {exc}") from None


def _reject_bare_int_addresses(file: Path, raw: dict[str, Any]) -> None:
    """YAML reads `civ_address: 94` as decimal 94 (0x5E), a silent misconfiguration.

    Icom documents addresses in hex, so the file must spell them as quoted hex.
    """
    radios = raw.get("radios") or []
    if not isinstance(radios, list):
        return
    for i, radio in enumerate(radios):
        if not isinstance(radio, dict):
            continue
        for key in ("civ_address", "controller_address"):
            value = radio.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                raise ValueError(
                    f"radios[{i}].{key}: write CI-V addresses as quoted hex, "
                    f'for example {key}: "94"'
                )
