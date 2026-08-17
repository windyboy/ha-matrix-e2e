"""Options flow tests using the Home Assistant test harness. No real credentials."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from custom_components.matrix_e2ee import config_flow as config_flow_module
from custom_components.matrix_e2ee.client import MatrixE2EEClient
from custom_components.matrix_e2ee.const import (
    CONF_ALLOWED_ROOMS,
    CONF_ALLOWED_USERS,
    CONF_COMMAND_PREFIX,
    CONF_HOMESERVER,
    CONF_USERNAME,
    DOMAIN,
    EVENT_VERIFICATION,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.fakes import FakeNio, FakeSas

HS = "https://matrix.example.org"
USERNAME = "@ha-bot:example.org"
PEER = "@admin:example.org"
PEER_DEVICE = "ELEMENTABC"
TXN = "txn-elementabc"


class KeyVerificationStart:
    def __init__(
        self, sender, transaction_id, from_device, short_authentication_string=None
    ):
        self.sender = sender
        self.transaction_id = transaction_id
        self.from_device = from_device
        self.short_authentication_string = short_authentication_string or ["emoji"]
        self.type = "m.key.verification.start"


class KeyVerificationKey:
    def __init__(self, sender, transaction_id):
        self.sender = sender
        self.transaction_id = transaction_id
        self.type = "m.key.verification.key"


class KeyVerificationMac:
    def __init__(self, sender, transaction_id):
        self.sender = sender
        self.transaction_id = transaction_id
        self.type = "m.key.verification.mac"


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


def _make_running_client(hass: HomeAssistant, tmp_path):
    created: dict[str, FakeNio] = {}

    def factory(homeserver, user, **kwargs):
        nio = FakeNio(homeserver, user, **kwargs)
        nio.user_id = USERNAME
        created["nio"] = nio
        return nio

    client = MatrixE2EEClient(
        config_dir=tmp_path,
        homeserver=HS,
        username=USERNAME,
        password="pw",
        allowed_rooms=[],
        allowed_users=[PEER],
        command_prefix="!",
        fire_event=lambda event_type, data: hass.bus.async_fire(event_type, data),
        nio_client_factory=factory,
    )
    return client, created


async def _seed_inbound_sas(client: MatrixE2EEClient, nio: FakeNio) -> None:
    """Register the peer device and SAS so the flow can capture it."""
    nio.add_device(PEER, PEER_DEVICE, verified=False)
    nio.key_verifications[TXN] = FakeSas(TXN, PEER, PEER_DEVICE, we_started_it=False)


async def _drive_peer_sas(client: MatrixE2EEClient) -> None:
    """Send the inbound start + key events that surface SAS emojis."""
    await client.handle_to_device_event(KeyVerificationStart(PEER, TXN, PEER_DEVICE))
    await client.handle_to_device_event(KeyVerificationKey(PEER, TXN))


async def test_options_flow_persists_and_schedules_reload(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _make_entry(hass)

    scheduled: list[str] = []
    monkeypatch.setattr(
        hass.config_entries,
        "async_schedule_reload",
        lambda entry_id: scheduled.append(entry_id),
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "access_controls"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "access_controls"

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


async def test_options_flow_clears_lists(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        result["flow_id"], {"next_step_id": "access_controls"}
    )
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


async def test_verify_device_aborts_without_running_client(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "verify_device"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "no_client"


async def test_verify_device_full_match_flow(hass: HomeAssistant, tmp_path) -> None:
    entry = _make_entry(hass)
    client, created = _make_running_client(hass, tmp_path)
    await client.async_start()
    nio = created["nio"]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    original_options = dict(entry.options)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "verify_device"}
    )
    assert result["type"] == "progress"
    assert result["step_id"] == "wait_sas"

    await _seed_inbound_sas(client, nio)
    await _drive_peer_sas(client)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] == "menu"
    assert result["step_id"] == "compare"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "match"}
    )
    assert result["type"] == "progress"
    assert result["step_id"] == "wait_done"

    nio.receive_verification_mac(TXN)
    await client.handle_to_device_event(KeyVerificationMac(PEER, TXN))
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] == "abort"
    assert result["reason"] == "verification_complete"

    assert entry.options == original_options
    await client.async_stop()


async def test_verify_device_done_race_mac_before_confirm(
    hass: HomeAssistant, tmp_path
) -> None:
    entry = _make_entry(hass)
    client, created = _make_running_client(hass, tmp_path)
    await client.async_start()
    nio = created["nio"]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "verify_device"}
    )
    assert result["type"] == "progress"

    await hass.async_block_till_done()
    await _seed_inbound_sas(client, nio)
    await _drive_peer_sas(client)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["step_id"] == "compare"

    # Peer's MAC arrives before our confirm: verifying it completes the SAS
    # synchronously inside async_confirm_verification, so we abort right away.
    nio.receive_verification_mac(TXN)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "match"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "verification_complete"

    await client.async_stop()


async def test_verify_device_mismatch_cancels(hass: HomeAssistant, tmp_path) -> None:
    entry = _make_entry(hass)
    client, created = _make_running_client(hass, tmp_path)
    await client.async_start()
    nio = created["nio"]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "verify_device"}
    )
    assert result["type"] == "progress"

    await hass.async_block_till_done()
    await _seed_inbound_sas(client, nio)
    await _drive_peer_sas(client)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["step_id"] == "compare"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "mismatch"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "verification_canceled"
    assert nio.key_verifications[TXN].canceled is True

    await client.async_stop()


async def test_verify_device_peer_timeout(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _make_entry(hass)
    client, created = _make_running_client(hass, tmp_path)
    await client.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    monkeypatch.setattr(config_flow_module, "VERIFICATION_TIMEOUT_SECONDS", 0.01)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "verify_device"}
    )
    assert result["type"] == "progress"

    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] == "abort"
    assert result["reason"] == "verification_timeout"

    await client.async_stop()
