"""matrix-nio client lifecycle for matrix_e2ee (login / restore / shutdown)."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .const import (
    DEVICE_NAME,
    ERROR_DEVICE_MISMATCH,
    ERROR_DEVICE_MISSING,
    ERROR_ENCRYPTION_UNAVAILABLE,
    ERROR_INVALID_TRANSACTION,
    ERROR_LOGIN_FAILED,
    ERROR_PASSWORD_REQUIRED,
    ERROR_RESTORE_FAILED,
    ERROR_ROOM_NOT_ALLOWED,
    ERROR_SEND_FAILED,
    ERROR_SESSION_MISSING,
    ERROR_SOFT_LOGOUT,
    ERROR_HARD_LOGOUT,
    ERROR_REFRESH_TOKEN_UNSUPPORTED,
    ERROR_UNVERIFIED_DEVICE,
    ERROR_VERIFICATION_TIMEOUT,
    EVENT_COMMAND,
    EVENT_ERROR,
    EVENT_VERIFICATION,
    NIO_DEFAULT_PICKLE_KEY,
    VERIFICATION_TIMEOUT_SECONDS,
)
from .storage import (
    MatrixSession,
    SessionError,
    atomic_save_session,
    ensure_store_dir,
    load_session,
    store_path,
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


def _is_soft_logout(response: Any) -> bool:
    if bool(getattr(response, "soft_logout", False)):
        return True
    body = getattr(response, "body", None)
    if isinstance(body, dict) and bool(body.get("soft_logout")):
        return True
    return False


def _is_auth_failure(response: Any) -> bool:
    errcode = getattr(response, "errcode", None)
    if errcode == "M_UNKNOWN_TOKEN":
        return True
    status = getattr(response, "status_code", None)
    if status in (401, "401"):
        return True
    name = type(response).__name__.lower()
    text = str(response).lower()
    return "unauthorized" in name or "unknown_token" in text or "unknown token" in text


def _has_unsupported_token_lifetime(response: Any, nio: Any) -> bool:
    """v1 refuses refresh tokens and short-lived access tokens."""
    refresh = getattr(response, "refresh_token", None) or getattr(nio, "refresh_token", None)
    if isinstance(refresh, str) and refresh:
        return True
    expires = getattr(response, "expires_in_ms", None)
    if expires is None:
        expires = getattr(nio, "expires_in_ms", None)
    return isinstance(expires, (int, float)) and expires > 0


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
        self._verification_enabled = False
        self._sas_started_at: dict[str, float] = {}
        self._verification_timeout = VERIFICATION_TIMEOUT_SECONDS
        self._monotonic = time.monotonic
        self._soft_logged_out = False

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
        if not self._soft_logged_out:
            self.enable_verification_callbacks()

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
        if _has_unsupported_token_lifetime(response, nio):
            await self._close_nio()
            raise MatrixE2EEError(
                ERROR_REFRESH_TOKEN_UNSUPPORTED,
                "short-lived or refresh tokens are not supported; refusing to persist session",
            )
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
            if _is_soft_logout(whoami):
                self.session = session
                self._first_setup = False
                self._soft_logged_out = True
                self._install_secret_filter()
                self._emit_error(ERROR_SOFT_LOGOUT)
                _LOGGER.warning(
                    "matrix_e2ee soft logout; crypto store kept; call matrix_e2ee.reauthenticate"
                )
                return
            await self._close_nio()
            if _is_auth_failure(whoami):
                raise MatrixE2EEError(
                    ERROR_HARD_LOGOUT,
                    "hard logout; delete session and crypto store, then login as a new device",
                )
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

    def _reject_if_soft_logged_out(self) -> None:
        if self._soft_logged_out:
            self._emit_error(ERROR_SOFT_LOGOUT)
            raise MatrixE2EEError(
                ERROR_SOFT_LOGOUT,
                "session is soft-logged-out; call matrix_e2ee.reauthenticate",
            )

    def _restore_session_token(self, nio: Any, session: MatrixSession) -> None:
        restore = getattr(nio, "restore_login", None)
        if restore is not None:
            restore(session.user_id, session.device_id, session.access_token)
            return
        nio.user_id = session.user_id
        nio.device_id = session.device_id
        nio.access_token = session.access_token

    def safe_diagnostics(self) -> dict[str, Any]:
        """Return non-secret session/store status for the README runbook."""
        store = store_path(self._config_dir)
        try:
            store_present = store.is_dir()
        except OSError:
            store_present = False
        session = self.session
        return {
            "user_id": session.user_id if session is not None else None,
            "device_id": session.device_id if session is not None else None,
            "session_present": session is not None,
            "store_present": store_present,
            "soft_logged_out": self._soft_logged_out,
            "encryption_enabled": True,
            "store_sync_tokens": True,
        }

    async def async_reauthenticate(self, password: str) -> None:
        """Replace the access token after soft logout. Never creates a new device."""
        if not isinstance(password, str) or not password:
            raise MatrixE2EEError(
                ERROR_PASSWORD_REQUIRED,
                "password is required to reauthenticate",
            )
        session = self.session
        if session is None:
            raise MatrixE2EEError(ERROR_SESSION_MISSING, "matrix client is not started")
        nio = self._require_nio()
        previous_password = self._password
        self._password = password
        self._install_secret_filter()
        try:
            response = await nio.login(password, device_name=DEVICE_NAME)
        except Exception as err:  # noqa: BLE001 — never log the password
            self._password = previous_password
            self._install_secret_filter()
            self._emit_error(ERROR_LOGIN_FAILED)
            raise MatrixE2EEError(ERROR_LOGIN_FAILED, "reauthenticate login failed") from err
        if _is_error_response(response):
            self._restore_session_token(nio, session)
            self._password = previous_password
            self._install_secret_filter()
            self._emit_error(ERROR_LOGIN_FAILED)
            raise MatrixE2EEError(ERROR_LOGIN_FAILED, "reauthenticate login failed")
        if _has_unsupported_token_lifetime(response, nio):
            self._restore_session_token(nio, session)
            self._password = previous_password
            self._install_secret_filter()
            self._emit_error(ERROR_REFRESH_TOKEN_UNSUPPORTED)
            raise MatrixE2EEError(
                ERROR_REFRESH_TOKEN_UNSUPPORTED,
                "short-lived or refresh tokens are not supported",
            )
        if nio.device_id != session.device_id or nio.user_id != session.user_id:
            self._restore_session_token(nio, session)
            self._password = previous_password
            self._install_secret_filter()
            self._emit_error(ERROR_DEVICE_MISMATCH)
            raise MatrixE2EEError(
                ERROR_DEVICE_MISMATCH,
                "homeserver returned a different device; refusing to replace token",
            )
        if not nio.access_token:
            self._restore_session_token(nio, session)
            self._password = previous_password
            self._install_secret_filter()
            self._emit_error(ERROR_LOGIN_FAILED)
            raise MatrixE2EEError(ERROR_LOGIN_FAILED, "reauthenticate returned no access token")
        new_session = session.with_access_token(nio.access_token)
        atomic_save_session(self._config_dir, new_session)
        self.session = new_session
        self._soft_logged_out = False
        self._install_secret_filter()
        self.enable_verification_callbacks()
        _LOGGER.info("matrix_e2ee reauthenticate replaced access token; device unchanged")

    async def async_send_message(self, room_id: str, message: str) -> None:
        """Send text to an allowlisted room. Never falls back to plaintext."""
        if not room_allowed(room_id, self.allowed_rooms):
            self._emit_error(ERROR_ROOM_NOT_ALLOWED, room_id=room_id)
            raise MatrixE2EEError(ERROR_ROOM_NOT_ALLOWED, "room is not allowlisted")
        self._reject_if_soft_logged_out()
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
        if self._soft_logged_out or not self._commands_enabled:
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
        if self._soft_logged_out:
            return
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

    def enable_verification_callbacks(self) -> None:
        """Register to-device SAS handlers. Accepting the protocol is not auto-trust."""
        if self._verification_enabled:
            return
        nio = self._require_nio()
        add = getattr(nio, "add_to_device_callback", None)
        if add is not None:
            add(self.handle_to_device_event, _key_verification_event_type())
        self._verification_enabled = True

    def _emit_verification(self, stage: str, **extra: Any) -> None:
        payload = {"stage": stage}
        payload.update({key: value for key, value in extra.items() if value is not None})
        self._fire_event(EVENT_VERIFICATION, payload)

    def _lookup_device(self, nio: Any, user_id: str, device_id: str) -> Any | None:
        store = getattr(nio, "device_store", None)
        if store is None:
            return None
        try:
            user_devices = store[user_id]
        except (KeyError, TypeError, AttributeError):
            return None
        try:
            return user_devices[device_id]
        except (KeyError, TypeError, AttributeError):
            return None

    def _get_sas(self, nio: Any, transaction_id: str) -> Any | None:
        verifications = getattr(nio, "key_verifications", None)
        if not isinstance(verifications, dict):
            return None
        return verifications.get(transaction_id)

    def _sas_party(self, sas: Any, event: Any | None = None) -> tuple[str | None, str | None]:
        device = getattr(sas, "other_olm_device", None) if sas is not None else None
        user_id = getattr(device, "user_id", None) or (
            getattr(event, "sender", None) if event is not None else None
        )
        device_id = (
            getattr(device, "device_id", None)
            or getattr(device, "id", None)
            or (getattr(event, "from_device", None) if event is not None else None)
        )
        return user_id, device_id

    def _sas_emojis(self, sas: Any) -> list[list[str]] | None:
        get_emoji = getattr(sas, "get_emoji", None)
        if get_emoji is None:
            return None
        try:
            raw = get_emoji()
        except Exception:  # noqa: BLE001 — emoji is optional until keys are shared
            return None
        if not raw:
            return None
        emojis: list[list[str]] = []
        for item in raw:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                emojis.append([str(item[0]), str(item[1])])
        return emojis or None

    def _mark_sas_started(self, transaction_id: str) -> None:
        self._sas_started_at.setdefault(transaction_id, self._monotonic())

    def _sas_is_timed_out(self, nio: Any, transaction_id: str) -> bool:
        sas = self._get_sas(nio, transaction_id)
        if sas is not None and bool(getattr(sas, "timed_out", False)):
            return True
        started = self._sas_started_at.get(transaction_id)
        if started is None:
            return False
        return (self._monotonic() - started) >= self._verification_timeout

    async def _send_to_device(self, nio: Any, message: Any) -> Any:
        to_device = getattr(nio, "to_device", None)
        if to_device is None or message is None:
            return None
        return await _maybe_await(to_device(message))

    async def async_start_verification(self, user_id: str, device_id: str) -> str:
        """Start SAS with a known device. Does not mark the device trusted."""
        self._reject_if_soft_logged_out()
        nio = self._require_nio()
        device = self._lookup_device(nio, user_id, device_id)
        if device is None:
            self._emit_error(ERROR_DEVICE_MISSING, user_id=user_id, device_id=device_id)
            raise MatrixE2EEError(ERROR_DEVICE_MISSING, "device is not in the crypto store")
        start = getattr(nio, "start_key_verification", None)
        if start is None:
            self._emit_error(ERROR_ENCRYPTION_UNAVAILABLE, user_id=user_id, device_id=device_id)
            raise MatrixE2EEError(
                ERROR_ENCRYPTION_UNAVAILABLE, "key verification is unavailable"
            )
        try:
            response = await _maybe_await(start(device))
        except Exception as err:  # noqa: BLE001 — never log device keys
            code = _verification_error_code(err)
            self._emit_error(code, user_id=user_id, device_id=device_id)
            raise MatrixE2EEError(code, "start verification failed") from err
        if _is_error_response(response):
            code = _verification_error_code(response)
            self._emit_error(code, user_id=user_id, device_id=device_id)
            raise MatrixE2EEError(code, "start verification failed")
        transaction_id = _transaction_id_from_verifications(
            getattr(nio, "key_verifications", {}), user_id, device_id
        )
        if not transaction_id:
            self._emit_error(ERROR_INVALID_TRANSACTION, user_id=user_id, device_id=device_id)
            raise MatrixE2EEError(ERROR_INVALID_TRANSACTION, "verification transaction missing")
        self._mark_sas_started(transaction_id)
        self._emit_verification(
            "started",
            transaction_id=transaction_id,
            user_id=user_id,
            device_id=device_id,
        )
        return transaction_id

    async def async_confirm_verification(self, transaction_id: str) -> None:
        """Confirm SAS emojis match. This is the only path that verifies a device."""
        self._reject_if_soft_logged_out()
        nio = self._require_nio()
        if self._sas_is_timed_out(nio, transaction_id):
            await self._timeout_verification(nio, transaction_id)
            raise MatrixE2EEError(ERROR_VERIFICATION_TIMEOUT, "verification timed out")
        sas = self._get_sas(nio, transaction_id)
        if sas is None:
            self._emit_error(ERROR_INVALID_TRANSACTION, transaction_id=transaction_id)
            raise MatrixE2EEError(ERROR_INVALID_TRANSACTION, "unknown verification transaction")
        confirm = getattr(nio, "confirm_short_auth_string", None) or getattr(
            nio, "confirm_key_verification", None
        )
        if confirm is None:
            self._emit_error(ERROR_ENCRYPTION_UNAVAILABLE, transaction_id=transaction_id)
            raise MatrixE2EEError(
                ERROR_ENCRYPTION_UNAVAILABLE, "key verification is unavailable"
            )
        try:
            response = await _maybe_await(confirm(transaction_id))
        except Exception as err:  # noqa: BLE001 — never log SAS secrets
            code = _verification_error_code(err)
            self._emit_error(code, transaction_id=transaction_id)
            raise MatrixE2EEError(code, "confirm verification failed") from err
        if _is_error_response(response):
            code = _verification_error_code(response)
            self._emit_error(code, transaction_id=transaction_id)
            raise MatrixE2EEError(code, "confirm verification failed")
        user_id, device_id = self._sas_party(sas)
        if bool(getattr(sas, "verified", False)):
            self._sas_started_at.pop(transaction_id, None)
            self._emit_verification(
                "done",
                transaction_id=transaction_id,
                user_id=user_id,
                device_id=device_id,
            )
        else:
            self._emit_verification(
                "sas",
                transaction_id=transaction_id,
                user_id=user_id,
                device_id=device_id,
                emojis=self._sas_emojis(sas),
            )

    async def async_cancel_verification(self, transaction_id: str) -> None:
        """Cancel an in-progress SAS. Does not verify the device."""
        self._reject_if_soft_logged_out()
        nio = self._require_nio()
        sas = self._get_sas(nio, transaction_id)
        if sas is None:
            self._emit_error(ERROR_INVALID_TRANSACTION, transaction_id=transaction_id)
            raise MatrixE2EEError(ERROR_INVALID_TRANSACTION, "unknown verification transaction")
        user_id, device_id = self._sas_party(sas)
        cancel = getattr(nio, "cancel_key_verification", None)
        if cancel is not None:
            try:
                response = await _maybe_await(cancel(transaction_id, reject=False))
            except Exception as err:  # noqa: BLE001 — cancel must still emit
                code = _verification_error_code(err)
                self._emit_error(code, transaction_id=transaction_id)
                raise MatrixE2EEError(code, "cancel verification failed") from err
            if _is_error_response(response):
                code = _verification_error_code(response)
                self._emit_error(code, transaction_id=transaction_id)
                raise MatrixE2EEError(code, "cancel verification failed")
        self._sas_started_at.pop(transaction_id, None)
        self._emit_verification(
            "canceled",
            transaction_id=transaction_id,
            user_id=user_id,
            device_id=device_id,
        )

    async def _timeout_verification(self, nio: Any, transaction_id: str) -> None:
        sas = self._get_sas(nio, transaction_id)
        user_id, device_id = self._sas_party(sas) if sas is not None else (None, None)
        cancel = getattr(nio, "cancel_key_verification", None)
        if cancel is not None and sas is not None and not bool(getattr(sas, "canceled", False)):
            try:
                await _maybe_await(cancel(transaction_id, reject=False))
            except Exception:  # noqa: BLE001 — timeout path still reports timeout
                pass
        self._sas_started_at.pop(transaction_id, None)
        self._emit_error(ERROR_VERIFICATION_TIMEOUT, transaction_id=transaction_id)
        self._emit_verification(
            "timeout",
            transaction_id=transaction_id,
            user_id=user_id,
            device_id=device_id,
        )

    def _should_auto_confirm(self, sas: Any) -> bool:
        """Auto-confirm self-verification, or finish an already-confirmed SAS."""
        if bool(getattr(sas, "sas_accepted", False)):
            return True
        session = self.session
        if session is None:
            return False
        other_user = getattr(getattr(sas, "other_olm_device", None), "user_id", None)
        return other_user is not None and other_user == session.user_id

    async def _try_confirm(self, nio: Any, transaction_id: str) -> None:
        """Send our MAC and verify the device once the SAS is accepted."""
        confirm = getattr(nio, "confirm_short_auth_string", None) or getattr(
            nio, "confirm_key_verification", None
        )
        if confirm is None:
            return
        try:
            await _maybe_await(confirm(transaction_id))
        except Exception:  # noqa: BLE001 — protocol error is not trust
            return

    async def handle_to_device_event(self, event: Any) -> None:
        """Drive inbound SAS. Accepting the protocol is not device trust."""
        if self._soft_logged_out:
            return
        nio = self.nio
        if nio is None:
            return
        kind = _verification_kind(event)
        if not kind:
            return
        transaction_id = getattr(event, "transaction_id", None)
        if not isinstance(transaction_id, str) or not transaction_id:
            return
        if kind == "cancel":
            sas = self._get_sas(nio, transaction_id)
            user_id, device_id = self._sas_party(sas, event)
            self._sas_started_at.pop(transaction_id, None)
            self._emit_verification(
                "canceled",
                transaction_id=transaction_id,
                user_id=user_id,
                device_id=device_id,
            )
            return
        if self._sas_is_timed_out(nio, transaction_id):
            await self._timeout_verification(nio, transaction_id)
            return
        if kind == "start":
            methods = getattr(event, "short_authentication_string", None) or []
            if "emoji" not in methods:
                return
            accept = getattr(nio, "accept_key_verification", None)
            if accept is not None:
                try:
                    await _maybe_await(accept(transaction_id))
                except Exception:  # noqa: BLE001 — fail closed without trusting
                    self._emit_error(ERROR_INVALID_TRANSACTION, transaction_id=transaction_id)
                    return
            sas = self._get_sas(nio, transaction_id)
            share = getattr(sas, "share_key", None) if sas is not None else None
            if share is not None:
                try:
                    await self._send_to_device(nio, share())
                except Exception:  # noqa: BLE001 — do not log keys
                    self._emit_error(ERROR_SEND_FAILED, transaction_id=transaction_id)
                    return
            self._mark_sas_started(transaction_id)
            user_id, device_id = self._sas_party(sas, event)
            self._emit_verification(
                "started",
                transaction_id=transaction_id,
                user_id=user_id,
                device_id=device_id,
            )
            return
        sas = self._get_sas(nio, transaction_id)
        if sas is None:
            self._emit_error(ERROR_INVALID_TRANSACTION, transaction_id=transaction_id)
            return
        user_id, device_id = self._sas_party(sas, event)
        self._mark_sas_started(transaction_id)
        if kind == "key":
            self._emit_verification(
                "sas",
                transaction_id=transaction_id,
                user_id=user_id,
                device_id=device_id,
                emojis=self._sas_emojis(sas),
            )
            return
        if kind == "mac":
            if self._should_auto_confirm(sas):
                await self._try_confirm(nio, transaction_id)
            if bool(getattr(sas, "verified", False)):
                self._sas_started_at.pop(transaction_id, None)
                self._emit_verification(
                    "done",
                    transaction_id=transaction_id,
                    user_id=user_id,
                    device_id=device_id,
                )


def _key_verification_event_type() -> Any:
    try:
        from nio.events import KeyVerificationEvent

        return KeyVerificationEvent
    except Exception:  # noqa: BLE001 — tests may not install nio extras
        return None


def _verification_kind(event: Any) -> str:
    name = type(event).__name__
    by_name = {
        "KeyVerificationStart": "start",
        "KeyVerificationKey": "key",
        "KeyVerificationMac": "mac",
        "KeyVerificationCancel": "cancel",
    }
    if name in by_name:
        return by_name[name]
    event_type = getattr(event, "type", None)
    by_type = {
        "m.key.verification.start": "start",
        "m.key.verification.key": "key",
        "m.key.verification.mac": "mac",
        "m.key.verification.cancel": "cancel",
    }
    if isinstance(event_type, str) and event_type in by_type:
        return by_type[event_type]
    return ""


def _transaction_id_from_verifications(
    verifications: Any, user_id: str, device_id: str
) -> str | None:
    if not isinstance(verifications, dict) or not verifications:
        return None
    for txn, sas in verifications.items():
        device = getattr(sas, "other_olm_device", None)
        other_user = getattr(device, "user_id", None)
        other_device = getattr(device, "device_id", None) or getattr(device, "id", None)
        if other_user == user_id and other_device == device_id:
            return str(txn)
    if len(verifications) == 1:
        return str(next(iter(verifications)))
    return None


def _verification_error_code(err: Any) -> str:
    name = type(err).__name__.lower()
    text = str(err).lower()
    if "timeout" in name or "timeout" in text:
        return ERROR_VERIFICATION_TIMEOUT
    if "localprotocol" in name or "does not exist" in text or "transaction" in text:
        return ERROR_INVALID_TRANSACTION
    if "unverified" in name or "unverified" in text:
        return ERROR_UNVERIFIED_DEVICE
    return ERROR_SEND_FAILED


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

