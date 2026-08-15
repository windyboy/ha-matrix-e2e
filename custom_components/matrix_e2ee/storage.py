"""Atomic Matrix session storage for matrix_e2ee.

On-disk format (document-specified, not the Home Assistant Store envelope):

{
  "version": 1,
  "user_id": "...",
  "device_id": "...",
  "access_token": "...",
  "pickle_key": "..."
}

The session file is written only after a successful first login.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any

from .const import (
    ERROR_SESSION_CORRUPT,
    ERROR_STORE_MISSING,
    SESSION_FILENAME,
    SESSION_VERSION,
    STORE_DIRNAME,
)

_LOGGER = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("user_id", "device_id", "access_token", "pickle_key")


class SessionError(Exception):
    """Session load/save failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MatrixSession:
    """Persisted device-bound Matrix session (highly sensitive)."""

    version: int
    user_id: str
    device_id: str
    access_token: str
    pickle_key: str

    def to_dict(self) -> dict[str, Any]:
        """Return the on-disk JSON object. Caller must not log this."""
        return {
            "version": self.version,
            "user_id": self.user_id,
            "device_id": self.device_id,
            "access_token": self.access_token,
            "pickle_key": self.pickle_key,
        }


def session_path(config_dir: str | Path) -> Path:
    """Return `<config>/.storage/matrix_e2ee_session.json`."""
    return Path(config_dir) / ".storage" / SESSION_FILENAME


def store_path(config_dir: str | Path) -> Path:
    """Return `<config>/.storage/matrix_e2ee_store`."""
    return Path(config_dir) / ".storage" / STORE_DIRNAME


def load_session(config_dir: str | Path) -> MatrixSession | None:
    """Load a session or return None if the file does not exist.

    Corrupt or incomplete files fail closed (raise SessionError). Missing
    files are not an error: they mean first-time login is required.
    """
    path = session_path(config_dir)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        _LOGGER.error("matrix_e2ee session file is unreadable or not JSON")
        raise SessionError(
            ERROR_SESSION_CORRUPT, "session file is corrupt"
        ) from err
    if not isinstance(data, dict):
        _LOGGER.error("matrix_e2ee session file is not an object")
        raise SessionError(ERROR_SESSION_CORRUPT, "session file is corrupt")
    if data.get("version") != SESSION_VERSION:
        _LOGGER.error("matrix_e2ee session version is unsupported")
        raise SessionError(ERROR_SESSION_CORRUPT, "session version unsupported")
    missing = [field for field in _REQUIRED_FIELDS if not data.get(field)]
    if missing:
        _LOGGER.error("matrix_e2ee session is incomplete")
        raise SessionError(ERROR_SESSION_CORRUPT, "session file is incomplete")
    store = store_path(config_dir)
    if not store.is_dir():
        _LOGGER.error(
            "matrix_e2ee crypto store directory is missing; treat as a new device"
        )
        raise SessionError(
            ERROR_STORE_MISSING,
            "crypto store directory is missing; treat this as a new device "
            "(old history cannot be decrypted)",
        )
    return MatrixSession(
        version=SESSION_VERSION,
        user_id=str(data["user_id"]),
        device_id=str(data["device_id"]),
        access_token=str(data["access_token"]),
        pickle_key=str(data["pickle_key"]),
    )


def atomic_save_session(config_dir: str | Path, session: MatrixSession) -> None:
    """Atomically write the session JSON after a successful login."""
    path = session_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(session.to_dict(), indent=2, sort_keys=True)
    tmp_path = path.with_suffix(".json.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def ensure_store_dir(config_dir: str | Path) -> Path:
    """Create the crypto store directory with restrictive permissions."""
    path = store_path(config_dir)
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path
