"""Audit log: one JSON object per line, rotated by size.

Records logins, failed logins, lockouts, logouts, and every TX start/stop
with user, callsign, radio, frequency, duration and stop reason. Passwords,
session tokens and CSRF tokens are never written.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import threading
import time
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 10
_FORBIDDEN = {"password", "token", "csrf", "cookie"}


class AuditLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))

    def __call__(self, record: dict[str, Any]) -> None:
        clean = {k: v for k, v in record.items() if k.lower() not in _FORBIDDEN}
        clean.setdefault("ts", round(time.time(), 3))
        line = json.dumps(clean, sort_keys=True, default=str)
        rec = logging.LogRecord("audit", logging.INFO, __file__, 0, line, None, None)
        with self._lock:
            self._handler.emit(rec)

    def close(self) -> None:
        self._handler.close()
