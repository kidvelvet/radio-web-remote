"""Local certificate authority and server certificate.

First run creates a root CA and a server certificate signed by it. Clients
install the CA once (from /setup); the server certificate can be re-issued
for new names or addresses without reinstalling anything on clients.

Certificate rules follow Apple's "Requirements for trusted certificates in
iOS 13 and macOS 10.15" (support.apple.com/en-us/103769): RSA keys of at
least 2048 bits, SHA-2 signatures, the server's names in Subject Alternative
Name, ExtendedKeyUsage with serverAuth, and at most 825 days of validity.

Private keys are written with mode 0600 and never leave the data folder.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import ipaddress
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from radio_web_remote.config import Config

CA_DAYS = 3650
CA_KEY_BITS = 3072
SERVER_KEY_BITS = 2048
RENEW_WITHIN_DAYS = 30

IPAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class TlsFiles:
    cert_file: Path
    key_file: Path
    ca_cert_file: Path | None  # None when the user supplied their own certificate


@dataclass(frozen=True)
class SanSet:
    dns: frozenset[str]
    ips: frozenset[IPAddr]

    def describe(self) -> str:
        return ", ".join(sorted(self.dns) + sorted(str(i) for i in self.ips))


def tls_dir(data_dir: Path) -> Path:
    return data_dir / "tls"


def local_addresses() -> set[IPAddr]:
    """Best-effort list of this host's addresses, without extra dependencies."""
    found: set[IPAddr] = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None):
            with contextlib.suppress(ValueError):
                found.add(ipaddress.ip_address(str(info[4][0]).split("%")[0]))
    # The address of the default route: connect() on UDP sends nothing.
    for family, probe in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1")):
        with contextlib.suppress(OSError), socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((probe, 9))
            found.add(ipaddress.ip_address(s.getsockname()[0].split("%")[0]))
    return {a for a in found if not a.is_link_local and not a.is_unspecified}


def wanted_sans(cfg: Config, addresses: set[IPAddr] | None = None) -> SanSet:
    host = socket.gethostname().split(".")[0].lower()
    dns = {"localhost", host, f"{host}.local"} | {h.lower() for h in cfg.server.hostnames}
    ips = set(local_addresses() if addresses is None else addresses) | set(cfg.server.vpn_ips)
    return SanSet(frozenset(d for d in dns if d), frozenset(ips))


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def create_ca(directory: Path, club_name: str) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=CA_KEY_BITS)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "radio-web-remote local CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, club_name[:64] or "radio-web-remote"),
        ]
    )
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "ca.crt", directory / "ca.key"
    _write_private(key_path, _key_pem(key))
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def create_server_cert(directory: Path, sans: SanSet, days: int) -> tuple[Path, Path]:
    ca_cert = x509.load_pem_x509_certificate((directory / "ca.crt").read_bytes())
    ca_key = serialization.load_pem_private_key((directory / "ca.key").read_bytes(), password=None)
    assert isinstance(ca_key, rsa.RSAPrivateKey)
    key = rsa.generate_private_key(public_exponent=65537, key_size=SERVER_KEY_BITS)
    first_dns = sorted(sans.dns - {"localhost"}) or ["localhost"]
    general: list[x509.GeneralName] = [x509.DNSName(d) for d in sorted(sans.dns)]
    general += [x509.IPAddress(i) for i in sorted(sans.ips, key=str)]
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, first_dns[0])]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(general), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = directory / "server.crt", directory / "server.key"
    _write_private(key_path, _key_pem(key))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def cert_sans(cert_file: Path) -> SanSet:
    cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return SanSet(
        frozenset(d.lower() for d in ext.get_values_for_type(x509.DNSName)),
        frozenset(
            i
            for i in ext.get_values_for_type(x509.IPAddress)
            if isinstance(i, ipaddress.IPv4Address | ipaddress.IPv6Address)
        ),
    )


def needs_new_server_cert(cert_file: Path, sans: SanSet) -> str | None:
    """Reason to re-issue, or None if the current certificate is fine."""
    if not cert_file.exists():
        return "no server certificate yet"
    cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    if cert.not_valid_after_utc - _now() < dt.timedelta(days=RENEW_WITHIN_DAYS):
        return f"expires {cert.not_valid_after_utc:%Y-%m-%d}"
    have = cert_sans(cert_file)
    missing = (sans.dns - have.dns, sans.ips - have.ips)
    if missing[0] or missing[1]:
        names = sorted(missing[0]) + sorted(str(i) for i in missing[1])
        return "new names or addresses: " + ", ".join(names)
    return None


def ensure_certificates(cfg: Config, data_dir: Path) -> tuple[TlsFiles, str | None]:
    """Return the files to serve, creating or renewing as needed, and what changed."""
    if cfg.server.tls.cert_file and cfg.server.tls.key_file:
        return TlsFiles(Path(cfg.server.tls.cert_file), Path(cfg.server.tls.key_file), None), None
    d = tls_dir(data_dir)
    changed: list[str] = []
    if not (d / "ca.crt").exists() or not (d / "ca.key").exists():
        create_ca(d, cfg.site.club_name)
        changed.append("created local CA")
    sans = wanted_sans(cfg)
    reason = needs_new_server_cert(d / "server.crt", sans)
    if reason or "created local CA" in changed:
        # Keep names the old certificate had, so a briefly missing interface
        # does not drop an address clients already use.
        if (d / "server.crt").exists() and not changed:
            old = cert_sans(d / "server.crt")
            sans = SanSet(sans.dns | old.dns, sans.ips | old.ips)
        create_server_cert(d, sans, cfg.server.tls.server_cert_days)
        changed.append(f"issued server certificate ({reason or 'new CA'}) for {sans.describe()}")
    return TlsFiles(d / "server.crt", d / "server.key", d / "ca.crt"), "; ".join(changed) or None
