"""Options flow tests using the Home Assistant test harness. No real credentials."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from custom_components.matrix_e2ee.const import (
    CONF_ALLOWED_ROOMS,
    CONF_ALLOWED_USERS,
    CONF_COMMAND_PREFIX,
    CONF_HOMESERVER,
    CONF_USERNAME,
    DOMAIN,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

HS = "https://matrix.example.org"
USERNAME = "@ha-bot:example.org"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations) -> None:
    """Make HA's loader discover the custom_components package."""


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME},
        options={
            CONF_ALLOWED_ROOMS: [],
            CONF_ALLOWED_USERS: [],
            CONF_COMMAND_PREFIX: "!",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_flow_persists_and_schedules_reload(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _make_entry(hass)

    scheduled: list[str] = []
    monkeypatch.setattr(
        hass.config_entries, "async_schedule_reload", lambda entry_id: scheduled.append(entry_id)
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ALLOWED_ROOMS: "!room1:example.org, !room2:example.org",
            CONF_ALLOWED_USERS: "@admin:example.org",
            CONF_COMMAND_PREFIX: "$",
        },
    )
    assert result["type"] == "create_entry"

    assert entry.options == {
        CONF_ALLOWED_ROOMS: ["!room1:example.org", "!room2:example.org"],
        CONF_ALLOWED_USERS: ["@admin:example.org"],
        CONF_COMMAND_PREFIX: "$",
    }
    assert scheduled == [entry.entry_id]


async def test_options_flow_clears_lists(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _make_entry(hass)
    monkeypatch.setattr(
        hass.config_entries, "async_schedule_reload", lambda entry_id: None
    )
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_ALLOWED_ROOMS: ["!old:example.org"],
            CONF_ALLOWED_USERS: ["@old:example.org"],
            CONF_COMMAND_PREFIX: "!",
        },
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ALLOWED_ROOMS: "",
            CONF_ALLOWED_USERS: "",
            CONF_COMMAND_PREFIX: "!",
        },
    )
    assert result["type"] == "create_entry"
    assert entry.options[CONF_ALLOWED_ROOMS] == []
    assert entry.options[CONF_ALLOWED_USERS] == []
