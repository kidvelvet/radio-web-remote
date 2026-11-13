import datetime as dt
import ipaddress
import os
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

from radio_web_remote.config import Config
from radio_web_remote.security import tls


@pytest.fixture(scope="module")
def issued(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    cfg = Config.model_validate(
        {"server": {"hostnames": ["shack.example.test"], "vpn_ips": ["100.64.1.2"]}}
    )
    files, changed = tls.ensure_certificates(cfg, d)
    return d, cfg, files, changed


def load(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_first_run_creates_ca_and_server_cert(issued):
    _, _, files, changed = issued
    assert "created local CA" in changed
    assert files.cert_file.exists() and files.key_file.exists() and files.ca_cert_file.exists()


def test_server_cert_meets_apple_requirements(issued):
    _, _, files, _ = issued
    cert = load(files.cert_file)
    assert isinstance(cert.public_key(), rsa.RSAPublicKey) and cert.public_key().key_size >= 2048
    assert cert.signature_hash_algorithm.name == "sha256"
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert cert.not_valid_after_utc - cert.not_valid_before_utc <= dt.timedelta(days=825)
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False


def test_sans_include_configured_names_and_addresses(issued):
    _, _, files, _ = issued
    sans = tls.cert_sans(files.cert_file)
    assert {"localhost", "shack.example.test"} <= sans.dns
    assert ipaddress.ip_address("100.64.1.2") in sans.ips
    assert ipaddress.ip_address("127.0.0.1") in sans.ips


def test_server_cert_is_signed_by_ca(issued):
    _, _, files, _ = issued
    ca, cert = load(files.ca_cert_file), load(files.cert_file)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    ca.public_key().verify(
        cert.signature,
        cert.tbs_certificate_bytes,
        padding.PKCS1v15(),
        cert.signature_hash_algorithm,
    )
    assert cert.issuer == ca.subject


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_private_keys_are_owner_only(issued):
    d, _, files, _ = issued
    for key in (files.key_file, d / "tls" / "ca.key"):
        assert os.stat(key).st_mode & 0o077 == 0


def test_second_run_changes_nothing(issued):
    d, cfg, files, _ = issued
    before = files.cert_file.read_bytes()
    _, changed = tls.ensure_certificates(cfg, d)
    assert changed is None and files.cert_file.read_bytes() == before


def test_new_address_reissues_server_cert_but_keeps_ca(issued):
    d, cfg, files, _ = issued
    ca_before = files.ca_cert_file.read_bytes()
    cfg2 = cfg.model_copy(
        update={
            "server": cfg.server.model_copy(update={"vpn_ips": [ipaddress.ip_address("10.9.8.7")]})
        }
    )
    _, changed = tls.ensure_certificates(cfg2, d)
    assert "10.9.8.7" in changed
    sans = tls.cert_sans(files.cert_file)
    assert ipaddress.ip_address("10.9.8.7") in sans.ips
    assert ipaddress.ip_address("100.64.1.2") in sans.ips  # old names kept
    assert files.ca_cert_file.read_bytes() == ca_before


def test_user_supplied_certificate_is_used_as_is(tmp_path):
    cfg = Config.model_validate(
        {"server": {"tls": {"cert_file": "/x/c.pem", "key_file": "/x/k.pem"}}}
    )
    files, changed = tls.ensure_certificates(cfg, tmp_path)
    assert files.cert_file == Path("/x/c.pem") and files.ca_cert_file is None and changed is None
    assert not (tmp_path / "tls").exists()


def test_cert_and_key_must_be_set_together():
    with pytest.raises(ValueError):
        Config.model_validate({"server": {"tls": {"cert_file": "/x/c.pem"}}})


def test_cert_days_capped_at_825():
    with pytest.raises(ValueError):
        Config.model_validate({"server": {"tls": {"server_cert_days": 900}}})


def test_ca_is_name_constrained_to_local_names_and_private_addresses(issued):
    _, _, files, _ = issued
    ca = load(files.ca_cert_file)
    ext = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert ext.critical
    dns = {g.value for g in ext.value.permitted_subtrees if isinstance(g, x509.DNSName)}
    nets = {g.value for g in ext.value.permitted_subtrees if isinstance(g, x509.IPAddress)}
    assert {"local", "lan", "home.arpa", "localhost", "shack.example.test"} <= dns
    assert ipaddress.ip_network("192.168.0.0/16") in nets
    assert ipaddress.ip_network("100.64.0.0/10") in nets
    # Nothing public: the CA cannot vouch for, say, a bank's website.
    assert not any(d in ("com", "org", "net") for d in dns)
    assert not any(ipaddress.ip_address("8.8.8.8") in n for n in nets)


def test_server_cert_names_are_within_ca_constraints(issued):
    _, _, files, _ = issued
    assert tls.uncovered_by_ca(files.ca_cert_file, tls.cert_sans(files.cert_file)) == []


def test_public_vpn_address_is_added_to_constraints(tmp_path):
    cfg = Config.model_validate({"server": {"vpn_ips": ["203.0.113.7"]}})
    files, _ = tls.ensure_certificates(cfg, tmp_path)
    assert ipaddress.ip_address("203.0.113.7") in tls.cert_sans(files.cert_file).ips
    assert tls.uncovered_by_ca(files.ca_cert_file, tls.cert_sans(files.cert_file)) == []


def test_name_outside_constraints_is_left_out_with_a_message(tmp_path):
    tls.ensure_certificates(Config(), tmp_path)
    later = Config.model_validate({"server": {"hostnames": ["radio.example.com"]}})
    files, changed = tls.ensure_certificates(later, tmp_path)
    assert "radio.example.com" in changed and "cert reset" in changed
    assert "radio.example.com" not in tls.cert_sans(files.cert_file).dns


MAC_IFCONFIG = """en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	inet6 fe80::1c2b:3d4e:5f60:7182%en0 prefixlen 64 secured scopeid 0xe
	inet 192.168.25.114 netmask 0xffffff00 broadcast 192.168.25.255
en1: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	inet 192.168.25.116 netmask 0xffffff00 broadcast 192.168.25.255
utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
	inet 100.101.102.103 --> 100.101.102.103 netmask 0xffffffff
"""
LINUX_IP = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever
2: eth0    inet 10.0.1.20/24 brd 10.0.1.255 scope global eth0\\       valid_lft forever
2: eth0    inet6 fd00::20/64 scope global\\       valid_lft forever
"""


def test_parse_interface_addresses():
    mac = tls.parse_interface_addresses(MAC_IFCONFIG)
    assert {
        ipaddress.ip_address(a) for a in ("192.168.25.114", "192.168.25.116", "100.101.102.103")
    } <= mac
    lin = tls.parse_interface_addresses(LINUX_IP)
    assert {ipaddress.ip_address(a) for a in ("127.0.0.1", "10.0.1.20", "fd00::20")} <= lin
