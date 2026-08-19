"""Diagnostics platform tests. No credentials."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.matrix_e2ee as matrix_e2ee
from custom_components.matrix_e2ee.client import MatrixE2EEClient
from custom_components.matrix_e2ee.const import CONF_HOMESERVER, CONF_USERNAME, DOMAIN
from custom_components.matrix_e2ee.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.fakes import FakeNio

HS = "https://matrix.example.org"
USERNAME = "@ha-bot:example.org"

# The exact set of non-secret fields safe_diagnostics() may return. Any extra
# field (token, pickle key, crypto material) fails this test.
SAFE_FIELDS = {
    "user_id",
    "device_id",
    "session_present",
    "store_present",
    "soft_logged_out",
    "encryption_enabled",
    "store_sync_tokens",
    "known_device_count",
    "verified_peer_count",
}


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


async def test_diagnostics_returns_redacted_snapshot(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(matrix_e2ee, "_NIO_CLIENT_FACTORY", FakeNio)
    await _seed_session(tmp_path)
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert isinstance(result, dict)
    assert set(result) == {"integration_version", "nio_version", "client"}

    client = hass.data[DOMAIN][entry.entry_id]
    assert result["client"] == client.safe_diagnostics()

    # Only the known-safe fields may appear; no token/pickle/crypto material.
    assert set(result["client"]) == SAFE_FIELDS
    assert result["client"]["user_id"] == USERNAME
    assert result["client"]["session_present"] is True
    assert result["client"]["encryption_enabled"] is True

    await hass.config_entries.async_unload(entry.entry_id)
