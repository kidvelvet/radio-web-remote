"""Assemble and run the server: config, users, certificates, stations, HTTPS."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from radio_web_remote.config import Config, RadioConfig
from radio_web_remote.radios.icom import MODELS
from radio_web_remote.security.audit import AuditLog
from radio_web_remote.security.tls import TlsFiles, ensure_certificates
from radio_web_remote.security.users import UserStore
from radio_web_remote.sim.icom import IcomSimulator, loopback_transport
from radio_web_remote.station.radio_station import RadioStation
from radio_web_remote.transport.base import ByteTransport
from radio_web_remote.web.app import create_app

log = logging.getLogger(__name__)


class NoAdmin(RuntimeError):
    pass


@dataclass
class Runtime:
    app: FastAPI
    tls: TlsFiles
    tls_changed: str | None
    stations: dict[str, RadioStation]
    users: UserStore
    audit: AuditLog


def users_db(data_dir: Path) -> Path:
    return data_dir / "users.db"


def _sim_factory(sim: IcomSimulator) -> ByteTransport:
    return loopback_transport(sim)


def build_runtime(cfg: Config, data_dir: Path, simulate: bool = False) -> Runtime:
    users = UserStore(users_db(data_dir))
    if users.count_admins() == 0:
        raise NoAdmin("No admin account yet. Run: radio-web-remote init")
    audit = AuditLog(data_dir / "logs" / "audit.log")
    tls, changed = ensure_certificates(cfg, data_dir)

    radios = list(cfg.radios)
    if simulate and not radios:
        radios = [RadioConfig(id="sim-ic7300", driver="icom.ic7300")]
    stations: dict[str, RadioStation] = {}
    for rc in radios:
        factory = None
        if simulate:
            model = MODELS.get(rc.driver)
            if model is None:
                raise ValueError(f"no simulator for driver {rc.driver!r}")
            sim = IcomSimulator(model)

            def factory(sim: IcomSimulator = sim) -> ByteTransport:
                return _sim_factory(sim)

        stations[rc.id] = RadioStation(rc, cfg.ptt, transport_factory=factory, audit=audit)
    app = create_app(cfg, users, stations, audit=audit, ca_cert_file=tls.ca_cert_file)
    return Runtime(app, tls, changed, stations, users, audit)


def serve(cfg: Config, data_dir: Path, simulate: bool = False, log_level: str = "info") -> None:
    rt = build_runtime(cfg, data_dir, simulate=simulate)
    if rt.tls_changed:
        log.warning("TLS: %s", rt.tls_changed)
    https = uvicorn.Server(
        uvicorn.Config(
            rt.app,
            host=cfg.server.bind_address,
            port=cfg.server.https_port,
            ssl_certfile=str(rt.tls.cert_file),
            ssl_keyfile=str(rt.tls.key_file),
            log_level=log_level,
            proxy_headers=False,
            server_header=False,
            ws="websockets",
            ws_max_size=64 * 1024,
            ws_ping_interval=20,
            lifespan="on",
        )
    )
    servers = [https]
    if cfg.server.http_redirect.enabled:
        servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    _make_redirect(cfg.server.https_port),
                    host=cfg.server.bind_address,
                    port=cfg.server.http_redirect.port,
                    log_level=log_level,
                    server_header=False,
                    lifespan="off",
                )
            )
        )

    async def run_all() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    try:
        asyncio.run(run_all())
    finally:
        rt.audit.close()


def _make_redirect(https_port: int) -> FastAPI:
    from fastapi import Request
    from fastapi.responses import RedirectResponse

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def redirect(request: Request, path: str) -> RedirectResponse:
        host = (request.headers.get("host") or "localhost").split(":")[0]
        return RedirectResponse(f"https://{host}:{https_port}/{path}", status_code=308)

    return app
