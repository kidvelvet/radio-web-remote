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
