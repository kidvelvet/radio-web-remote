"""Per-OS location for state (config, users DB, certificates, logs).

State never lives in the install directory or the source checkout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = "radio-web-remote"
ENV_HOME = "RADIO_WEB_REMOTE_HOME"


def data_dir() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR
