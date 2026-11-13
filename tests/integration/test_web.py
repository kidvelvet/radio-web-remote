"""HTTPS app: auth, CSRF, headers, origin checks, and PTT over the control WebSocket."""

import json
import time

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from radio_web_remote.config import Config, PttConfig, RadioConfig
from radio_web_remote.radios.icom import IC7300
from radio_web_remote.security import users as users_mod
from radio_web_remote.security.audit import AuditLog
from radio_web_remote.security.users import UserStore
from radio_web_remote.sim.icom import IcomSimulator, loopback_transport
from radio_web_remote.station.radio_station import RadioStation
from radio_web_remote.station.tx_controller import Role
from radio_web_remote.web.app import CSP, create_app

BASE = "https://testserver"
ORIGIN = {"origin": BASE}
PW = "correct horse battery"


@pytest.fixture
def env(tmp_path):
    store = UserStore(
        tmp_path / "users.db", hasher=PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    store.create_user("admin", PW, Role.ADMIN, "N0ADM")
    store.create_user("alice", PW, Role.OPERATOR, "N0CALL")
    store.create_user("bob", PW, Role.OPERATOR, "N1CALL")
    store.create_user("lee", PW, Role.LISTENER)
    sim = IcomSimulator(IC7300)
    ptt = PttConfig(heartbeat_interval_s=0.1, heartbeat_timeout_s=0.5)
    cfg = Config.model_validate({"ptt": ptt.model_dump()})
    audit = AuditLog(tmp_path / "audit.log")
    station = RadioStation(
        RadioConfig(id="ic7300"),
        ptt,
        transport_factory=lambda: loopback_transport(sim),
        audit=audit,
        poll_interval_s=0.1,
        tx_watchdog_s=0.02,
    )
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n")
    app = create_app(cfg, store, {"ic7300": station}, audit=audit, ca_cert_file=ca)
    with TestClient(app, base_url=BASE) as client:
        deadline = time.monotonic() + 3
        while not station.connected and time.monotonic() < deadline:
            time.sleep(0.02)
        yield {
            "client": client,
            "app": app,
            "sim": sim,
            "station": station,
            "store": store,
            "tmp": tmp_path,
        }
    audit.close()


def login(client, username="alice", password=PW):
    token = client.get("/api/login-csrf").json()["csrf"]
    return client.post(
        "/api/login",
        json={"username": username, "password": password},
        headers={"x-csrf-token": token},
    )


def cookie_header(client):
    return "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar)


def login_as(client, username):
    """Log in and return that user's cookie header, leaving the client's jar empty.

    Lets one TestClient (one event loop, shared with the radio station) act as
    several users at once."""
    client.cookies.clear()
    assert login(client, username).status_code == 200
    jar = cookie_header(client)
    client.cookies.clear()
    return jar


def wsc(client, path="/ws", origin=BASE, cookies=None):
    """Open the control socket. The test transport is ws://, which withholds Secure
    cookies; a browser on wss:// sends them, so pass the session cookie explicitly."""
    headers = {} if origin is None else {"origin": origin}
    jar = cookie_header(client) if cookies is None else cookies
    if jar:
        headers["cookie"] = jar
    return client.websocket_connect(path, headers=headers)


def recv_until(ws, pred, limit=200):
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("expected message never arrived")


def tx_state(msg):
    return msg["type"] == "state" and msg["state"]["tx"]


def keyed(msg):
    return tx_state(msg) and msg["state"]["tx"]["keyed"]


# -- headers and public pages ----------------------------------------------------


def test_security_headers(env):
    r = env["client"].get("/setup")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == CSP
    assert "unsafe-inline" not in CSP and "http:" not in CSP and "*" not in CSP
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "microphone=(self)" in r.headers["permissions-policy"]


def test_public_routes(env):
    c = env["client"]
    assert c.get("/login").status_code == 200
    assert c.get("/static/css/app.css").status_code == 200
    r = c.get("/setup/ca.crt")
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-x509-ca-cert"
    assert b"PRIVATE" not in r.content


def test_static_html_has_no_inline_script_or_style(env):
    from radio_web_remote.web.app import STATIC_DIR

    for page in STATIC_DIR.glob("*.html"):
        text = page.read_text()
        assert "<script>" not in text and "style=" not in text and "onclick" not in text, page.name


@pytest.mark.parametrize("path", ["/", "/api/me"])
def test_protected_routes_need_login(env, path):
    r = env["client"].get(path, follow_redirects=False)
    assert r.status_code in (303, 401)
    if r.status_code == 303:
        assert r.headers["location"] == "/login"


# -- login -------------------------------------------------------------------------


def test_login_requires_csrf(env):
    r = env["client"].post("/api/login", json={"username": "alice", "password": PW})
    assert r.status_code == 403


def test_login_sets_strict_cookie_and_me_works(env):
    c = env["client"]
    r = login(c)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "rwr_session=" in cookie and "secure" in cookie and "httponly" in cookie
    assert "samesite=strict" in cookie
    me = c.get("/api/me").json()
    assert me["username"] == "alice" and me["callsign"] == "N0CALL" and me["role"] == "operator"
    assert c.get("/", follow_redirects=False).status_code == 200


def test_wrong_password_then_lockout(env):
    c = env["client"]
    for _ in range(users_mod.LOCK_AFTER - 1):
        assert login(c, password="wrong password!").status_code == 401
    assert login(c, password="wrong password!").status_code == 423
    assert login(c).status_code == 423  # right password, still locked


def test_unknown_user_same_answer_as_wrong_password(env):
    a = login(env["client"], "nobody", "whatever12345")
    b = login(env["client"], "alice", "whatever12345")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_rate_limit_per_address(env):
    c = env["client"]
    codes = [login(c, f"ghost{i}", "wrong password!").status_code for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_audit_log_has_logins_without_secrets(env):
    login(env["client"], password="wrong password!")
    login(env["client"])
    lines = [json.loads(x) for x in (env["tmp"] / "audit.log").read_text().splitlines()]
    events = [x["event"] for x in lines]
    assert "login_failed" in events and "login" in events
    assert PW not in (env["tmp"] / "audit.log").read_text()


# -- logout --------------------------------------------------------------------------


def test_logout_needs_session_csrf(env):
    c = env["client"]
    login(c)
    assert c.post("/api/logout").status_code == 403
    csrf = c.get("/api/me").json()["csrf"]
    assert c.post("/api/logout", headers={"x-csrf-token": csrf}).status_code == 200
    assert c.get("/api/me").status_code == 401


# -- control WebSocket -------------------------------------------------------------------


def test_ws_rejects_without_session(env):
    with (
        pytest.raises(WebSocketDisconnect),
        env["client"].websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        ws.receive_json()


@pytest.mark.parametrize(
    "origin", [None, "https://evil.example", "http://testserver", "https://testserver.evil"]
)
def test_ws_rejects_bad_origin(env, origin):
    c = env["client"]
    login(c)
    with pytest.raises(WebSocketDisconnect), wsc(c, origin=origin) as ws:
        ws.receive_json()


def test_ws_hello_and_state(env):
    c = env["client"]
    login(c)
    with wsc(c, "/ws?radio=ic7300") as ws:
        hello = ws.receive_json()
        assert (
            hello["type"] == "hello"
            and hello["user"]["callsign"] == "N0CALL"
            and hello["may_transmit"]
        )
        st = recv_until(ws, lambda m: m["type"] == "state" and m["state"]["connected"])
        assert st["state"]["frequency_hz"] == 14_074_000 and st["state"]["model"] == "IC-7300"


def test_ws_ptt_and_close_unkeys_server_side(env):
    c = env["client"]
    login(c)
    with wsc(c, "/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ptt", "down": True})
        recv_until(ws, keyed)
        assert env["sim"].state.transmitting
        assert env["station"].state.tx.holder_callsign == "N0CALL"
    # Socket closed with PTT still down: the server must unkey on its own.
    deadline = time.monotonic() + 2
    while env["sim"].state.transmitting and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not env["sim"].state.transmitting
    assert env["station"].state.tx.last_stop_reason.value == "connection_closed"


def test_ws_heartbeat_loss_unkeys(env):
    c = env["client"]
    login(c)
    with wsc(c, "/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ptt", "down": True})
        recv_until(ws, keyed)
        msg = recv_until(ws, lambda m: tx_state(m) and not m["state"]["tx"]["transmitting"])
        assert msg["state"]["tx"]["last_stop"] == "heartbeat_lost"


def test_ws_hidden_unkeys(env):
    c = env["client"]
    login(c)
    with wsc(c, "/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ptt", "down": True})
        recv_until(ws, keyed)
        ws.send_json({"type": "visibility", "hidden": True})
        msg = recv_until(ws, lambda m: tx_state(m) and not m["state"]["tx"]["transmitting"])
        assert msg["state"]["tx"]["last_stop"] == "hidden"


def test_ws_listener_cannot_transmit_or_tune(env):
    c = env["client"]
    login(c, "lee")
    with wsc(c, "/ws") as ws:
        assert ws.receive_json()["may_transmit"] is False
        ws.send_json({"type": "ptt", "down": True})
        err = recv_until(ws, lambda m: m["type"] == "error")
        assert err["code"] == "not_permitted"
        ws.send_json({"type": "set_frequency", "hz": 7_000_000})
        err = recv_until(ws, lambda m: m["type"] == "error")
        assert err["code"] == "control_denied"
    assert not env["sim"].state.transmitting and env["sim"].state.frequency_hz == 14_074_000


def test_ws_second_operator_sees_tx_held(env):
    c = env["client"]
    alice, bob = login_as(c, "alice"), login_as(c, "bob")
    with (
        wsc(c, cookies=alice) as wa,
        wsc(c, cookies=bob) as wb,
    ):
        wa.receive_json()
        wb.receive_json()
        wa.send_json({"type": "ptt", "down": True})
        held = recv_until(wb, lambda m: tx_state(m) and m["state"]["tx"]["transmitting"])
        assert held["state"]["tx"]["holder"] == "N0CALL"
        wb.send_json({"type": "ptt", "down": True})
        err = recv_until(wb, lambda m: m["type"] == "error")
        assert err["code"] == "held_by_other" and "N0CALL" in err["message"]
        wa.send_json({"type": "ptt", "down": False})
        recv_until(wa, lambda m: tx_state(m) and not m["state"]["tx"]["transmitting"])


def test_ws_tune_and_mode(env):
    c = env["client"]
    login(c)
    with wsc(c, "/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_frequency", "hz": 7_074_000})
        recv_until(ws, lambda m: m["type"] == "state" and m["state"]["frequency_hz"] == 7_074_000)
        ws.send_json({"type": "set_mode", "mode": "LSB", "filter": 2})
        recv_until(ws, lambda m: m["type"] == "state" and m["state"]["mode"] == "LSB")
    assert env["sim"].state.frequency_hz == 7_074_000
    assert (env["sim"].state.mode_code, env["sim"].state.filter_code) == (0x00, 0x02)


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "set_frequency", "hz": "14074000"},
        {"type": "set_frequency", "hz": -5},
        {"type": "set_frequency", "hz": True},
        {"type": "set_mode", "mode": "SSTV"},
        {"type": "set_mode", "mode": "USB", "filter": 9},
        {"type": "launch_missiles"},
    ],
)
def test_ws_rejects_bad_messages(env, msg):
    c = env["client"]
    login(c)
    with wsc(c, "/ws") as ws:
        ws.receive_json()
        ws.send_json(msg)
        assert recv_until(ws, lambda m: m["type"] == "error")["code"] == "bad_request"


def test_ws_invalid_json_and_oversize(env):
    c = env["client"]
    login(c)
    with wsc(c, "/ws") as ws:
        ws.receive_json()
        ws.send_text("{not json")
        assert recv_until(ws, lambda m: m["type"] == "error")["code"] == "bad_json"
        ws.send_text("x" * 5000)
        assert recv_until(ws, lambda m: m["type"] == "error")["code"] == "too_big"


def test_logout_unkeys_and_closes_socket(env):
    c = env["client"]
    login(c)
    csrf = c.get("/api/me").json()["csrf"]
    with wsc(c, "/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ptt", "down": True})
        recv_until(ws, keyed)
        assert c.post("/api/logout", headers={"x-csrf-token": csrf}).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            recv_until(ws, lambda m: False)
    assert not env["sim"].state.transmitting
    assert env["station"].state.tx.last_stop_reason.value == "logged_out"


def test_tx_audit_entries(env):
    c = env["client"]
    login(c)
    with wsc(c, "/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ptt", "down": True})
        recv_until(ws, keyed)
        ws.send_json({"type": "ptt", "down": False})
        recv_until(ws, lambda m: tx_state(m) and not m["state"]["tx"]["transmitting"])
    lines = [json.loads(x) for x in (env["tmp"] / "audit.log").read_text().splitlines()]
    start = next(x for x in lines if x["event"] == "tx_start")
    stop = next(x for x in lines if x["event"] == "tx_stop")
    assert (
        start["callsign"] == "N0CALL"
        and start["radio"] == "ic7300"
        and start["frequency_hz"] == 14_074_000
    )
    assert stop["reason"] == "released" and stop["duration_s"] >= 0


def test_ws_levels_and_data_mode(env):
    c = env["client"]
    login(c)
    with wsc(c) as ws:
        ws.receive_json()
        ws.send_json({"type": "set_level", "level": "af", "value": 77})
        recv_until(ws, lambda m: m["type"] == "state" and m["state"]["levels"].get("af") == 77)
        ws.send_json({"type": "set_data_mode", "on": True})
        recv_until(ws, lambda m: m["type"] == "state" and m["state"]["data_mode"] is True)
        for bad in (
            {"type": "set_level", "level": "volume", "value": 1},
            {"type": "set_level", "level": "af", "value": 300},
            {"type": "set_data_mode", "on": "yes"},
        ):
            ws.send_json(bad)
            assert recv_until(ws, lambda m: m["type"] == "error")["code"] == "bad_request"
    assert env["sim"].state.levels[0x01] == 77 and env["sim"].state.data_mode


def test_successful_logins_do_not_count_toward_rate_limit(env):
    c = env["client"]
    for _ in range(15):
        assert login(c).status_code == 200


def test_clients_warned_while_tx_off_unconfirmed(env):
    # H7300-7 (2026-11-13): with USB pulled, the radio stayed keyed for about 43 s
    # while the server could not reach it. Every client must see that.
    c = env["client"]
    login(c)
    with wsc(c) as ws:
        ws.receive_json()
        ws.send_json({"type": "ptt", "down": True})
        recv_until(ws, keyed)
        env["sim"].silent = True  # radio unreachable
        ws.send_json({"type": "ptt", "down": False})
        warn = recv_until(ws, lambda m: tx_state(m) and m["state"]["tx"]["unconfirmed"], limit=400)
        assert warn["state"]["tx"]["transmitting"] is False
        env["sim"].silent = False
        recv_until(ws, lambda m: tx_state(m) and not m["state"]["tx"]["unconfirmed"], limit=800)
    events = [json.loads(x)["event"] for x in (env["tmp"] / "audit.log").read_text().splitlines()]
    assert "tx_off_unconfirmed" in events and "tx_off_confirmed" in events


def test_ws_scope_subscription_and_settings(env):
    from radio_web_remote.web.scope_wire import decode_scope_line

    env["sim"].scope_interval_s = 0.05
    c = env["client"]
    lee, alice = login_as(c, "lee"), login_as(c, "alice")
    with wsc(c, cookies=lee) as ws:  # listeners may watch the scope
        ws.receive_json()
        ws.send_json({"type": "scope", "on": True})
        for _ in range(400):
            msg = ws.receive()
            if msg.get("bytes"):
                break
        line = decode_scope_line(msg["bytes"])
        assert len(line.points) == 475 and line.max_value == 160
        assert env["sim"].state.scope_output
        ws.send_json({"type": "set_scope", "setting": "span", "value": 25_000})
        assert recv_until(ws, lambda m: m.get("type") == "error")["code"] == "control_denied"
    # Last viewer gone: scope data output is turned off again.
    deadline = time.monotonic() + 2
    while env["sim"].state.scope_output and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not env["sim"].state.scope_output
    with wsc(c, cookies=alice) as ws:
        ws.receive_json()
        ws.send_json({"type": "set_scope", "setting": "span", "value": 25_000})
        recv_until(
            ws, lambda m: m.get("type") == "state" and m["state"]["scope"].get("span") == 25_000
        )
        ws.send_json({"type": "set_scope", "setting": "span", "value": 12_345})
        assert recv_until(ws, lambda m: m.get("type") == "error")["code"] in (
            "bad_request",
            "radio",
        )
