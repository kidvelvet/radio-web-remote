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
import re
import socket
import subprocess
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
IPNet = ipaddress.IPv4Network | ipaddress.IPv6Network

# The local CA may only vouch for local names and private addresses (X.509
# Name Constraints, RFC 5280 4.2.1.10). If its key were ever stolen, it still
# could not be used to impersonate public websites to devices that trust it.
LOCAL_DNS_SUFFIXES = ("local", "lan", "home.arpa", "internal", "localhost")
PRIVATE_NETS: tuple[IPNet, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",  # RFC 1918
        "100.64.0.0/10",  # RFC 6598 shared space (used by some VPNs, e.g. Tailscale)
        "127.0.0.0/8",
        "::1/128",
        "fc00::/7",  # loopback, IPv6 unique local
    )
)


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


_ADDR = re.compile(r"\binet6?\s+(?:addr:)?([0-9a-fA-F:.]+)(?:%\w+)?")


def parse_interface_addresses(text: str) -> set[IPAddr]:
    """Addresses from `ifconfig` or `ip -o addr` output (both use 'inet'/'inet6')."""
    out: set[IPAddr] = set()
    for m in _ADDR.finditer(text):
        with contextlib.suppress(ValueError):
            out.add(ipaddress.ip_address(m.group(1).split("/")[0]))
    return out


def _interface_addresses() -> set[IPAddr]:
    for cmd in (["ip", "-o", "addr"], ["ifconfig"], ["ifconfig", "-a"]):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)  # noqa: S603
            if r.returncode == 0 and r.stdout:
                return parse_interface_addresses(r.stdout)
    return set()


def local_addresses() -> set[IPAddr]:
    """This host's addresses, without extra dependencies: every interface (from ip/ifconfig),
    the hostname's addresses, and the default-route address."""
    found: set[IPAddr] = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    found |= _interface_addresses()
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


def ca_constraints(sans: SanSet) -> x509.NameConstraints:
    dns = set(LOCAL_DNS_SUFFIXES) | {d for d in sans.dns if d}
    nets: set[IPNet] = set(PRIVATE_NETS)
    for ip in sans.ips:  # e.g. a VPN address outside the private ranges
        if not any(ip in n for n in PRIVATE_NETS):
            nets.add(ipaddress.ip_network(ip))
    permitted: list[x509.GeneralName] = [x509.DNSName(d) for d in sorted(dns)]
    permitted += [x509.IPAddress(n) for n in sorted(nets, key=str)]
    return x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None)


def uncovered_by_ca(ca_file: Path, sans: SanSet) -> list[str]:
    """Names in `sans` that the CA's name constraints do not permit ([] if unconstrained)."""
    ca = x509.load_pem_x509_certificate(ca_file.read_bytes())
    try:
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return []
    subtrees = nc.permitted_subtrees or []
    dns_ok = [g.value.lower() for g in subtrees if isinstance(g, x509.DNSName)]
    nets_ok = [
        g.value
        for g in subtrees
        if isinstance(g, x509.IPAddress)
        and isinstance(g.value, ipaddress.IPv4Network | ipaddress.IPv6Network)
    ]
    bad = [d for d in sans.dns if not any(d == c or d.endswith("." + c) for c in dns_ok)]
    bad += [str(ip) for ip in sans.ips if not any(ip in n for n in nets_ok)]
    return sorted(bad)


def create_ca(directory: Path, club_name: str, sans: SanSet | None = None) -> tuple[Path, Path]:
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
        .add_extension(ca_constraints(sans or SanSet(frozenset(), frozenset())), critical=True)
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
    sans = wanted_sans(cfg)
    if not (d / "ca.crt").exists() or not (d / "ca.key").exists():
        create_ca(d, cfg.site.club_name, sans)
        changed.append("created local CA")
    outside = uncovered_by_ca(d / "ca.crt", sans)
    if outside:
        # Issuing them would give clients a certificate they must reject.
        sans = SanSet(
            frozenset(x for x in sans.dns if x not in outside),
            frozenset(x for x in sans.ips if str(x) not in outside),
        )
        changed.append(
            "left out names the local CA may not vouch for: "
            + ", ".join(outside)
            + " (run 'radio-web-remote cert reset' to make a new CA that covers them)"
        )
    reason = needs_new_server_cert(d / "server.crt", sans)
    if reason or "created local CA" in changed:
        # Keep names the old certificate had, so a briefly missing interface
        # does not drop an address clients already use.
        if (d / "server.crt").exists() and "created local CA" not in changed:
            old = cert_sans(d / "server.crt")
            sans = SanSet(sans.dns | old.dns, sans.ips | old.ips)
        create_server_cert(d, sans, cfg.server.tls.server_cert_days)
        changed.append(f"issued server certificate ({reason or 'new CA'}) for {sans.describe()}")
    return TlsFiles(d / "server.crt", d / "server.key", d / "ca.crt"), "; ".join(changed) or None
