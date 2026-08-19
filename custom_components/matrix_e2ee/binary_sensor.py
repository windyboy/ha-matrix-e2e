"""Binary sensor platform for matrix_e2ee connection health.

Strictly diagnostic: reports whether the bot's Matrix connection is up, whether
it is soft-logged-out, and the bot device id. No control actions, and no
secrets are ever exposed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import MatrixE2EEClient
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the matrix_e2ee connectivity sensor."""
    client = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MatrixE2EEConnectivitySensor(client, entry)])


class MatrixE2EEConnectivitySensor(BinarySensorEntity):
    """Binary sensor reporting the bot's Matrix connection health."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_should_poll = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client: MatrixE2EEClient, entry: ConfigEntry) -> None:
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_connection"
        user_id = (
            client.session.user_id if client.session is not None else entry.unique_id
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Matrix E2EE bot ({user_id})",
            manufacturer="matrix-nio",
            model="matrix_e2ee",
        )

    @property
    def is_on(self) -> bool:
        """Return True when the bot is connected (not soft-logged-out)."""
        return self._client.connection_health()["connected"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return non-secret connection attributes."""
        health = self._client.connection_health()
        health.pop("connected", None)
        return health
