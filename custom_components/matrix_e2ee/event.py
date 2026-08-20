"""Event platform exposing the bot's last accepted inbound activity."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import MatrixE2EEClient
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Matrix bot event entity."""
    client = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MatrixE2EEBotEvent(client, entry)])


class MatrixE2EEBotEvent(EventEntity):
    """Report the latest accepted command, message, or verified SAS completion."""

    _attr_has_entity_name = True
    _attr_name = "Bot activity"
    _attr_event_types: ClassVar[list[str]] = [
        "message",
        "command",
        "verification_done",
    ]

    def __init__(self, client: MatrixE2EEClient, entry: ConfigEntry) -> None:
        self._client = client
        self._remove_listener: Callable[[], None] | None = None
        self._attr_unique_id = f"{entry.entry_id}_bot_event"
        user_id = (
            client.session.user_id if client.session is not None else entry.unique_id
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Matrix E2EE bot ({user_id})",
            manufacturer="matrix-nio",
            model="matrix_e2ee",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has assigned an entity id."""
        await super().async_added_to_hass()
        self._remove_listener = self._client.add_activity_listener(self._on_activity)

    async def async_will_remove_from_hass(self) -> None:
        """Remove the client callback with the platform entity."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    def _on_activity(self, activity_type: str) -> None:
        self._trigger_event(activity_type)
        self.async_write_ha_state()
