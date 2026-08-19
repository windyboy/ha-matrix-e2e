"""Reauth and reconfigure flow tests. No real credentials."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matrix_e2ee import config_flow
from custom_components.matrix_e2ee.client import MatrixE2EEClient
from custom_components.matrix_e2ee.const import (
    CONF_HOMESERVER,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.matrix_e2ee.storage import session_path, store_path
from tests.fakes import FakeNio

HS = "https://matrix.example.org"
USERNAME = "@ha-bot:example.org"


@pytest.fixture
def hass_config_dir(tmp_path) -> str:
    """Isolate the HA config dir per test so quarantine targets tmp_path."""
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations) -> None:
    """Make HA's loader discover the custom_components package."""


@pytest.fixture(autouse=True)
def _inject_fake_nio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_flow, "_NIO_CLIENT_FACTORY", FakeNio)


def _factory(whoami_soft_logout: bool = False):
    created: dict[str, FakeNio] = {}

    def factory(homeserver, user, **kwargs):
        nio = FakeNio(homeserver, user, **kwargs)
        nio.user_id = USERNAME
        nio.whoami_soft_logout = whoami_soft_logout
        created["nio"] = nio
        return nio

    return factory, created


async def _make_client(tmp_path, whoami_soft_logout: bool = False):
    factory, created = _factory(whoami_soft_logout)
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
        nio_client_factory=factory,
    )
    return client, created


async def test_reauth_flow_reauthenticates_soft_logged_out_client(
    hass: HomeAssistant, tmp_path
) -> None:
    # Seed a session via first login.
    seed_client, _ = await _make_client(tmp_path)
    await seed_client.async_start()
    seed_device_id = seed_client.session.device_id
    await seed_client.async_stop()

    # Restore into a soft-logged-out state.
    client, _ = await _make_client(tmp_path, whoami_soft_logout=True)
    await client.async_start()
    assert client._soft_logged_out is True

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME},
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reauth"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert client._soft_logged_out is False
    assert client.session.device_id == seed_device_id

    await client.async_stop()


async def test_reauth_step_ignores_entry_data_without_password(
    hass: HomeAssistant,
) -> None:
    """HA invokes reauth with data=entry.data (no password); show the form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data={CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reauth"


async def test_reconfigure_same_origin_updates_homeserver_only(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: "https://matrix.example.org", CONF_USERNAME: USERNAME},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"

    # A trailing slash is the same origin: no new-device step is required.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOMESERVER: "https://matrix.example.org/"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOMESERVER] == "https://matrix.example.org"
    assert entry.data[CONF_USERNAME] == USERNAME


async def test_reconfigure_origin_change_quarantines_session_and_store(
    hass: HomeAssistant, tmp_path
) -> None:
    # Seed a session + store on disk, representing the bot's existing device.
    seed_client, _ = await _make_client(tmp_path)
    await seed_client.async_start()
    await seed_client.async_stop()
    assert session_path(tmp_path).exists()
    assert store_path(tmp_path).is_dir()

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: "https://old.example.org", CONF_USERNAME: USERNAME},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOMESERVER: "https://new.example.org"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure_new_device"

    # The old token/store must no longer sit where load_session reads them.
    assert not session_path(tmp_path).exists()
    assert not store_path(tmp_path).is_dir()


async def test_reconfigure_origin_change_requires_fresh_login(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: dict[str, FakeNio] = {}

    def factory(homeserver, user, **kwargs):
        nio = FakeNio(homeserver, user, **kwargs)
        nio.user_id = USERNAME
        created["nio"] = nio
        return nio

    monkeypatch.setattr(config_flow, "_NIO_CLIENT_FACTORY", factory)

    # Seed a session on disk, representing the bot's existing device.
    seed_client, _ = await _make_client(tmp_path)
    await seed_client.async_start()
    await seed_client.async_stop()
    assert session_path(tmp_path).exists()

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: "https://old.example.org", CONF_USERNAME: USERNAME},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOMESERVER: "https://new.example.org"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure_new_device"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "pw"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOMESERVER] == "https://new.example.org"

    # The old token was never restored against the new origin; a fresh login
    # happened instead.
    nio = created["nio"]
    assert nio.restore_called_with is None
    assert nio.login_calls
