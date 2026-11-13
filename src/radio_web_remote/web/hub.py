"""Control WebSocket connections: one per browser tab.

Each connection is its own TX identity (session public id plus a counter),
so closing one tab unkeys only if that tab holds PTT.

Client -> server (JSON):
  {"type": "heartbeat"}
  {"type": "ptt", "down": true | false}
  {"type": "visibility", "hidden": true | false}
  {"type": "set_frequency", "hz": 14074000}
  {"type": "set_mode", "mode": "USB", "filter": 1 | null}

Server -> client (JSON):
  {"type": "hello", "conn": ..., "user": {...}, "radio": "<id>"}
  {"type": "state", "state": {...}}        latest radio state, coalesced
  {"type": "error", "code": ..., "message": ...}

State updates are coalesced per connection: only the newest state waits to
be sent, so a fast VFO spin (about 10 frames/s on an IC-7300) cannot build a
backlog on a slow phone link.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from radio_web_remote.config import PttConfig
from radio_web_remote.radios.base import Mode, ModeSetting, RadioError
from radio_web_remote.security.users import Session
from radio_web_remote.station.radio_station import ControlDenied, RadioState, RadioStation
from radio_web_remote.station.tx_controller import Operator, StopReason, TxDenied

log = logging.getLogger(__name__)

MAX_MESSAGE = 4096
IDLE_CLOSE_S = 10.0  # no message at all for this long: the client is gone
REVALIDATE_S = 30.0
MAX_HZ = 10_000_000_000

Revalidate = Callable[[WebSocket], Awaitable[Session | None]]


class _Conn:
    def __init__(self, ws: WebSocket, op: Operator, public_id: str, radio_id: str) -> None:
        self.ws = ws
        self.op = op
        self.public_id = public_id
        self.radio_id = radio_id
        self.pending_state: dict[str, Any] | None = None
        self.messages: list[dict[str, Any]] = []
        self.wake = asyncio.Event()

    def push_state(self, state: dict[str, Any]) -> None:
        self.pending_state = state
        self.wake.set()

    def push(self, msg: dict[str, Any]) -> None:
        if len(self.messages) < 50:
            self.messages.append(msg)
        self.wake.set()


class Hub:
    def __init__(self, stations: dict[str, RadioStation], ptt: PttConfig) -> None:
        self.stations = stations
        self.ptt = ptt
        self._conns: dict[str, _Conn] = {}
        self._ids = itertools.count(1)
        self._listeners: dict[str, Callable[[RadioState], None]] = {}

    def start(self) -> None:
        for rid, st in self.stations.items():
            if rid in self._listeners:
                continue

            def on_state(state: RadioState, rid: str = rid) -> None:
                data = state.to_json()
                for c in self._conns.values():
                    if c.radio_id == rid:
                        c.push_state(data)

            self._listeners[rid] = on_state
            st.add_listener(on_state)

    async def close(self) -> None:
        for rid, cb in self._listeners.items():
            self.stations[rid].remove_listener(cb)
        self._listeners.clear()
        for c in list(self._conns.values()):
            with contextlib.suppress(Exception):
                await c.ws.close(code=1001)

    @property
    def connection_count(self) -> int:
        return len(self._conns)

    async def end_session(self, public_id: str, reason: StopReason) -> None:
        """Logout or disable: unkey and close every connection of that session."""
        for c in [c for c in self._conns.values() if c.public_id == public_id]:
            await self.stations[c.radio_id].tx.client_gone(c.op.session_id, reason)
            with contextlib.suppress(Exception):
                await c.ws.close(code=4001)

    # -- one connection ------------------------------------------------------
    async def serve(
        self, ws: WebSocket, sess: Session, radio_id: str, revalidate: Revalidate
    ) -> None:
        u = sess.user
        conn_id = f"{sess.public_id}:{next(self._ids)}"
        op = Operator(conn_id, u.username, u.callsign, u.role, u.limits)
        conn = _Conn(ws, op, sess.public_id, radio_id)
        station = self.stations[radio_id]
        self._conns[conn_id] = conn
        conn.push(
            {
                "type": "hello",
                "conn": conn_id,
                "radio": radio_id,
                "user": {"username": u.username, "role": u.role.value, "callsign": u.callsign},
                "may_transmit": op.may_transmit,
                "heartbeat_interval_s": self.ptt.heartbeat_interval_s,
            }
        )
        conn.push_state(station.state.to_json())
        sender = asyncio.create_task(self._sender(conn))
        checker = asyncio.create_task(self._revalidate(conn, revalidate))
        try:
            await self._receive(conn, station)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("control connection %s failed", conn_id)
        finally:
            self._conns.pop(conn_id, None)
            # Server-side fail-safe: a closed connection never keeps the transmitter.
            # Shielded: this handler is often being cancelled when we get here.
            await asyncio.shield(station.tx.client_gone(conn_id, StopReason.CONNECTION_CLOSED))
            for t in (sender, checker):
                t.cancel()
            with contextlib.suppress(Exception):
                if ws.client_state is WebSocketState.CONNECTED:
                    await ws.close()

    async def _sender(self, conn: _Conn) -> None:
        while True:
            await conn.wake.wait()
            conn.wake.clear()
            msgs, conn.messages = conn.messages, []
            state, conn.pending_state = conn.pending_state, None
            for m in msgs:
                await conn.ws.send_text(json.dumps(m))
            if state is not None:
                await conn.ws.send_text(json.dumps({"type": "state", "state": state}))

    async def _revalidate(self, conn: _Conn, revalidate: Revalidate) -> None:
        while True:
            await asyncio.sleep(REVALIDATE_S)
            if await revalidate(conn.ws) is None:
                await self.stations[conn.radio_id].tx.client_gone(
                    conn.op.session_id, StopReason.LOGGED_OUT
                )
                await conn.ws.close(code=4001)
                return

    async def _receive(self, conn: _Conn, station: RadioStation) -> None:
        while True:
            try:
                text = await asyncio.wait_for(conn.ws.receive_text(), IDLE_CLOSE_S)
            except TimeoutError:
                await conn.ws.close(code=4000)
                return
            if len(text) > MAX_MESSAGE:
                conn.push({"type": "error", "code": "too_big", "message": "message too large"})
                continue
            try:
                msg = json.loads(text)
                if not isinstance(msg, dict):
                    raise ValueError("not an object")
            except ValueError:
                conn.push({"type": "error", "code": "bad_json", "message": "invalid JSON"})
                continue
            await self._handle(conn, station, msg)

    async def _handle(self, conn: _Conn, station: RadioStation, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        op = conn.op
        tx = station.tx
        try:
            if kind == "heartbeat":
                tx.heartbeat(op)
            elif kind == "ptt":
                if msg.get("down") is True:
                    await tx.ptt_down(op)
                else:
                    await tx.ptt_up(op)
            elif kind == "visibility":
                if msg.get("hidden") is True:
                    await tx.client_gone(op.session_id, StopReason.HIDDEN)
            elif kind == "set_frequency":
                hz = msg.get("hz")
                if not isinstance(hz, int) or isinstance(hz, bool) or not 0 < hz < MAX_HZ:
                    raise ValueError("hz must be a positive integer")
                await station.set_frequency(op, hz)
            elif kind == "set_mode":
                mode = Mode(str(msg.get("mode")))
                flt = msg.get("filter")
                if flt is not None and flt not in (1, 2, 3):
                    raise ValueError("filter must be 1, 2, 3 or null")
                await station.set_mode(op, ModeSetting(mode, flt))
            else:
                raise ValueError(f"unknown message type {kind!r}")
        except TxDenied as exc:
            conn.push({"type": "error", "code": exc.reason.value, "message": str(exc)})
        except ControlDenied as exc:
            conn.push({"type": "error", "code": "control_denied", "message": str(exc)})
        except RadioError as exc:
            conn.push({"type": "error", "code": "radio", "message": str(exc)})
        except ValueError as exc:
            conn.push({"type": "error", "code": "bad_request", "message": str(exc)})
