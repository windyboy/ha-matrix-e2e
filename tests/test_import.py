"""YAML import (SOURCE_IMPORT) entry tests. No real credentials."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from custom_components.matrix_e2ee import async_setup
from custom_components.matrix_e2ee import config_flow
from custom_components.matrix_e2ee.client import MatrixE2EEClient
from custom_components.matrix_e2ee.const import (
    CONF_ALLOWED_ROOMS,
    CONF_ALLOWED_USERS,
    CONF_COMMAND_PREFIX,
    CONF_HOMESERVER,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFICATION_PEER_USERS,
    DOMAIN,
)
from custom_components.matrix_e2ee.storage import load_session
from tests.fakes import FakeNio

HS = "https://matrix.example.org"
USERNAME = "@ha-bot:example.org"
PASSWORD = "pw"


@pytest.fixture
def hass_config_dir(tmp_path) -> str:
    """Isolate the config dir per test so login sessions do not leak."""
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations) -> None:
    """Make HA's loader discover the custom_components package."""


@pytest.fixture(autouse=True)
def _inject_fake_nio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_flow, "_NIO_CLIENT_FACTORY", FakeNio)


async def _seed_session(tmp_path) -> None:
    """Create a full device session via a first login."""
    client = MatrixE2EEClient(
        config_dir=tmp_path,
        homeserver=HS,
        username=USERNAME,
        password=PASSWORD,
        allowed_rooms=[],
        allowed_users=[],
        verification_peer_users=[],
        command_prefix="!",
        fire_event=lambda event_type, data: None,
        nio_client_factory=FakeNio,
    )
    await client.async_start()
    await client.async_stop()


async def test_yaml_import_reuses_existing_crypto_store(
    hass: HomeAssistant, tmp_path
) -> None:
    """Migrating YAML must reuse the existing device session, not re-login."""
    await _seed_session(tmp_path)
    seed = load_session(tmp_path)
    assert seed is not None

    result = await async_setup(
        hass, {DOMAIN: {CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME}}
    )
    assert result is True
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.data == {CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME}
    assert CONF_PASSWORD not in entry.data

    after = load_session(tmp_path)
    assert after is not None
    # Same device identity: the crypto store was reused, not recreated.
    assert after.device_id == seed.device_id
    assert after.pickle_key == seed.pickle_key


async def test_yaml_import_first_login_with_password(
    hass: HomeAssistant, tmp_path
) -> None:
    """First run with a YAML password creates an entry with imported options."""
    result = await async_setup(
        hass,
        {
            DOMAIN: {
                CONF_HOMESERVER: HS,
                CONF_USERNAME: USERNAME,
                CONF_PASSWORD: PASSWORD,
                CONF_ALLOWED_ROOMS: ["!room:example.org"],
                CONF_ALLOWED_USERS: ["@admin:example.org"],
                CONF_COMMAND_PREFIX: "!",
            }
        },
    )
    assert result is True
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.data == {CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME}
    assert CONF_PASSWORD not in entry.data
    assert entry.options == {
        CONF_ALLOWED_ROOMS: ["!room:example.org"],
        CONF_ALLOWED_USERS: ["@admin:example.org"],
        CONF_VERIFICATION_PEER_USERS: [],
        CONF_COMMAND_PREFIX: "!",
    }


async def test_yaml_import_without_password_and_no_session(
    hass: HomeAssistant, tmp_path
) -> None:
    """Without a session or password the import aborts and creates no entry."""
    result = await async_setup(
        hass, {DOMAIN: {CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME}}
    )
    assert result is True
    await hass.async_block_till_done()

    assert hass.config_entries.async_entries(DOMAIN) == []
