"""Entry lifecycle (async_setup_entry / async_unload_entry) tests. No credentials."""

from __future__ import annotations

import asyncio

import pytest

from homeassistant.core import HomeAssistant

import custom_components.matrix_e2ee as matrix_e2ee
from custom_components.matrix_e2ee.client import MatrixE2EEClient
from custom_components.matrix_e2ee.const import (
    CONF_HOMESERVER,
    CONF_USERNAME,
    DOMAIN,
    SERVICE_REAUTHENTICATE,
    SERVICE_SEND_MESSAGE,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
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


class BlockingSyncNio(FakeNio):
    """FakeNio whose sync_forever never returns (mirrors real matrix-nio)."""

    async def sync_forever(self, timeout=0, since=None, **kwargs):
        self.sync_forever_calls.append(
            {"timeout": timeout, "since": since, "callback_count": len(self.callbacks)}
        )
        await asyncio.Event().wait()


async def test_setup_entry_registers_services_and_starts_sync(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(matrix_e2ee, "_NIO_CLIENT_FACTORY", FakeNio)
    await _seed_session(tmp_path)
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    client = hass.data[DOMAIN][entry.entry_id]
    assert client._sync_task is not None
    assert hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE)
    assert hass.services.has_service(DOMAIN, SERVICE_REAUTHENTICATE)

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE)


async def test_setup_entry_soft_logout_raises_auth_failed(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_session(tmp_path)

    def soft_factory(homeserver, user, **kwargs):
        nio = FakeNio(homeserver, user, **kwargs)
        nio.whoami_soft_logout = True
        return nio

    monkeypatch.setattr(matrix_e2ee, "_NIO_CLIENT_FACTORY", soft_factory)
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is False

    # The client stays reachable so the reauth flow can reauthenticate it.
    client = hass.data[DOMAIN][entry.entry_id]
    assert client._soft_logged_out is True


async def test_sync_loop_is_background_task(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """async_sync_loop must not block async_block_till_done (startup wrap-up)."""
    monkeypatch.setattr(matrix_e2ee, "_NIO_CLIENT_FACTORY", BlockingSyncNio)
    await _seed_session(tmp_path)
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True

    client = hass.data[DOMAIN][entry.entry_id]
    assert client._sync_task is not None

    # sync_forever never returns; a tracked task would hang async_block_till_done
    # for the full startup timeout. A background task must not.
    await asyncio.wait_for(hass.async_block_till_done(), timeout=1)

    assert await hass.config_entries.async_unload(entry.entry_id) is True
