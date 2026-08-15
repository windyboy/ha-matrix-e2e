"""matrix-nio client lifecycle for matrix_e2ee (login / restore / shutdown)."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .const import (
    DEVICE_NAME,
    ERROR_DEVICE_MISMATCH,
    ERROR_ENCRYPTION_UNAVAILABLE,
    ERROR_LOGIN_FAILED,
    ERROR_PASSWORD_REQUIRED,
    ERROR_RESTORE_FAILED,
    ERROR_ROOM_NOT_ALLOWED,
    ERROR_SEND_FAILED,
    ERROR_SESSION_MISSING,
    ERROR_UNVERIFIED_DEVICE,
    EVENT_COMMAND,
    EVENT_ERROR,
    NIO_DEFAULT_PICKLE_KEY,
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

_ENCRYPTED_EVENT_TYPES = frozenset(
    {"m.room.encrypted", "MegolmEvent", "OlmEvent", "EncryptedEvent"}
)


class SecretRedactFilter(logging.Filter):
    """Drop known session secrets from log records. Never log the secret list."""

    def __init__(self) -> None:
        super().__init__()
        self._secrets: tuple[str, ...] = ()

    def set_secrets(self, *values: str | None) -> None:
        # Skip short strings to avoid redacting ordinary words (e.g. "pw").
        self._secrets = tuple(
            value for value in values if isinstance(value, str) and len(value) >= 8
        )

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — never break logging
            return True
        redacted = message
        for secret in self._secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "[redacted]")
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_REDACT_FILTER = SecretRedactFilter()


_REDACT_LOGGER_NAMES = (
    "custom_components.matrix_e2ee",
    "custom_components.matrix_e2ee.client",
    "custom_components.matrix_e2ee.storage",
)


def _ensure_redact_filter() -> SecretRedactFilter:
    # Logger filters are not inherited by child loggers; attach to each module.
    for name in _REDACT_LOGGER_NAMES:
        logger = logging.getLogger(name)
        if _REDACT_FILTER not in logger.filters:
            logger.addFilter(_REDACT_FILTER)
    return _REDACT_FILTER


class MatrixE2EEError(Exception):
    """Fail-closed integration error with a public error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_error_response(response: Any) -> bool:
    return type(response).__name__.endswith("Error")


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def room_allowed(room_id: str, allowed_rooms: list[str]) -> bool:
    """Empty allowlist forbids every room."""
    return bool(allowed_rooms) and room_id in allowed_rooms


def user_allowed(user_id: str, allowed_users: list[str]) -> bool:
    """Empty allowlist forbids every command sender."""
    return bool(allowed_users) and user_id in allowed_users


def parse_command(body: str, prefix: str) -> tuple[str, list[str]] | None:
    """Return (command, args) if body starts with prefix; never return the raw body."""
    if not prefix or not body.startswith(prefix):
        return None
    rest = body[len(prefix) :].strip()
    if not rest:
        return None
    parts = rest.split()
    return parts[0], parts[1:]


def _nio_room(nio: Any, room_id: str) -> Any | None:
    rooms = getattr(nio, "rooms", None)
    if not isinstance(rooms, dict):
        return None
    return rooms.get(room_id)


def _olm_ready(nio: Any) -> bool:
    return getattr(nio, "olm", None) is not None


def _sync_since(nio: Any) -> str | None:
    for attr in ("next_batch", "loaded_sync_token"):
        value = getattr(nio, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _requires_verified_sender(room: Any, event: Any) -> bool:
    """Encrypted rooms and decrypted/ciphertext events require verified=True."""
    if getattr(room, "encrypted", False):
        return True
    if getattr(event, "decrypted", False):
        return True
    event_type = getattr(event, "type", None)
    if event_type in _ENCRYPTED_EVENT_TYPES:
        return True
    if type(event).__name__ in _ENCRYPTED_EVENT_TYPES:
        return True
    return False


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
        factory = self._nio_client_factory
        if factory is not None:
            return factory(
                self._homeserver,
                self._username,
                device_id=device_id or "",
                store_path=store,
                pickle_key=pickle_key,
                encryption_enabled=True,
                store_sync_tokens=True,
            )
        from nio import AsyncClient, AsyncClientConfig

        config = AsyncClientConfig(
            encryption_enabled=True,
            store_sync_tokens=True,
            pickle_key=pickle_key,
        )
        return AsyncClient(
            self._homeserver,
            self._username,
            device_id=device_id or "",
            store_path=store,
            config=config,
        )

    def _install_secret_filter(self) -> None:
        session = self.session
        values: list[str | None] = [self._password]
        if session is not None:
            values.extend([session.access_token, session.pickle_key])
        _ensure_redact_filter().set_secrets(*values)

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
        if pickle_key == NIO_DEFAULT_PICKLE_KEY:
            raise MatrixE2EEError(
                ERROR_LOGIN_FAILED,
                "refusing to use the SDK default pickle key",
            )
        nio = self._make_nio(pickle_key=pickle_key, device_id=None)
        self.nio = nio
        response = await nio.login(self._password, device_name=DEVICE_NAME)
        if _is_error_response(response):
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
        self._install_secret_filter()
        await self._upload_keys_if_needed()
        _LOGGER.info("matrix_e2ee first login succeeded; session stored")

    async def _restore(self, session: MatrixSession) -> None:
        if session.pickle_key == NIO_DEFAULT_PICKLE_KEY:
            raise MatrixE2EEError(
                ERROR_RESTORE_FAILED,
                "refusing to restore a session that used the SDK default pickle key",
            )
        nio = self._make_nio(
            pickle_key=session.pickle_key,
            device_id=session.device_id,
        )
        self.nio = nio
        nio.restore_login(session.user_id, session.device_id, session.access_token)
        whoami = await nio.whoami()
        if _is_error_response(whoami):
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
        self._install_secret_filter()
        await self._upload_keys_if_needed()
        _LOGGER.info("matrix_e2ee restored existing Matrix device")

    async def _upload_keys_if_needed(self) -> None:
        nio = self.nio
        if nio is None:
            return
        should_upload = getattr(nio, "should_upload_keys", False)
        upload = getattr(nio, "keys_upload", None)
        if should_upload and upload is not None:
            await _maybe_await(upload())

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
        await _maybe_await(close())

    def _require_nio(self) -> Any:
        if self.nio is None:
            raise MatrixE2EEError(ERROR_SESSION_MISSING, "matrix client is not started")
        return self.nio

    def _emit_error(self, code: str, **extra: Any) -> None:
        payload = {"code": code}
        payload.update(extra)
        self._fire_event(EVENT_ERROR, payload)

    async def async_send_message(self, room_id: str, message: str) -> None:
        """Send text to an allowlisted room. Never falls back to plaintext."""
        if not room_allowed(room_id, self.allowed_rooms):
            self._emit_error(ERROR_ROOM_NOT_ALLOWED, room_id=room_id)
            raise MatrixE2EEError(ERROR_ROOM_NOT_ALLOWED, "room is not allowlisted")
        nio = self._require_nio()
        room = _nio_room(nio, room_id)
        if room is not None and getattr(room, "encrypted", False) and not _olm_ready(nio):
            self._emit_error(ERROR_ENCRYPTION_UNAVAILABLE, room_id=room_id)
            raise MatrixE2EEError(
                ERROR_ENCRYPTION_UNAVAILABLE,
                "encrypted room requires olm; refusing plaintext fallback",
            )
        try:
            # Single attempt. Never retry while ignoring unverified devices.
            response = await nio.room_send(
                room_id,
                "m.room.message",
                {"msgtype": "m.text", "body": message},
                ignore_unverified_devices=False,
            )
        except Exception as err:  # noqa: BLE001 — map SDK failures, never log body
            code = _send_error_code(err)
            self._emit_error(code, room_id=room_id)
            raise MatrixE2EEError(code, "encrypted send failed") from err
        if _is_error_response(response):
            code = _send_error_code(response)
            self._emit_error(code, room_id=room_id)
            raise MatrixE2EEError(code, "encrypted send failed")

    def handle_incoming_event(self, room: Any, event: Any) -> None:
        """Process one inbound text event. Historical events are ignored until enabled."""
        if not self._commands_enabled:
            return
        sender = getattr(event, "sender", None)
        room_id = getattr(room, "room_id", None) or getattr(event, "room_id", None)
        if not sender or not room_id:
            return
        if self.session and sender == self.session.user_id:
            return
        requires_verified = _requires_verified_sender(room, event)
        verified = bool(getattr(event, "verified", False))
        decrypted = bool(getattr(event, "decrypted", False))
        body = getattr(event, "body", None)
        if requires_verified and not verified:
            if decrypted or isinstance(body, str):
                self._emit_error(
                    ERROR_UNVERIFIED_DEVICE,
                    room_id=room_id,
                    sender=sender,
                )
            return
        if not isinstance(body, str):
            return
        if not room_allowed(room_id, self.allowed_rooms):
            return
        if not user_allowed(sender, self.allowed_users):
            return
        parsed = parse_command(body, self.command_prefix)
        if parsed is None:
            return
        command, args = parsed
        self._fire_event(
            EVENT_COMMAND,
            {
                "room_id": room_id,
                "sender": sender,
                "command": command,
                "args": args,
            },
        )

    def enable_command_callbacks(self) -> None:
        """Register inbound handlers. Must not run before the first historical sync."""
        nio = self._require_nio()
        add = getattr(nio, "add_event_callback", None)
        if add is not None:
            event_type = _room_message_type()
            add(self.handle_incoming_event, event_type)
        self._commands_enabled = True

    async def async_sync_loop(self) -> None:
        """Initial sync without command replay; then incremental sync with callbacks."""
        nio = self._require_nio()
        since = _sync_since(nio)
        catch_up = self._first_setup or not since
        sync = getattr(nio, "sync", None)
        if catch_up and sync is not None:
            await _maybe_await(sync(timeout=30_000, full_state=True))
        self.enable_command_callbacks()
        sync_forever = getattr(nio, "sync_forever", None)
        if sync_forever is None:
            return
        kwargs: dict[str, Any] = {"timeout": 30_000}
        if since and not catch_up:
            kwargs["since"] = since
        await _maybe_await(sync_forever(**kwargs))


def _room_message_type() -> Any:
    try:
        from nio.events.room_events import RoomMessageText

        return RoomMessageText
    except Exception:  # noqa: BLE001 — tests may not install nio extras
        return None


def _send_error_code(err: Any) -> str:
    name = type(err).__name__.lower()
    text = str(err).lower()
    if "unverified" in name or "unverified" in text:
        return ERROR_UNVERIFIED_DEVICE
    if "encryption" in name or "olm" in name or "olm" in text:
        return ERROR_ENCRYPTION_UNAVAILABLE
    return ERROR_SEND_FAILED
