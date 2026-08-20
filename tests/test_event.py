"""HA event entity and inbound activity tests. No credentials."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.matrix_e2ee as matrix_e2ee
from custom_components.matrix_e2ee.const import (
    CONF_HOMESERVER,
    CONF_USERNAME,
    DOMAIN,
    EVENT_COMMAND,
    EVENT_MESSAGE_RECEIVED,
    EVENT_VERIFICATION_DONE,
)
from tests.fakes import FakeNio
from tests.test_binary_sensor import HS, USERNAME, _seed_session


@pytest.fixture
def hass_config_dir(tmp_path) -> str:
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations) -> None:
    """Make Home Assistant load this integration."""


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME},
    )
    entry.add_to_hass(hass)
    return entry


async def test_event_entity_receives_client_activity(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(matrix_e2ee, "_NIO_CLIENT_FACTORY", FakeNio)
    await _seed_session(tmp_path)
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client = hass.data[DOMAIN][entry.entry_id]
    client._record_activity("command")
    await hass.async_block_till_done()

    state = next(item for item in hass.states.async_all() if item.domain == "event")
    assert state.attributes["event_type"] == "command"
    assert set(state.attributes["event_types"]) == {
        "message",
        "command",
        "verification_done",
    }
    assert state.state != "unknown"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_accepted_message_fires_public_events_without_body(tmp_path) -> None:
    events: list[tuple[str, dict]] = []
    client = matrix_e2ee.MatrixE2EEClient(
        config_dir=tmp_path,
        homeserver=HS,
        username=USERNAME,
        password="pw",
        allowed_rooms=["!room:example.org"],
        allowed_users=["@admin:example.org"],
        verification_peer_users=[],
        command_prefix="!",
        fire_event=lambda name, data: events.append((name, data)),
        nio_client_factory=FakeNio,
    )
    await client.async_start()
    client._commands_enabled = True

    class Room:
        room_id = "!room:example.org"
        encrypted = False

    class Event:
        sender = "@admin:example.org"
        body = "!status secret-body"
        event_id = "$event"
        verified = True
        decrypted = False

    client.handle_incoming_event(Room(), Event())
    message = next(data for name, data in events if name == EVENT_MESSAGE_RECEIVED)
    command = next(data for name, data in events if name == EVENT_COMMAND)
    assert message == {
        "sender": "@admin:example.org",
        "room_id": "!room:example.org",
        "event_id": "$event",
    }
    assert command["command"] == "status"
    assert "body" not in str(message)
    await client.async_stop()


async def test_verification_done_emits_secret_free_activity(tmp_path) -> None:
    events: list[tuple[str, dict]] = []
    client = matrix_e2ee.MatrixE2EEClient(
        config_dir=tmp_path,
        homeserver=HS,
        username=USERNAME,
        password="pw",
        allowed_rooms=[],
        allowed_users=[],
        verification_peer_users=[],
        command_prefix="!",
        fire_event=lambda name, data: events.append((name, data)),
        nio_client_factory=FakeNio,
    )
    client._emit_verification("done", user_id="@peer:example.org", device_id="PEER")
    done = next(data for name, data in events if name == EVENT_VERIFICATION_DONE)
    assert done["peer_user_id"] == "@peer:example.org"
    assert done["peer_device_id"] == "PEER"
    assert "key" not in str(done).lower()
