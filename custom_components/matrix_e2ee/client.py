"""matrix-nio client lifecycle for matrix_e2ee (login / restore / shutdown)."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nio import AsyncClient, AsyncClientConfig
from nio.responses import LoginResponse, WhoamiResponse

from .const import (
    DEVICE_NAME,
    ERROR_DEVICE_MISMATCH,
    ERROR_LOGIN_FAILED,
    ERROR_PASSWORD_REQUIRED,
    ERROR_RESTORE_FAILED,
    ERROR_SESSION_MISSING,
)
from .storage import (
    MatrixSession,
    SessionError,
    atomic_save_session,
    ensure_store_dir,
    load_session,
)

_LOGGER = logging.getLogger(__name__)

NioClientFactory = Callable[..., Any]


class MatrixE2EEError(Exception):
    """Fail-closed integration error with a public error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_success(response: Any, success_type: type) -> bool:
    return isinstance(response, success_type)


class MatrixE2EEClient:
    """Owns the nio AsyncClient, session, and crypto store path."""

    def __init__(
        self,
        *,
        config_dir: str | Path,
        homeserver: str,
        username: str,
        password: str | None,
        allowed_rooms: list[str],
        allowed_users: list[str],
        command_prefix: str,
        fire_event: Callable[[str, dict[str, Any]], None],
        nio_client_factory: NioClientFactory | None = None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._homeserver = homeserver
        self._username = username
        self._password = password
        self.allowed_rooms = list(allowed_rooms)
        self.allowed_users = list(allowed_users)
        self.command_prefix = command_prefix
        self._fire_event = fire_event
        self._nio_client_factory = nio_client_factory
        self.nio: Any | None = None
        self.session: MatrixSession | None = None
        self._first_setup = False
        self._commands_enabled = False
        self._sync_task: Any | None = None

    def _make_nio(
        self,
        *,
        pickle_key: str,
        device_id: str | None,
    ) -> Any:
        store = str(ensure_store_dir(self._config_dir))
        config = AsyncClientConfig(
            encryption_enabled=True,
            store_sync_tokens=True,
            pickle_key=pickle_key,
        )
        factory = self._nio_client_factory or AsyncClient
        return factory(
            self._homeserver,
            self._username,
            device_id=device_id or "",
            store_path=store,
            config=config,
        )

    async def async_start(self) -> None:
        """Create or restore an E2EE-capable Matrix device, then connect."""
        try:
            existing = load_session(self._config_dir)
        except SessionError as err:
            raise MatrixE2EEError(err.code, str(err)) from err

        if existing is None:
            await self._first_login()
        else:
            await self._restore(existing)

    async def _first_login(self) -> None:
        if not self._password:
            raise MatrixE2EEError(
                ERROR_PASSWORD_REQUIRED,
                "password is required when no session exists",
            )
        pickle_key = secrets.token_urlsafe(32)
        nio = self._make_nio(pickle_key=pickle_key, device_id=None)
        self.nio = nio
        response = await nio.login(self._password, device_name=DEVICE_NAME)
        if not _is_success(response, LoginResponse):
            await self._close_nio()
            raise MatrixE2EEError(ERROR_LOGIN_FAILED, "matrix login failed")
        if not nio.user_id or not nio.device_id or not nio.access_token:
            await self._close_nio()
            raise MatrixE2EEError(ERROR_LOGIN_FAILED, "matrix login returned incomplete device")
        session = MatrixSession(
            version=1,
            user_id=nio.user_id,
            device_id=nio.device_id,
            access_token=nio.access_token,
            pickle_key=pickle_key,
        )
        atomic_save_session(self._config_dir, session)
        self.session = session
        self._first_setup = True
        _LOGGER.info("matrix_e2ee first login succeeded; session stored")

    async def _restore(self, session: MatrixSession) -> None:
        nio = self._make_nio(
            pickle_key=session.pickle_key,
            device_id=session.device_id,
        )
        self.nio = nio
        nio.restore_login(session.user_id, session.device_id, session.access_token)
        whoami = await nio.whoami()
        if not _is_success(whoami, WhoamiResponse):
            await self._close_nio()
            raise MatrixE2EEError(ERROR_RESTORE_FAILED, "restore whoami failed")
        if nio.device_id != session.device_id or nio.user_id != session.user_id:
            await self._close_nio()
            raise MatrixE2EEError(
                ERROR_DEVICE_MISMATCH,
                "homeserver returned a different device; refusing to continue",
            )
        self.session = session
        self._first_setup = False
        _LOGGER.info("matrix_e2ee restored existing Matrix device")

    async def async_stop(self) -> None:
        """Cancel sync and close the nio client."""
        task = self._sync_task
        self._sync_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except Exception:  # noqa: BLE001 — shutdown must not raise
                pass
        await self._close_nio()

    async def _close_nio(self) -> None:
        nio = self.nio
        self.nio = None
        if nio is None:
            return
        close = getattr(nio, "close", None)
        if close is None:
            return
        result = close()
        if hasattr(result, "__await__"):
            await result

    def _require_nio(self) -> Any:
        if self.nio is None:
            raise MatrixE2EEError(ERROR_SESSION_MISSING, "matrix client is not started")
        return self.nio
