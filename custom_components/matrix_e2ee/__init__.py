"""Setup for matrix_e2ee: Config Flow with a YAML import shim.

Unique domain; does not override the built-in ``matrix`` integration.
"""

from __future__ import annotations

import logging
from typing import Any

from .const import (
    ATTR_DEVICE_ID,
    ATTR_ED25519,
    ATTR_MESSAGE,
    ATTR_PASSWORD,
    ATTR_ROOM_ID,
    ATTR_TRANSACTION_ID,
    ATTR_USER_ID,
    CONF_ALLOWED_ROOMS,
    CONF_ALLOWED_USERS,
    CONF_COMMAND_PREFIX,
    CONF_HOMESERVER,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_COMMAND_PREFIX,
    DOMAIN,
    EVENT_ERROR,
    EVENT_FINGERPRINT,
    SERVICE_CANCEL_VERIFICATION,
    SERVICE_CONFIRM_VERIFICATION,
    SERVICE_GET_FINGERPRINT,
    SERVICE_REAUTHENTICATE,
    SERVICE_SEND_MESSAGE,
    SERVICE_START_VERIFICATION,
    SERVICE_VERIFY_DEVICE_BY_FINGERPRINT,
)

_LOGGER = logging.getLogger(__name__)

# Tests patch this to inject a fake nio client (no real network or crypto).
_NIO_CLIENT_FACTORY: Any = None

_ALL_SERVICES = (
    SERVICE_SEND_MESSAGE,
    SERVICE_START_VERIFICATION,
    SERVICE_CONFIRM_VERIFICATION,
    SERVICE_CANCEL_VERIFICATION,
    SERVICE_GET_FINGERPRINT,
    SERVICE_VERIFY_DEVICE_BY_FINGERPRINT,
    SERVICE_REAUTHENTICATE,
)

try:
    import voluptuous as vol
    from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers.service import async_register_admin_service

    from .client import MatrixE2EEClient, MatrixE2EEError
except ImportError:  # pragma: no cover - unit tests without Home Assistant
    CONFIG_SCHEMA = None
    SEND_MESSAGE_SCHEMA = None

    async def async_setup(hass, config):  # type: ignore[no-untyped-def]
        """Home Assistant is required for YAML setup."""
        raise RuntimeError("Home Assistant is required to set up matrix_e2ee")

    async def async_setup_entry(hass, entry):  # type: ignore[no-untyped-def]
        """Home Assistant is required for entry setup."""
        raise RuntimeError("Home Assistant is required to set up matrix_e2ee")

    async def async_unload_entry(hass, entry):  # type: ignore[no-untyped-def]
        """Home Assistant is required for entry unload."""
        raise RuntimeError("Home Assistant is required to set up matrix_e2ee")
else:
    CONFIG_SCHEMA = vol.Schema(
        {
            DOMAIN: vol.Schema(
                {
                    vol.Required(CONF_HOMESERVER): cv.string,
                    vol.Required(CONF_USERNAME): cv.string,
                    vol.Optional(CONF_PASSWORD): cv.string,
                    vol.Optional(CONF_ALLOWED_ROOMS, default=list): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                    vol.Optional(CONF_ALLOWED_USERS, default=list): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                    vol.Optional(
                        CONF_COMMAND_PREFIX, default=DEFAULT_COMMAND_PREFIX
                    ): cv.string,
                }
            )
        },
        extra=vol.ALLOW_EXTRA,
    )

    SEND_MESSAGE_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Required(ATTR_ROOM_ID): cv.string,
        }
    )
    START_VERIFICATION_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_USER_ID): cv.string,
            vol.Required(ATTR_DEVICE_ID): cv.string,
        }
    )
    VERIFY_DEVICE_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_USER_ID): cv.string,
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_ED25519): cv.string,
        }
    )
    TRANSACTION_SCHEMA = vol.Schema({vol.Required(ATTR_TRANSACTION_ID): cv.string})
    REAUTHENTICATE_SCHEMA = vol.Schema({vol.Required(ATTR_PASSWORD): cv.string})

    def _fire_event(hass: HomeAssistant, event_type: str, data: dict) -> None:
        hass.bus.async_fire(event_type, data)

    def _options(entry: ConfigEntry) -> dict:
        """Read access-control options from an entry with safe defaults."""
        return {
            CONF_ALLOWED_ROOMS: entry.options.get(CONF_ALLOWED_ROOMS, []),
            CONF_ALLOWED_USERS: entry.options.get(CONF_ALLOWED_USERS, []),
            CONF_COMMAND_PREFIX: entry.options.get(
                CONF_COMMAND_PREFIX, DEFAULT_COMMAND_PREFIX
            ),
        }

    def _register_services(hass: HomeAssistant, client: MatrixE2EEClient) -> None:
        """Register all matrix_e2ee services bound to a client instance."""

        async def _handle_send(call) -> None:
            try:
                await client.async_send_message(
                    call.data[ATTR_ROOM_ID], call.data[ATTR_MESSAGE]
                )
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee send failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        hass.services.async_register(
            DOMAIN, SERVICE_SEND_MESSAGE, _handle_send, schema=SEND_MESSAGE_SCHEMA
        )

        async def _handle_start_verification(call) -> None:
            try:
                await client.async_start_verification(
                    call.data[ATTR_USER_ID], call.data[ATTR_DEVICE_ID]
                )
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee start_verification failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        async_register_admin_service(
            hass,
            DOMAIN,
            SERVICE_START_VERIFICATION,
            _handle_start_verification,
            schema=START_VERIFICATION_SCHEMA,
        )

        async def _handle_confirm_verification(call) -> None:
            try:
                await client.async_confirm_verification(call.data[ATTR_TRANSACTION_ID])
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee confirm_verification failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        async_register_admin_service(
            hass,
            DOMAIN,
            SERVICE_CONFIRM_VERIFICATION,
            _handle_confirm_verification,
            schema=TRANSACTION_SCHEMA,
        )

        async def _handle_cancel_verification(call) -> None:
            try:
                await client.async_cancel_verification(call.data[ATTR_TRANSACTION_ID])
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee cancel_verification failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        async_register_admin_service(
            hass,
            DOMAIN,
            SERVICE_CANCEL_VERIFICATION,
            _handle_cancel_verification,
            schema=TRANSACTION_SCHEMA,
        )

        async def _handle_get_fingerprint(call) -> None:
            _fire_event(hass, EVENT_FINGERPRINT, client.safe_fingerprint() or {})

        hass.services.async_register(
            DOMAIN, SERVICE_GET_FINGERPRINT, _handle_get_fingerprint
        )

        async def _handle_verify_device_by_fingerprint(call) -> None:
            try:
                await client.async_verify_device_by_fingerprint(
                    call.data[ATTR_USER_ID],
                    call.data[ATTR_DEVICE_ID],
                    call.data[ATTR_ED25519],
                )
            except MatrixE2EEError as err:
                _LOGGER.error(
                    "matrix_e2ee verify_device_by_fingerprint failed: %s", err.code
                )
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        async_register_admin_service(
            hass,
            DOMAIN,
            SERVICE_VERIFY_DEVICE_BY_FINGERPRINT,
            _handle_verify_device_by_fingerprint,
            schema=VERIFY_DEVICE_SCHEMA,
        )

        async def _handle_reauthenticate(call) -> None:
            try:
                await client.async_reauthenticate(call.data[ATTR_PASSWORD])
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee reauthenticate failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})
                return
            task = client._sync_task
            if task is None or getattr(task, "done", lambda: True)():
                # Background task — does not block bootstrap or shutdown.
                client._sync_task = hass.async_create_background_task(
                    client.async_sync_loop(), "matrix_e2ee_sync_loop"
                )

        async_register_admin_service(
            hass,
            DOMAIN,
            SERVICE_REAUTHENTICATE,
            _handle_reauthenticate,
            schema=REAUTHENTICATE_SCHEMA,
        )

    def _unregister_services(hass: HomeAssistant) -> None:
        """Remove all matrix_e2ee services if present."""
        for service in _ALL_SERVICES:
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)

    async def async_setup(hass: HomeAssistant, config: dict) -> bool:
        """Forward a YAML configuration block to the import config flow."""
        if DOMAIN not in config:
            return True
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=dict(config[DOMAIN]),
            )
        )
        return True

    async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry
    ) -> bool:
        """Set up matrix_e2ee from a config entry."""
        options = _options(entry)
        client = MatrixE2EEClient(
            config_dir=hass.config.path(),
            homeserver=entry.data[CONF_HOMESERVER],
            username=entry.data[CONF_USERNAME],
            password=None,
            allowed_rooms=options[CONF_ALLOWED_ROOMS],
            allowed_users=options[CONF_ALLOWED_USERS],
            command_prefix=options[CONF_COMMAND_PREFIX],
            fire_event=lambda event_type, data: _fire_event(hass, event_type, data),
            nio_client_factory=_NIO_CLIENT_FACTORY,
        )

        # Store before starting so the reauth flow can reach the client even
        # when setup fails with a soft logout below.
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

        try:
            await client.async_start()
        except MatrixE2EEError as err:
            raise ConfigEntryAuthFailed(
                f"matrix_e2ee setup failed: {err.code}"
            ) from err

        if client._soft_logged_out:
            raise ConfigEntryAuthFailed(
                "Matrix access token is soft-logged-out; re-authenticate to continue"
            )

        _unregister_services(hass)
        _register_services(hass, client)

        fingerprint = client.safe_fingerprint()
        if fingerprint:
            _fire_event(hass, EVENT_FINGERPRINT, fingerprint)

        # Background task — does not block bootstrap or shutdown.
        client._sync_task = hass.async_create_background_task(
            client.async_sync_loop(), "matrix_e2ee_sync_loop"
        )

        async def _on_stop(_event) -> None:
            await client.async_stop()

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
        )
        return True

    async def async_unload_entry(
        hass: HomeAssistant, entry: ConfigEntry
    ) -> bool:
        """Unload a config entry."""
        client = hass.data.setdefault(DOMAIN, {}).pop(entry.entry_id, None)
        if client is not None:
            await client.async_stop()
        _unregister_services(hass)
        return True
