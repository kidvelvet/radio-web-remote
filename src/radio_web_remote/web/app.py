"""FastAPI application.

Public (no login): /setup, /setup/ca.crt, /login, /api/login, /api/login-csrf,
and /static assets. Everything else needs a session cookie.

- Cookies: Secure, HttpOnly, SameSite=Strict.
- CSRF: every state-changing HTTP request carries X-CSRF-Token. For login,
  it must match a pre-login cookie (double submit); afterwards, the
  session's own CSRF token.
- WebSocket: session cookie plus an Origin that matches the Host the browser
  connected to (https only). Anything else is closed before accept.
- Strict Content-Security-Policy; no third-party origins anywhere.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hmac
import ipaddress
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from radio_web_remote import __version__
from radio_web_remote.config import Config
from radio_web_remote.security.audit import AuditLog
from radio_web_remote.security.ratelimit import RateLimiter
from radio_web_remote.security.users import LockedOut, LoginFailed, Session, UserStore
from radio_web_remote.station.radio_station import RadioStation
from radio_web_remote.station.tx_controller import StopReason
from radio_web_remote.web.hub import Hub

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SESSION_COOKIE = "rwr_session"
LOGIN_CSRF_COOKIE = "rwr_login_csrf"
AUDIT_THROTTLE_S = 60.0
AUDIT_CAP_PER_MIN = 60  # lines per minute per event (failed logins, say), all addresses together

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; media-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@dataclass
class AppContext:
    cfg: Config
    users: UserStore
    audit: AuditLog | None
    stations: dict[str, RadioStation]
    ca_cert_file: Path | None
    login_limiter: RateLimiter
    hub: Hub


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if not request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _client_ip(request: Request | WebSocket) -> str:
    return request.client.host if request.client else "unknown"


def _limit_key(ip: str) -> str:
    """Rate-limit key: the address; for a global IPv6 address its /64, since a host
    can rotate addresses inside it. An IPv4 client on a dual-stack listener shows
    as ::ffff:a.b.c.d and is keyed by its IPv4 address; private and link-local
    IPv6 addresses are keyed one by one (one /64 is often the whole LAN)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        if addr.is_global:
            return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


# Password hashing runs here, not in the default executor that other blocking
# work shares; at most this many at once, however many logins arrive.
_LOGIN_WORKERS = 2
_login_pool = concurrent.futures.ThreadPoolExecutor(_LOGIN_WORKERS, "login")


def _cookie_args() -> dict[str, Any]:
    return {"secure": True, "httponly": True, "samesite": "strict", "path": "/"}


def same_origin(origin: str | None, host: str | None) -> bool:
    """True only for https://<the exact Host the browser used>."""
    if not origin or not host:
        return False
    return hmac.compare_digest(origin.lower(), f"https://{host}".lower())


def create_app(
    cfg: Config,
    users: UserStore,
    stations: dict[str, RadioStation],
    audit: AuditLog | None = None,
    ca_cert_file: Path | None = None,
    start_stations: bool = True,
) -> FastAPI:
    ctx = AppContext(
        cfg=cfg,
        users=users,
        audit=audit,
        stations=stations,
        ca_cert_file=ca_cert_file,
        login_limiter=RateLimiter(max_events=10, window_s=300),
        hub=Hub(stations, cfg.ptt),
    )

    def audit_event(event: str, **fields: Any) -> None:
        if ctx.audit is not None:
            ctx.audit({"event": event, **fields})

    throttle: dict[str, tuple[float, int]] = {}
    caps: dict[str, dict[str, int]] = {}

    def flush_caps(minute: int) -> None:
        for event, cap in caps.items():
            if minute != cap["minute"]:
                if cap["suppressed"]:
                    audit_event(f"{event}_suppressed", count=cap["suppressed"])
                cap.update(minute=minute, count=0, suppressed=0)

    def audit_capped(event: str, **fields: Any) -> None:
        """Logged up to AUDIT_CAP_PER_MIN lines a minute per event, all addresses
        together; past that a count is logged when the minute ends."""
        minute = int(time.monotonic() // 60)
        flush_caps(minute)
        cap = caps.setdefault(event, {"minute": minute, "count": 0, "suppressed": 0})
        if cap["count"] >= AUDIT_CAP_PER_MIN:
            cap["suppressed"] += 1
            return
        cap["count"] += 1
        audit_event(event, **fields)

    def audit_throttled(event: str, ip: str, key: str | None = None, **fields: Any) -> None:
        """At most one line per event and key (default: the address) per minute,
        with a count of the rest: floods must not rotate the transmit history
        out of the audit log."""
        now, k = time.monotonic(), f"{event}|{key or _limit_key(ip)}"
        last, suppressed = throttle.get(k, (0.0, 0))
        if now - last < AUDIT_THROTTLE_S:
            throttle[k] = (last, suppressed + 1)
            return
        throttle.pop(k, None)
        throttle[k] = (now, 0)  # newest last
        while len(throttle) > 10_000:  # forget the oldest keys, not all of them
            del throttle[next(iter(throttle))]
        audit_capped(event, ip=ip, suppressed=suppressed, **fields)  # many keys: capped too

    async def flush_caps_every_minute() -> None:
        while True:  # a count is written soon after a flood ends, not at the next event
            await asyncio.sleep(60)
            flush_caps(int(time.monotonic() // 60))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if start_stations:
            for st in stations.values():
                await st.start()
        ctx.hub.start()
        flusher = asyncio.create_task(flush_caps_every_minute())
        try:
            yield
        finally:
            flusher.cancel()
            await ctx.hub.close()
            if start_stations:
                for st in stations.values():
                    await st.close()  # unkeys first

    app = FastAPI(
        title="radio-web-remote",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.ctx = ctx
    app.add_middleware(SecurityHeaders)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    async def current_session(request: Request | WebSocket) -> Session | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        return await asyncio.to_thread(users.get_session, token)

    def require_csrf(request: Request, expected: str | None) -> bool:
        got = request.headers.get("x-csrf-token", "")
        return bool(expected) and hmac.compare_digest(got, expected or "")

    # -- public ------------------------------------------------------------
    @app.get("/setup")
    async def setup_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "setup.html")

    # Read once at startup: only the public CA certificate, never a key.
    ca_pem = ca_cert_file.read_bytes() if ca_cert_file and ca_cert_file.exists() else None

    @app.get("/setup/ca.crt")
    async def ca_cert() -> Response:
        if ca_pem is None:
            return JSONResponse(
                {"error": "this server uses its own certificate; no CA to install"}, 404
            )
        return Response(
            ca_pem,
            media_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": 'attachment; filename="radio-web-remote-ca.crt"'},
        )

    @app.get("/setup/info")
    async def setup_info() -> dict[str, Any]:
        return {
            "club": cfg.site.club_name,
            "project": cfg.project.name,
            "has_ca": ca_pem is not None,
            "supported_clients": cfg.project.supported_clients,
        }

    @app.get("/login")
    async def login_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/api/login-csrf")
    async def login_csrf() -> JSONResponse:
        token = secrets.token_urlsafe(24)
        resp = JSONResponse({"csrf": token})
        resp.set_cookie(LOGIN_CSRF_COOKIE, token, max_age=600, **_cookie_args())
        return resp

    @app.post("/api/login")
    async def login(request: Request) -> JSONResponse:
        ip = _client_ip(request)
        if not require_csrf(request, request.cookies.get(LOGIN_CSRF_COOKIE)):
            return JSONResponse({"error": "csrf"}, 403)
        # Each attempt takes a slot before the password check (so a burst cannot
        # all get in at once); a successful login gives it back, so members
        # signing in from several devices are never throttled.
        key = _limit_key(ip)
        if not ctx.login_limiter.allow(key):
            retry = max(1, ctx.login_limiter.retry_after(key))
            audit_throttled("login_rate_limited", ip)
            return JSONResponse(
                {"error": "too many attempts"}, 429, headers={"Retry-After": str(retry)}
            )
        try:
            body = await request.json()
            username = str(body.get("username", ""))[:64]
            password = str(body.get("password", ""))[:256]
        except Exception:
            return JSONResponse({"error": "bad request"}, 400)
        loop = asyncio.get_running_loop()
        try:
            user = await loop.run_in_executor(_login_pool, users.verify_login, username, password)
        except LockedOut as exc:
            audit_throttled("login_locked", ip, key=username.lower(), user=username.lower())
            return JSONResponse({"error": "locked", "retry_after": exc.seconds_left}, 423)
        except LoginFailed:
            audit_capped("login_failed", user=username.lower(), ip=ip)
            return JSONResponse({"error": "wrong username or password"}, 401)
        ctx.login_limiter.refund(key)
        token, sess = await loop.run_in_executor(_login_pool, users.create_session, user, ip)
        await loop.run_in_executor(_login_pool, users.purge_expired)
        audit_event(
            "login", user=user.username, role=user.role.value, ip=ip, session=sess.public_id
        )
        resp = JSONResponse({"ok": True})
        resp.set_cookie(SESSION_COOKIE, token, **_cookie_args())
        resp.delete_cookie(LOGIN_CSRF_COOKIE, path="/")
        return resp

    # -- authenticated -------------------------------------------------------
    @app.get("/")
    async def index(request: Request) -> Response:
        if await current_session(request) is None:
            return RedirectResponse("/login", 303)
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/me")
    async def me(request: Request) -> JSONResponse:
        sess = await current_session(request)
        if sess is None:
            return JSONResponse({"error": "login required"}, 401)
        u = sess.user
        return JSONResponse(
            {
                "username": u.username,
                "role": u.role.value,
                "callsign": u.callsign,
                "csrf": sess.csrf,
                "club": cfg.site.club_name,
                "radios": [{"id": rid, "model": st.state.model} for rid, st in stations.items()],
                "ptt": {
                    "heartbeat_interval_s": cfg.ptt.heartbeat_interval_s,
                    "tx_timeout_s": cfg.ptt.tx_timeout_s,
                },
            }
        )

    @app.post("/api/logout")
    async def logout(request: Request) -> JSONResponse:
        sess = await current_session(request)
        if sess is None:
            return JSONResponse({"error": "login required"}, 401)
        if not require_csrf(request, sess.csrf):
            return JSONResponse({"error": "csrf"}, 403)
        token = request.cookies.get(SESSION_COOKIE, "")
        await ctx.hub.end_session(sess.public_id, StopReason.LOGGED_OUT)
        await asyncio.to_thread(users.end_session, token)
        audit_event("logout", user=sess.user.username, session=sess.public_id)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        if not same_origin(websocket.headers.get("origin"), websocket.headers.get("host")):
            audit_throttled("ws_rejected", _client_ip(websocket), reason="origin")
            await websocket.close(code=1008)
            return
        sess = await current_session(websocket)
        if sess is None:
            await websocket.close(code=1008)
            return
        radio_id = websocket.query_params.get("radio") or next(iter(stations), "")
        if radio_id not in stations:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await ctx.hub.serve(websocket, sess, radio_id, revalidate=current_session)

    return app
