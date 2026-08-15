"""YAML setup for matrix_e2ee. Unique domain; does not override built-in matrix."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .client import MatrixE2EEClient, MatrixE2EEError
from .const import (
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
)

_LOGGER = logging.getLogger(__name__)

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

    async def _on_stop(_event) -> None:
        await client.async_stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
    return True
