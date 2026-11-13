"""Every capability-table entry must cite where it came from."""

import pytest

from radio_web_remote.radios import registry
from radio_web_remote.radios.base import Feature
from radio_web_remote.radios.icom import MODELS
from radio_web_remote.sources import Source


def _has_source(src: object) -> bool:
    return isinstance(src, Source) and bool(src.document.strip()) and bool(src.page.strip())


@pytest.mark.parametrize("model", MODELS.values(), ids=lambda m: m.name)
def test_every_entry_has_a_source(model):
    assert _has_source(model.default_address.source)
    for command, src in model.commands.items():
        assert _has_source(src), f"{model.name} {command.name} has no source"
    for mode, entry in model.modes.items():
        assert _has_source(entry.source), f"{model.name} mode {mode.value} has no source"
    for number, entry in model.filters.items():
        assert _has_source(entry.source), f"{model.name} FIL{number} has no source"


@pytest.mark.parametrize("model", MODELS.values(), ids=lambda m: m.name)
def test_codes_are_unique(model):
    codes = [e.value for e in model.modes.values()]
    assert len(codes) == len(set(codes))
    fcodes = [e.value for e in model.filters.values()]
    assert len(fcodes) == len(set(fcodes))


def test_registry_lists_ic7300():
    assert "icom.ic7300" in registry.names()


def test_ic7300_features():
    m = MODELS["icom.ic7300"]
    assert {Feature.FREQUENCY, Feature.MODE, Feature.FILTER, Feature.PTT} <= m.features


def test_unknown_driver():
    with pytest.raises(KeyError):
        registry.create("nope.nothing", None)  # type: ignore[arg-type]
