from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from radio_web_remote.config import TX_TIMEOUT_CEILING_S, Config, load_config

ROOT = Path(__file__).resolve().parents[2]


def test_defaults_match_brief():
    cfg = Config()
    assert cfg.server.https_port == 8473
    assert cfg.server.http_redirect.enabled is False
    assert cfg.site.club_name == "WWT Amateur Radio Club"
    assert cfg.project.name == "radio-web-remote"
    assert cfg.project.license == "MIT"
    assert cfg.ptt.tx_timeout_s == TX_TIMEOUT_CEILING_S == 180
    assert cfg.ptt.heartbeat_timeout_s == 2.0
    assert cfg.radios == []


def test_example_file_equals_defaults():
    with open(ROOT / "config.example.yaml", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    assert Config.model_validate(raw) == Config()


def test_tx_timeout_cannot_be_raised():
    with pytest.raises(ValidationError):
        Config.model_validate({"ptt": {"tx_timeout_s": 181}})
    with pytest.raises(ValidationError):
        Config.model_validate({"ptt": {"tx_timeout_s": 0}})
    assert Config.model_validate({"ptt": {"tx_timeout_s": 60}}).ptt.tx_timeout_s == 60


def test_heartbeat_timeout_must_cover_two_intervals():
    with pytest.raises(ValidationError):
        Config.model_validate({"ptt": {"heartbeat_interval_s": 1.0, "heartbeat_timeout_s": 1.5}})


def test_unknown_keys_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate({"server": {"htps_port": 1}})


def test_radio_addresses_from_yaml(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(
        'radios:\n  - id: shack\n    civ_address: "94"\n    controller_address: "E0h"\n',
        encoding="utf-8",
    )
    r = load_config(f).radios[0]
    assert r.civ_address == 0x94 and r.controller_address == 0xE0
    assert r.driver == "icom.ic7300" and r.ptt_method == "civ" and r.baud == 115200


def test_bare_int_address_rejected(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("radios:\n  - id: shack\n    civ_address: 94\n", encoding="utf-8")
    with pytest.raises(ValueError, match="quoted hex"):
        load_config(f)


def test_duplicate_radio_ids():
    with pytest.raises(ValidationError):
        Config.model_validate({"radios": [{"id": "a"}, {"id": "a"}]})


def test_bad_radio_id():
    with pytest.raises(ValidationError):
        Config.model_validate({"radios": [{"id": "Has Spaces"}]})


def test_empty_file_gives_defaults(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("", encoding="utf-8")
    assert load_config(f) == Config()


# -- review round 1 (docs/review-changelog.md) ---------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"server": {"hostnames": ["https://radio.lan"]}},
        {"server": {"hostnames": ["10.0.0.1"]}},  # addresses go in vpn_ips
        {"server": {"hostnames": ["ràdio"]}},
        {"server": {"https_port": 8443, "http_redirect": {"enabled": True, "port": 8443}}},
        {"radios": [{"id": "r", "ptt_method": "dtr"}]},  # not implemented
        {"radios": [{"id": "r", "civ_address": "E0"}]},  # same as the controller
        {"radios": [{"id": "r", "audio": {"input_device": True}}]},
        {"radios": [{"id": "r", "audio": {"output_device": -1}}]},
    ],
)
def test_values_that_would_break_the_server_are_rejected(raw):
    with pytest.raises(ValidationError):
        Config.model_validate(raw)


def test_blank_cert_paths_mean_unset():
    cfg = Config.model_validate({"server": {"tls": {"cert_file": "", "key_file": " "}}})
    assert cfg.server.tls.cert_file is None and cfg.server.tls.key_file is None
    with pytest.raises(ValidationError):  # one without the other is still an error
        Config.model_validate({"server": {"tls": {"cert_file": "a.pem", "key_file": ""}}})


def test_yaml_syntax_error_is_a_config_error(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("server:\n  https_port: [\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        load_config(f)


def test_data_dir_config_is_found(tmp_path, monkeypatch):
    """Review E2-6: --data-dir X uses X/config.yaml when no other is given."""
    from radio_web_remote.config import find_config_file

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RADIO_WEB_REMOTE_CONFIG", raising=False)
    d = tmp_path / "data"
    d.mkdir()
    (d / "config.yaml").write_text("server:\n  https_port: 9443\n")
    assert find_config_file(None, d) == d / "config.yaml"
    assert load_config(None, d).server.https_port == 9443


def test_config_errors_name_the_field(tmp_path):
    """Review E2-7: not just '1 validation error for Config'."""
    from radio_web_remote.config import ConfigError

    f = tmp_path / "c.yaml"
    f.write_text("server:\n  https_port: 99999\n")
    with pytest.raises(ConfigError, match=r"server\.https_port"):
        load_config(f)


def test_named_data_dir_wins_over_a_stray_config(tmp_path, monkeypatch):
    """Review E3-9, E3-10."""
    from radio_web_remote.config import ConfigError, find_config_file

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RADIO_WEB_REMOTE_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text("radios:\n  - id: shack\n    civ_address: 94\n")
    d = tmp_path / "data"
    d.mkdir()
    (d / "config.yaml").write_text("server:\n  https_port: 9443\n")
    assert find_config_file(None, d) == d / "config.yaml"
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path / "config.yaml")
    assert str(info.value).count("config.yaml") == 1  # the file named once
