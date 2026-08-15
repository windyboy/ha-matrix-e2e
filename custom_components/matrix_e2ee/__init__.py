"""YAML setup for matrix_e2ee. Unique domain; does not override built-in matrix."""

from __future__ import annotations

import logging

from .const import (
    ATTR_DEVICE_ID,
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
    DATA_CLIENT,
    DEFAULT_COMMAND_PREFIX,
    DOMAIN,
    EVENT_ERROR,
    SERVICE_CANCEL_VERIFICATION,
    SERVICE_CONFIRM_VERIFICATION,
    SERVICE_REAUTHENTICATE,
    SERVICE_SEND_MESSAGE,
    SERVICE_START_VERIFICATION,
)

_LOGGER = logging.getLogger(__name__)

try:
    import voluptuous as vol
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import config_validation as cv

    from .client import MatrixE2EEClient, MatrixE2EEError
except ImportError:  # pragma: no cover - unit tests without Home Assistant
    CONFIG_SCHEMA = None
    SEND_MESSAGE_SCHEMA = None

    async def async_setup(hass, config):  # type: ignore[no-untyped-def]
        """Home Assistant is required for YAML setup."""
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
    TRANSACTION_SCHEMA = vol.Schema({vol.Required(ATTR_TRANSACTION_ID): cv.string})
    REAUTHENTICATE_SCHEMA = vol.Schema({vol.Required(ATTR_PASSWORD): cv.string})

    def _fire_event(hass: HomeAssistant, event_type: str, data: dict) -> None:
        hass.bus.async_fire(event_type, data)

    async def async_setup(hass: HomeAssistant, config: dict) -> bool:
        """Set up matrix_e2ee from YAML."""
        conf = config[DOMAIN]
        client = MatrixE2EEClient(
            config_dir=hass.config.path(),
            homeserver=conf[CONF_HOMESERVER],
            username=conf[CONF_USERNAME],
            password=conf.get(CONF_PASSWORD),
            allowed_rooms=conf.get(CONF_ALLOWED_ROOMS, []),
            allowed_users=conf.get(CONF_ALLOWED_USERS, []),
            command_prefix=conf.get(CONF_COMMAND_PREFIX, DEFAULT_COMMAND_PREFIX),
            fire_event=lambda event_type, data: _fire_event(hass, event_type, data),
        )
        try:
            await client.async_start()
        except MatrixE2EEError as err:
            _LOGGER.error("matrix_e2ee setup failed: %s", err.code)
            _fire_event(hass, EVENT_ERROR, {"code": err.code})
            return False

        hass.data[DOMAIN] = {DATA_CLIENT: client}

        async def _handle_send(call) -> None:
            try:
                await client.async_send_message(
                    call.data[ATTR_ROOM_ID], call.data[ATTR_MESSAGE]
                )
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee send failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            _handle_send,
            schema=SEND_MESSAGE_SCHEMA,
        )

        async def _handle_start_verification(call) -> None:
            try:
                await client.async_start_verification(
                    call.data[ATTR_USER_ID], call.data[ATTR_DEVICE_ID]
                )
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee start_verification failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        async def _handle_confirm_verification(call) -> None:
            try:
                await client.async_confirm_verification(call.data[ATTR_TRANSACTION_ID])
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee confirm_verification failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        async def _handle_cancel_verification(call) -> None:
            try:
                await client.async_cancel_verification(call.data[ATTR_TRANSACTION_ID])
            except MatrixE2EEError as err:
                _LOGGER.error("matrix_e2ee cancel_verification failed: %s", err.code)
                _fire_event(hass, EVENT_ERROR, {"code": err.code})

        hass.services.async_register(
            DOMAIN,
            SERVICE_START_VERIFICATION,
            _handle_start_verification,
            schema=START_VERIFICATION_SCHEMA,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_CONFIRM_VERIFICATION,
            _handle_confirm_verification,
            schema=TRANSACTION_SCHEMA,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_CANCEL_VERIFICATION,
            _handle_cancel_verification,
            schema=TRANSACTION_SCHEMA,
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
                client._sync_task = hass.async_create_task(client.async_sync_loop())

        hass.services.async_register(
            DOMAIN,
            SERVICE_REAUTHENTICATE,
            _handle_reauthenticate,
            schema=REAUTHENTICATE_SCHEMA,
        )

        client._sync_task = hass.async_create_task(client.async_sync_loop())

        async def _on_stop(_event) -> None:
            await client.async_stop()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
        return True
