import contextlib

import pytest
from argon2 import PasswordHasher

from radio_web_remote.security import users as u
from radio_web_remote.security.users import LockedOut, LoginFailed, UserError, UserStore
from radio_web_remote.station.tx_controller import Role, TxLimits

FAST = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
PW = "correct horse battery"


@pytest.fixture
def store(tmp_path):
    s = UserStore(tmp_path / "users.db", hasher=FAST)
    s.create_user("alice", PW, Role.OPERATOR, "n0call")
    return s


def test_create_and_login(store):
    user = store.verify_login("Alice", PW)
    assert user.username == "alice" and user.callsign == "N0CALL" and user.role is Role.OPERATOR
    assert store.get_user("alice").limits == TxLimits()


def test_password_stored_as_argon2id(store, tmp_path):
    import sqlite3

    h = sqlite3.connect(tmp_path / "users.db").execute("SELECT pw_hash FROM users").fetchone()[0]
    assert h.startswith("$argon2id$") and PW not in h


@pytest.mark.parametrize(
    ("name", "pw", "role", "call"),
    [
        ("Bad Name", PW, Role.LISTENER, ""),
        ("bob", "short", Role.LISTENER, ""),
        ("bob", PW, Role.OPERATOR, ""),  # operator needs a callsign
        ("bob", PW, Role.LISTENER, "not a call"),
    ],
)
def test_validation(store, name, pw, role, call):
    with pytest.raises(UserError):
        store.create_user(name, pw, role, call)


def test_duplicate_user(store):
    with pytest.raises(UserError):
        store.create_user("alice", PW, Role.LISTENER)


def test_wrong_password_and_unknown_user_look_the_same(store):
    with pytest.raises(LoginFailed):
        store.verify_login("alice", "wrong password!")
    with pytest.raises(LoginFailed):
        store.verify_login("nobody", "wrong password!")


def test_lockout_after_repeated_failures(store):
    for _ in range(u.LOCK_AFTER - 1):
        with pytest.raises(LoginFailed):
            store.verify_login("alice", "wrong password!")
    with pytest.raises(LockedOut):
        store.verify_login("alice", "wrong password!")
    with pytest.raises(LockedOut):  # even the right password is refused while locked
        store.verify_login("alice", PW)
    store.unlock("alice")
    assert store.verify_login("alice", PW)


def test_success_resets_failure_count(store):
    for _ in range(u.LOCK_AFTER - 1):
        with pytest.raises(LoginFailed):
            store.verify_login("alice", "wrong password!")
    store.verify_login("alice", PW)
    for _ in range(u.LOCK_AFTER - 1):
        with pytest.raises(LoginFailed):
            store.verify_login("alice", "wrong password!")


def test_disabled_user_cannot_log_in(store):
    store.set_disabled("alice", True)
    with pytest.raises(LoginFailed):
        store.verify_login("alice", PW)


def test_sessions(store):
    user = store.verify_login("alice", PW)
    token, sess = store.create_session(user, "192.0.2.10")
    got = store.get_session(token)
    assert (
        got.user.username == "alice" and got.csrf == sess.csrf and got.public_id == sess.public_id
    )
    assert store.get_session("not-a-token") is None and store.get_session(None) is None
    store.end_session(token)
    assert store.get_session(token) is None


def test_session_token_not_stored(store, tmp_path):
    import sqlite3

    token, _ = store.create_session(store.get_user("alice"))
    rows = sqlite3.connect(tmp_path / "users.db").execute("SELECT * FROM sessions").fetchall()
    assert token not in repr(rows)


def test_idle_session_expires(store, monkeypatch):
    token, _ = store.create_session(store.get_user("alice"))
    real = u.time.time
    monkeypatch.setattr(u.time, "time", lambda: real() + u.SESSION_IDLE_S + 1)
    assert store.get_session(token) is None


def test_password_change_and_disable_end_sessions(store):
    token, _ = store.create_session(store.get_user("alice"))
    store.set_password("alice", "another long password")
    assert store.get_session(token) is None
    token, _ = store.create_session(store.get_user("alice"))
    store.set_disabled("alice", True)
    assert store.get_session(token) is None


def test_limits_roundtrip(store):
    store.set_limits("alice", TxLimits(bands_hz=((14_000_000, 14_350_000), (7_000_000, 7_300_000))))
    lim = store.get_user("alice").limits
    assert lim.bands_hz == ((14_000_000, 14_350_000), (7_000_000, 7_300_000))
    assert lim.allows_frequency(7_074_000) and not lim.allows_frequency(10_136_000)


def test_admin_count(store):
    assert store.count_admins() == 0
    store.create_user("root", PW, Role.ADMIN, "N0ADM")
    assert store.count_admins() == 1


def test_concurrent_wrong_passwords_are_all_counted(store):
    """Review C2: the failure count is atomic, so a burst cannot outrun the lockout."""
    import concurrent.futures

    def attempt(_):
        try:
            store.verify_login("alice", "wrong password here")
        except (LoginFailed, LockedOut) as exc:
            return type(exc).__name__
        return "ok"

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(u.LOCK_AFTER - 1)))
    assert results == ["LoginFailed"] * (u.LOCK_AFTER - 1)
    with pytest.raises(LockedOut):  # the next one is the LOCK_AFTER-th
        store.verify_login("alice", "wrong password here")


def test_unknown_names_lock_like_real_ones(store):
    """Review C10: a 'locked' answer must not reveal which names exist."""
    for _ in range(u.LOCK_AFTER - 1):
        with pytest.raises(LoginFailed):
            store.verify_login("nobody", "wrong password here")
    with pytest.raises(LockedOut):
        store.verify_login("nobody", "wrong password here")
    with pytest.raises(LockedOut):
        store.verify_login("nobody", PW)


def test_init_with_password_on_stdin_needs_no_callsign(tmp_path, monkeypatch, capsys):
    """Review E2-3: the password line was read as the callsign."""
    import io

    from radio_web_remote import cli

    monkeypatch.setattr("sys.stdin", io.StringIO("a-good-password-123\n"))
    cfg = tmp_path / "c.yaml"
    cfg.write_text("server:\n  bind_address: 127.0.0.1\n")
    rc = cli.main(
        [
            "init",
            "--config",
            str(cfg),
            "--data-dir",
            str(tmp_path),
            "--username",
            "admin",
            "--password-stdin",
        ]
    )
    assert rc == 0, capsys.readouterr().err
    user = UserStore(tmp_path / "users.db").verify_login("admin", "a-good-password-123")
    assert user.callsign == ""


def test_flood_of_names_does_not_unlock_a_locked_one(store, monkeypatch):
    """Review C3-6: eviction drops unlocked names first."""
    monkeypatch.setattr(u, "GHOST_LIMIT", 50)
    for _ in range(u.LOCK_AFTER):
        with contextlib.suppress(LoginFailed, LockedOut):
            store.verify_login("victimname", "wrong password here")
    for n in range(200):
        with contextlib.suppress(LoginFailed):
            store.verify_login(f"flood{n}", "wrong password here")
    with pytest.raises(LockedOut):
        store.verify_login("victimname", "wrong password here")
