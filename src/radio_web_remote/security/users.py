"""Local user accounts and login sessions, stored in SQLite.

- Passwords: Argon2id (argon2-cffi defaults: time cost 3, 64 MiB, 4 lanes).
  Hashing takes tens of milliseconds, so the web layer calls these methods
  through asyncio.to_thread and never blocks the PTT watchdog's event loop.
- Lockout: LOCK_AFTER consecutive failures lock the account for LOCK_MINUTES.
  Unknown usernames do the same hashing work as known ones, so response time
  does not reveal which accounts exist.
- Sessions: a random token goes in the cookie; only its SHA-256 is stored.
  Each session also has a CSRF token and a public id that is safe to log.
- The first admin is created from the command line only (radio-web-remote init).

This module is synchronous and thread-safe (one connection per call).
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from radio_web_remote.station.tx_controller import Role, TxLimits

LOCK_AFTER = 5
LOCK_MINUTES = 15
MIN_PASSWORD_LEN = 10
SESSION_IDLE_S = 12 * 3600
SESSION_MAX_S = 7 * 24 * 3600
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
_CALLSIGN = re.compile(r"^[A-Z0-9/]{3,12}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    pw_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    callsign TEXT NOT NULL DEFAULT '',
    disabled INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    bands TEXT,                 -- JSON-free: "lo-hi,lo-hi" in Hz, NULL = no limit
    max_power_pct REAL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created REAL NOT NULL,
    last_seen REAL NOT NULL,
    ip TEXT
);
"""


class UserError(ValueError):
    pass


class LoginFailed(Exception):
    """Wrong username or password. Deliberately says no more than that."""


class LockedOut(Exception):
    def __init__(self, seconds_left: int) -> None:
        super().__init__(f"account locked for {seconds_left} s")
        self.seconds_left = seconds_left


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: Role
    callsign: str
    disabled: bool
    limits: TxLimits


@dataclass(frozen=True)
class Session:
    public_id: str
    csrf: str
    user: User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _parse_bands(text: str | None) -> tuple[tuple[int, int], ...] | None:
    if text is None:
        return None
    out = []
    for part in text.split(","):
        lo, hi = part.split("-")
        out.append((int(lo), int(hi)))
    return tuple(out)


def _format_bands(bands: tuple[tuple[int, int], ...] | None) -> str | None:
    if bands is None:
        return None
    for lo, hi in bands:
        if not 0 < lo <= hi:
            raise UserError(f"bad band range {lo}-{hi}")
    return ",".join(f"{lo}-{hi}" for lo, hi in bands)


class UserStore:
    def __init__(self, path: Path, hasher: PasswordHasher | None = None) -> None:
        self._ghosts: dict[str, tuple[int, float]] = {}  # unknown names: failures, lock
        self._ghost_lock = threading.Lock()
        self.path = path
        self.ph = hasher or PasswordHasher()
        self._dummy_hash = self.ph.hash(secrets.token_urlsafe(16))
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    # -- users -----------------------------------------------------------
    @staticmethod
    def _check(username: str, password: str | None, role: Role, callsign: str) -> None:
        if not _USERNAME.match(username):
            raise UserError("username: lowercase letters, digits, . _ - (max 32)")
        if password is not None and len(password) < MIN_PASSWORD_LEN:
            raise UserError(f"password must be at least {MIN_PASSWORD_LEN} characters")
        if role in (Role.OPERATOR,) and not callsign:
            raise UserError("operators need a callsign")
        if callsign and not _CALLSIGN.match(callsign):
            raise UserError("callsign: 3-12 of A-Z, 0-9, /")

    def _row_to_user(self, r: sqlite3.Row) -> User:
        return User(
            id=r["id"],
            username=r["username"],
            role=Role(r["role"]),
            callsign=r["callsign"],
            disabled=bool(r["disabled"]),
            limits=TxLimits(_parse_bands(r["bands"]), r["max_power_pct"]),
        )

    def create_user(self, username: str, password: str, role: Role, callsign: str = "") -> User:
        username, callsign = username.strip().lower(), callsign.strip().upper()
        self._check(username, password, role, callsign)
        with self._db() as db:
            try:
                db.execute(
                    "INSERT INTO users (username, pw_hash, role, callsign, created)"
                    " VALUES (?,?,?,?,?)",
                    (username, self.ph.hash(password), role.value, callsign, time.time()),
                )
            except sqlite3.IntegrityError:
                raise UserError(f"user {username!r} already exists") from None
        user = self.get_user(username)
        assert user is not None
        return user

    def get_user(self, username: str) -> User | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM users WHERE username = ?", (username.lower(),)).fetchone()
        return self._row_to_user(r) if r else None

    def list_users(self) -> list[User]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [self._row_to_user(r) for r in rows]

    def count_admins(self) -> int:
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0"
            ).fetchone()
        return int(row[0])

    def set_password(self, username: str, password: str) -> None:
        if len(password) < MIN_PASSWORD_LEN:
            raise UserError(f"password must be at least {MIN_PASSWORD_LEN} characters")
        self._update(username, pw_hash=self.ph.hash(password), failed=0, locked_until=0)
        self.end_sessions(username)

    def set_role(self, username: str, role: Role, callsign: str | None = None) -> None:
        user = self._require(username)
        cs = user.callsign if callsign is None else callsign.strip().upper()
        self._check(user.username, None, role, cs)
        self._update(username, role=role.value, callsign=cs)
        self.end_sessions(username)

    def set_limits(self, username: str, limits: TxLimits) -> None:
        self._update(
            username, bands=_format_bands(limits.bands_hz), max_power_pct=limits.max_power_pct
        )
        self.end_sessions(username)

    def set_disabled(self, username: str, disabled: bool) -> None:
        self._update(username, disabled=int(disabled))
        if disabled:
            self.end_sessions(username)

    def unlock(self, username: str) -> None:
        self._update(username, failed=0, locked_until=0)

    def delete_user(self, username: str) -> None:
        self._require(username)
        with self._db() as db:
            db.execute("DELETE FROM users WHERE username = ?", (username.lower(),))

    def _require(self, username: str) -> User:
        user = self.get_user(username)
        if user is None:
            raise UserError(f"no user {username!r}")
        return user

    _COLUMNS = frozenset(
        {
            "pw_hash",
            "role",
            "callsign",
            "disabled",
            "failed",
            "locked_until",
            "bands",
            "max_power_pct",
        }
    )

    def _update(self, username: str, **cols: object) -> None:
        if not cols.keys() <= self._COLUMNS:
            raise ValueError(f"unknown columns {set(cols) - self._COLUMNS}")
        self._require(username)
        sets = ", ".join(f"{c} = ?" for c in cols)  # names from the allow-list; values bound
        sql = f"UPDATE users SET {sets} WHERE username = ?"  # noqa: S608
        with self._db() as db:
            db.execute(sql, (*cols.values(), username.lower()))

    # -- login -----------------------------------------------------------
    def _ghost_locked_until(self, username: str, now: float) -> float:
        entry = self._ghosts.get(username)
        return entry[1] if entry and entry[1] > now else 0.0

    def _ghost_failed(self, username: str, now: float) -> bool:
        """Count a failure for a name with no account; True when it locks now."""
        with self._ghost_lock:
            if len(self._ghosts) > 10_000:  # bound memory under a flood of names
                self._ghosts = {k: v for k, v in self._ghosts.items() if v[1] > now}
            failed, _ = self._ghosts.get(username, (0, 0.0))
            failed += 1
            if failed >= LOCK_AFTER:
                self._ghosts[username] = (0, now + LOCK_MINUTES * 60)
                return True
            self._ghosts[username] = (failed, 0.0)
            return False

    def verify_login(self, username: str, password: str) -> User:
        username = username.strip().lower()
        with self._db() as db:
            r = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        now = time.time()
        if r is None:
            # Unknown names: same work and the same lockout as a real account,
            # so neither timing nor a 'locked' answer reveals which names exist.
            locked_until = self._ghost_locked_until(username, now)
            if locked_until > now:
                raise LockedOut(int(locked_until - now) + 1)
            with contextlib.suppress(VerificationError):
                self.ph.verify(self._dummy_hash, password)
            if self._ghost_failed(username, now):
                raise LockedOut(LOCK_MINUTES * 60)
            raise LoginFailed
        if r["locked_until"] > now:
            raise LockedOut(int(r["locked_until"] - now) + 1)
        try:
            self.ph.verify(r["pw_hash"], password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            # Atomic: concurrent wrong guesses must not each see the old count.
            with self._db() as db:
                failed = db.execute(
                    "UPDATE users SET failed = failed + 1 WHERE id = ? RETURNING failed",
                    (r["id"],),
                ).fetchone()[0]
                if failed >= LOCK_AFTER:
                    db.execute(
                        "UPDATE users SET failed = 0, locked_until = ? WHERE id = ?",
                        (now + LOCK_MINUTES * 60, r["id"]),
                    )
            if failed >= LOCK_AFTER:
                raise LockedOut(LOCK_MINUTES * 60) from None
            raise LoginFailed from None
        if r["disabled"]:
            raise LoginFailed
        updates: dict[str, object] = {"failed": 0, "locked_until": 0}
        if self.ph.check_needs_rehash(r["pw_hash"]):
            updates["pw_hash"] = self.ph.hash(password)
        self._update(username, **updates)
        return self._row_to_user(r)

    # -- sessions ----------------------------------------------------------
    def create_session(self, user: User, ip: str | None = None) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        public_id = secrets.token_hex(8)
        csrf = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute(
                "INSERT INTO sessions"
                " (token_hash, public_id, user_id, csrf, created, last_seen, ip)"
                " VALUES (?,?,?,?,?,?,?)",
                (_hash_token(token), public_id, user.id, csrf, now, now, ip),
            )
        return token, Session(public_id, csrf, user)

    def get_session(self, token: str | None, touch: bool = True) -> Session | None:
        if not token or len(token) > 256:
            return None
        h = _hash_token(token)
        now = time.time()
        with self._db() as db:
            r = db.execute(
                "SELECT s.public_id, s.csrf, s.created, s.last_seen, u.* FROM sessions s"
                " JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (h,),
            ).fetchone()
            if r is None:
                return None
            expired = now - r["last_seen"] > SESSION_IDLE_S or now - r["created"] > SESSION_MAX_S
            if expired or r["disabled"]:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))
                return None
            if touch and now - r["last_seen"] > 60:
                db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, h))
        return Session(r["public_id"], r["csrf"], self._row_to_user(r))

    def end_session(self, token: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))

    def end_sessions(self, username: str) -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)",
                (username.lower(),),
            )

    def purge_expired(self) -> int:
        now = time.time()
        with self._db() as db:
            cur = db.execute(
                "DELETE FROM sessions WHERE last_seen < ? OR created < ?",
                (now - SESSION_IDLE_S, now - SESSION_MAX_S),
            )
        return cur.rowcount
