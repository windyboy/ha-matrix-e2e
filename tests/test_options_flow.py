"""Options flow tests using the Home Assistant test harness. No real credentials."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from custom_components.matrix_e2ee.client import MatrixE2EEClient
from custom_components.matrix_e2ee.const import (
    CONF_ALLOWED_ROOMS,
    CONF_ALLOWED_USERS,
    CONF_COMMAND_PREFIX,
    CONF_HOMESERVER,
    CONF_USERNAME,
    DOMAIN,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.fakes import FakeNio, FakeSas

HS = "https://matrix.example.org"
USERNAME = "@ha-bot:example.org"
PEER = "@admin:example.org"
PEER_DEVICE = "ELEMENTABC"
TXN = f"txn-{PEER_DEVICE}"


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


async def _setup_running(
    hass: HomeAssistant, tmp_path
) -> tuple[MockConfigEntry, MatrixE2EEClient, FakeNio]:
    """Create an entry with a running client bound to a fake nio instance."""
    entry = _make_entry(hass)
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
        fire_event=lambda event_type, data: None,
        nio_client_factory=factory,
    )
    await client.async_start()
    nio = created["nio"]
    nio.add_device(PEER, PEER_DEVICE, verified=False)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client
    return entry, client, nio


async def _open_verify_device(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """Navigate the options menu to the verify-device wait step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "verify_device"}
    )


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


async def test_verify_device_times_out_without_inbound_sas(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry, client, nio = await _setup_running(hass, tmp_path)
    monkeypatch.setattr(
        "custom_components.matrix_e2ee.config_flow.VERIFICATION_TIMEOUT_SECONDS",
        0.5,
    )

    result = await _open_verify_device(hass, entry)
    assert result["type"] == "progress"
    assert result["step_id"] == "wait_inbound"

    # No device ever initiates: the wait expires and the flow aborts.
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] == "abort"
    assert result["reason"] == "verification_timeout"

    await client.async_stop()


async def test_latest_sas_snapshot_skips_finished(
    hass: HomeAssistant, tmp_path
) -> None:
    entry, client, nio = await _setup_running(hass, tmp_path)

    canceled = FakeSas("txn-canceled", PEER, "DEV1", we_started_it=False)
    canceled.canceled = True
    nio.key_verifications["txn-canceled"] = canceled

    verified = FakeSas("txn-verified", PEER, "DEV2", we_started_it=False)
    verified.accept_sas()
    verified.receive_mac()
    nio.key_verifications["txn-verified"] = verified

    older = FakeSas("txn-older", PEER, "DEV0", we_started_it=False)
    nio.key_verifications["txn-older"] = older
    client._mark_sas_started("txn-older")

    active = FakeSas("txn-active", PEER, "DEV3", we_started_it=False)
    nio.key_verifications["txn-active"] = active
    client._mark_sas_started("txn-active")

    snapshot = client.latest_sas_snapshot()
    assert snapshot is not None
    assert snapshot["transaction_id"] == "txn-active"
    assert snapshot["device_id"] == "DEV3"

    # All finished → nothing to surface.
    active.canceled = True
    older.canceled = True
    assert client.latest_sas_snapshot() is None

    await client.async_stop()


async def test_verify_device_full_match_flow(hass: HomeAssistant, tmp_path) -> None:
    entry, client, nio = await _setup_running(hass, tmp_path)
    original_options = dict(entry.options)

    result = await _open_verify_device(hass, entry)
    assert result["type"] == "progress"
    assert result["step_id"] == "wait_inbound"

    # The peer initiates from Element: a start arrives and builds the SAS.
    nio.key_verifications[TXN] = FakeSas(TXN, PEER, PEER_DEVICE, we_started_it=False)
    await client.handle_to_device_event(KeyVerificationStart(PEER, TXN, PEER_DEVICE))
    await hass.async_block_till_done()

    # Peer shares its key → SAS emojis become available.
    await client.handle_to_device_event(KeyVerificationKey(PEER, TXN))
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] == "menu"
    assert result["step_id"] == "compare"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "match"}
    )
    assert result["type"] == "progress"
    assert result["step_id"] == "wait_done"

    # Peer's MAC completes the SAS.
    nio.receive_verification_mac(TXN)
    await client.handle_to_device_event(KeyVerificationMac(PEER, TXN))
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] == "abort"
    assert result["reason"] == "verification_complete"

    assert nio.device_store[PEER][PEER_DEVICE].verified is True
    assert entry.options == original_options
    # The wizard never broadcasts a verification request.
    assert all(item.get("op") != "request" for item in nio.to_device_sent)
    await client.async_stop()


async def test_verify_device_done_race_mac_before_confirm(
    hass: HomeAssistant, tmp_path
) -> None:
    entry, client, nio = await _setup_running(hass, tmp_path)

    result = await _open_verify_device(hass, entry)
    assert result["type"] == "progress"

    nio.key_verifications[TXN] = FakeSas(TXN, PEER, PEER_DEVICE, we_started_it=False)
    await client.handle_to_device_event(KeyVerificationStart(PEER, TXN, PEER_DEVICE))
    await hass.async_block_till_done()

    await client.handle_to_device_event(KeyVerificationKey(PEER, TXN))
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["step_id"] == "compare"

    # Peer's MAC arrives before our confirm: confirm completes the SAS
    # synchronously, so the flow aborts right away instead of waiting.
    nio.receive_verification_mac(TXN)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "match"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "verification_complete"
    assert nio.device_store[PEER][PEER_DEVICE].verified is True

    await client.async_stop()


async def test_verify_device_mismatch_cancels(hass: HomeAssistant, tmp_path) -> None:
    entry, client, nio = await _setup_running(hass, tmp_path)

    result = await _open_verify_device(hass, entry)
    assert result["type"] == "progress"

    nio.key_verifications[TXN] = FakeSas(TXN, PEER, PEER_DEVICE, we_started_it=False)
    await client.handle_to_device_event(KeyVerificationStart(PEER, TXN, PEER_DEVICE))
    await hass.async_block_till_done()

    await client.handle_to_device_event(KeyVerificationKey(PEER, TXN))
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["step_id"] == "compare"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "mismatch"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "verification_canceled"
    assert nio.key_verifications[TXN].canceled is True
    assert nio.device_store[PEER][PEER_DEVICE].verified is False

    await client.async_stop()
