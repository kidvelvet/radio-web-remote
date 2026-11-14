"""Icom radios over CI-V. Importing this package registers the drivers."""

from __future__ import annotations

from radio_web_remote.radios import registry
from radio_web_remote.radios.icom.civ_driver import IcomCivDriver
from radio_web_remote.radios.icom.ic705 import IC705
from radio_web_remote.radios.icom.ic7300 import IC7300
from radio_web_remote.radios.icom.ic9700 import IC9700
from radio_web_remote.radios.icom.model import IcomModel
from radio_web_remote.transport.base import ByteTransport

MODELS: dict[str, IcomModel] = {m.driver_name: m for m in (IC7300, IC705, IC9700)}


def _factory(model: IcomModel) -> registry.DriverFactory:
    def make(transport: ByteTransport, options: registry.DriverOptions) -> IcomCivDriver:
        return IcomCivDriver(
            model,
            transport,
            address=options.address,
            controller_address=options.controller_address,
            response_timeout_s=options.response_timeout_s,
            auto_detect_address=options.auto_detect_address,
        )

    return make


for _name, _model in MODELS.items():
    registry.register(_name, _factory(_model))

__all__ = ["IC705", "IC7300", "IC9700", "MODELS", "IcomCivDriver", "IcomModel"]
