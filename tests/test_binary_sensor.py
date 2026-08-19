"""Binary sensor platform tests. No credentials."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.matrix_e2ee as matrix_e2ee
from custom_components.matrix_e2ee.binary_sensor import MatrixE2EEConnectivitySensor
from custom_components.matrix_e2ee.client import MatrixE2EEClient
from custom_components.matrix_e2ee.const import CONF_HOMESERVER, CONF_USERNAME, DOMAIN
from tests.fakes import FakeNio

HS = "https://matrix.example.org"
USERNAME = "@ha-bot:example.org"


@pytest.fixture
def hass_config_dir(tmp_path) -> str:
    """Isolate the config dir per test so login sessions do not leak."""
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations) -> None:
    """Make HA's loader discover the custom_components package."""


async def _seed_session(tmp_path) -> None:
    client = MatrixE2EEClient(
        config_dir=tmp_path,
        homeserver=HS,
        username=USERNAME,
        password="pw",
        allowed_rooms=[],
        allowed_users=[],
        verification_peer_users=[],
        command_prefix="!",
        fire_event=lambda event_type, data: None,
        nio_client_factory=FakeNio,
    )
    await client.async_start()
    await client.async_stop()


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME},
    )
    entry.add_to_hass(hass)
    return entry


def _binary_sensor_states(hass: HomeAssistant) -> list:
    return [s for s in hass.states.async_all() if s.domain == "binary_sensor"]


async def test_binary_sensor_reports_connected(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(matrix_e2ee, "_NIO_CLIENT_FACTORY", FakeNio)
    await _seed_session(tmp_path)
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    states = _binary_sensor_states(hass)
    assert len(states) == 1
    state = states[0]
    assert state.state == "on"

    attrs = state.attributes
    assert attrs.get("soft_logged_out") is False
    assert isinstance(attrs.get("device_id"), str)
    # No secrets may leak through the entity state.
    for secret in ("token", "pickle", "password", "secret"):
        assert secret not in str(attrs).lower()

    # A device is registered for the bot session.
    registry = dr.async_get(hass)
    assert any(
        any(ident[0] == DOMAIN for ident in device.identifiers)
        for device in registry.devices.values()
    )

    entity_id = state.entity_id

    # The connectivity sensor is a diagnostic entity.
    registry_entry = er.async_get(hass).entities[entity_id]
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    # Unloading leaves the diagnostic entity unavailable (the registry keeps it
    # for restore, but the integration no longer reports it).
    assert hass.states.get(entity_id).state == "unavailable"


async def test_connection_health_soft_logout(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(matrix_e2ee, "_NIO_CLIENT_FACTORY", FakeNio)
    await _seed_session(tmp_path)
    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    client = hass.data[DOMAIN][entry.entry_id]
    assert client.connection_health()["connected"] is True
    assert client.connection_health()["soft_logged_out"] is False

    client._soft_logged_out = True
    assert client.connection_health()["connected"] is False
    assert client.connection_health()["soft_logged_out"] is True

    await hass.config_entries.async_unload(entry.entry_id)


async def test_connection_health_reports_verified_peer_counts(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(matrix_e2ee, "_NIO_CLIENT_FACTORY", FakeNio)
    await _seed_session(tmp_path)
    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    client = hass.data[DOMAIN][entry.entry_id]

    # Empty store -> zero counts, empty peer list.
    empty = client.connection_health()
    assert empty["known_device_count"] == 0
    assert empty["verified_peer_count"] == 0
    assert empty["verified_peers"] == []

    # Two peer devices, one verified. The bot's own device is never counted.
    nio = client.nio
    nio.add_device("@peer1:example.org", "PEER1ABC", verified=False)
    nio.add_device("@peer2:example.org", "PEER2ABC", verified=True)

    health = client.connection_health()
    assert health["known_device_count"] == 2
    assert health["verified_peer_count"] == 1
    assert health["verified_peers"] == [
        {"user_id": "@peer2:example.org", "device_id": "PEER2ABC"}
    ]

    # The sensor mirrors the counts and never leaks key material.
    sensor = MatrixE2EEConnectivitySensor(client, entry)
    attrs = sensor.extra_state_attributes
    assert attrs["known_device_count"] == 2
    assert attrs["verified_peer_count"] == 1
    for secret in ("ed25519", "curve25519", "key", "token", "pickle", "secret"):
        assert secret not in str(attrs["verified_peers"]).lower()

    await hass.config_entries.async_unload(entry.entry_id)
