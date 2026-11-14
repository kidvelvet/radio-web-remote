"""Control WebSocket connections: one per browser tab.

Each connection is its own TX identity (session public id plus a counter),
so closing one tab unkeys only if that tab holds PTT.

Client -> server (JSON):
  {"type": "heartbeat"}
  {"type": "ptt", "down": true | false}
  {"type": "visibility", "hidden": true | false}
  {"type": "set_frequency", "hz": 14074000}
  {"type": "set_mode", "mode": "USB", "filter": 1 | null}
  {"type": "set_level", "level": "af" | "rf_gain" | "squelch" | "rf_power", "value": 0..255}
  {"type": "set_data_mode", "on": true | false}
  {"type": "scope", "on": true | false}   subscribe to scope lines (binary frames)
  {"type": "audio", "mode": "remote" | "host" | "monitor" | null}
  binary 0x03 frames: transmit audio (audio/pcm.py), used only from the TX holder
  {"type": "set_scope", "setting": "mode"|"span"|"edge"|"hold"|"ref"|"speed", "value": ...}

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
import collections
import contextlib
import itertools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from radio_web_remote.audio import pcm
from radio_web_remote.audio.router import AudioMode
from radio_web_remote.config import PttConfig
from radio_web_remote.radios.base import Level, Mode, ModeSetting, RadioError
from radio_web_remote.security.users import Session
from radio_web_remote.station.radio_station import ControlDenied, RadioState, RadioStation
from radio_web_remote.station.tx_controller import Operator, StopReason, TxDenied
from radio_web_remote.web.scope_wire import encode_scope_line

log = logging.getLogger(__name__)

MAX_MESSAGE = 4096
DIAG_S = 5.0  # audio diagnostics window
_now = time.monotonic  # diagnostics clock (patched in tests)
# Browser receive-audio diagnostics, per DIAG_S window (see static/js/audio.js).
DIAG_FIELDS = ("frames", "lost", "late", "max_gap_ms", "max_lag_ms", "gaps", "skips", "target_ms")
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
        self.scope_on = False
        # Newest few scope lines only: a slow link drops waterfall rows, never control.
        self.scope_lines: collections.deque[bytes] = collections.deque(maxlen=3)
        self.audio_mode: AudioMode | None = None
        # About 0.5 s of receive audio; beyond that a slow link drops audio, not control.
        self.audio_frames: collections.deque[bytes] = collections.deque(maxlen=25)
        self.audio_dropped = 0  # frames dropped since the last diagnostics line
        self.audio_max_send_s = 0.0
        self.audio_diag_t = 0.0
        self.client_diag_t = 0.0
        self.wake = asyncio.Event()

    def push_state(self, state: dict[str, Any]) -> None:
        self.pending_state = state
        self.wake.set()

    def push_audio(self, frame: bytes) -> None:
        if len(self.audio_frames) == self.audio_frames.maxlen:
            self.audio_dropped += 1
        self.audio_frames.append(frame)
        self.wake.set()

    def push_scope(self, frame: bytes) -> None:
        self.scope_lines.append(frame)
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
        self._scope_listeners: dict[str, Callable[[Any], None]] = {}
        self._audio_listeners: dict[str, Callable[[bytes], None]] = {}

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

            def on_scope(line: Any, rid: str = rid) -> None:
                frame: bytes | None = None
                for c in self._conns.values():
                    if c.radio_id == rid and c.scope_on:
                        frame = frame or encode_scope_line(line)
                        c.push_scope(frame)

            self._scope_listeners[rid] = on_scope
            st.add_scope_listener(on_scope)

            def on_rx_audio(frame_pcm: bytes, rid: str = rid) -> None:
                st = self.stations[rid]
                frame: bytes | None = None
                level_tick = st.audio.rx_seq % 10 == 0  # about 5 Hz
                holder = st.tx.status.holder_session if st.tx.status.transmitting else None
                for c in self._conns.values():
                    if c.radio_id != rid or c.audio_mode is None:
                        continue
                    if c.audio_mode is not AudioMode.HOST:
                        frame = frame or pcm.encode_frame(
                            pcm.TYPE_RX_AUDIO, st.audio.rx_seq, frame_pcm
                        )
                        c.push_audio(frame)
                    if level_tick and holder == c.op.session_id:
                        c.push({"type": "tx_level", "peak_dbfs": st.audio.tx_peak_dbfs})

            self._audio_listeners[rid] = on_rx_audio
            st.audio.add_rx_listener(on_rx_audio)

    async def close(self) -> None:
        for rid, cb in self._listeners.items():
            self.stations[rid].remove_listener(cb)
        for rid, scb in self._scope_listeners.items():
            self.stations[rid].remove_scope_listener(scb)
        for rid, acb in self._audio_listeners.items():
            self.stations[rid].audio.remove_rx_listener(acb)
        self._audio_listeners.clear()
        self._listeners.clear()
        self._scope_listeners.clear()
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
            station.audio.remove(conn_id)
            if conn.scope_on:
                await asyncio.shield(self._update_scope_demand(radio_id))
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
            lines = list(conn.scope_lines)
            conn.scope_lines.clear()
            audio = list(conn.audio_frames)
            conn.audio_frames.clear()
            for af in audio:
                t0 = time.monotonic()
                await conn.ws.send_bytes(af)
                conn.audio_max_send_s = max(conn.audio_max_send_s, time.monotonic() - t0)
            if audio:
                self._audio_diag(conn)
            for m in msgs:
                await conn.ws.send_text(json.dumps(m))
            if state is not None:
                await conn.ws.send_text(json.dumps({"type": "state", "state": state}))
            for frame in lines:
                await conn.ws.send_bytes(frame)

    def _audio_diag(self, conn: _Conn) -> None:
        """Every DIAG_S, log a line if this client's link held audio up (quiet when healthy)."""
        now = _now()
        if now - conn.audio_diag_t < DIAG_S:
            return
        if conn.audio_dropped or conn.audio_max_send_s > 0.1:
            log.info(
                "audio send to %s: dropped %d frames, longest send %.0f ms, scope %s",
                conn.op.username,
                conn.audio_dropped,
                conn.audio_max_send_s * 1000,
                "on" if conn.scope_on else "off",
            )
        conn.audio_diag_t = now
        conn.audio_dropped = 0
        conn.audio_max_send_s = 0.0

    def _client_diag(self, conn: _Conn, msg: dict[str, Any]) -> None:
        """Browser receive-audio diagnostics: numbers only, rate limited, logged if unhealthy."""
        now = _now()
        if now - conn.client_diag_t < DIAG_S / 2:
            return
        conn.client_diag_t = now
        vals = {}
        for key in DIAG_FIELDS:
            v = msg.get(key)
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 10**7:
                raise ValueError(f"bad {key}")
            vals[key] = v
        trouble = any(vals[k] for k in ("lost", "late", "gaps", "skips"))
        if trouble or vals["max_lag_ms"] > 100:
            log.info(
                "audio receive at %s: %s, scope %s",
                conn.op.username,
                ", ".join(f"{k} {v}" for k, v in vals.items()),
                "on" if conn.scope_on else "off",
            )

    async def _update_scope_demand(self, radio_id: str) -> None:
        wanted = any(c.scope_on for c in self._conns.values() if c.radio_id == radio_id)
        await self.stations[radio_id].set_scope_demand(wanted)

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
                message = await asyncio.wait_for(conn.ws.receive(), IDLE_CLOSE_S)
            except TimeoutError:
                await conn.ws.close(code=4000)
                return
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            data = message.get("bytes")
            if data is not None:
                self._handle_binary(conn, station, data)
                continue
            text = message.get("text") or ""
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

    def _handle_binary(self, conn: _Conn, station: RadioStation, data: bytes) -> None:
        """Transmit audio from the browser. Silently ignored unless this tab holds TX."""
        try:
            _seq, _rate, frame_pcm = pcm.decode_frame(data, pcm.TYPE_TX_AUDIO)
        except pcm.AudioFrameError as exc:
            conn.push({"type": "error", "code": "bad_audio", "message": str(exc)})
            return
        station.audio.tx_from_browser(conn.op.session_id, frame_pcm)

    async def _handle(self, conn: _Conn, station: RadioStation, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        op = conn.op
        tx = station.tx
        try:
            if kind == "heartbeat":
                tx.heartbeat(op)
            elif kind == "audio_diag":
                self._client_diag(conn, msg)
            elif kind == "ptt":
                if msg.get("down") is True:
                    await tx.ptt_down(op)
                else:
                    await tx.ptt_up(op)
            elif kind == "audio":
                mode = msg.get("mode")
                if mode is None:
                    conn.audio_mode = None
                    station.audio.remove(op.session_id)
                    conn.audio_frames.clear()
                else:
                    audio_mode = AudioMode(str(mode))
                    station.audio.set_mode(op.session_id, audio_mode)
                    conn.audio_mode = audio_mode
            elif kind == "scope":
                on = msg.get("on")
                if not isinstance(on, bool):
                    raise ValueError("on must be true or false")
                conn.scope_on = on
                if not on:
                    conn.scope_lines.clear()
                await self._update_scope_demand(conn.radio_id)
            elif kind == "set_scope":
                name = msg.get("setting")
                value = msg.get("value")
                checks = {
                    "mode": lambda v: v in ("center", "fixed"),
                    "span": lambda v: isinstance(v, int) and not isinstance(v, bool),
                    "edge": lambda v: v in (1, 2, 3) and not isinstance(v, bool),
                    "hold": lambda v: isinstance(v, bool),
                    "ref": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
                    "speed": lambda v: v in ("fast", "mid", "slow"),
                }
                if name not in checks or not checks[name](value):
                    raise ValueError("bad scope setting")
                await station.set_scope_setting(op, str(name), value)
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
            elif kind == "set_level":
                level = Level(str(msg.get("level")))
                value = msg.get("value")
                if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255:
                    raise ValueError("value must be an integer 0..255")
                await station.set_level(op, level, value)
            elif kind == "set_data_mode":
                on = msg.get("on")
                if not isinstance(on, bool):
                    raise ValueError("on must be true or false")
                await station.set_data_mode(op, on)
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
