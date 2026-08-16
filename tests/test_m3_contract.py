"""Mock nio SAS tests for the locked M3 contract. No real credentials."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.matrix_e2ee.client import MatrixE2EEClient, MatrixE2EEError
from custom_components.matrix_e2ee.const import (
    ERROR_DEVICE_MISSING,
    ERROR_INVALID_TRANSACTION,
    ERROR_UNVERIFIED_DEVICE,
    ERROR_VERIFICATION_TIMEOUT,
    ERROR_VERIFICATION_PEER_DENIED,
    EVENT_COMMAND,
    EVENT_ERROR,
    EVENT_VERIFICATION,
)
from tests.fakes import FakeNio, FakeSas

ROOM = "!roomid:example.org"
USER = "@admin:example.org"
BOT = "@ha-bot:example.org"
PEER_DEVICE = "ELEMENTABC"
TXN = "txn-elementabc"


class KeyVerificationStart:
    def __init__(self, sender, transaction_id, from_device, short_authentication_string=None):
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


class KeyVerificationCancel:
    def __init__(self, sender, transaction_id):
        self.sender = sender
        self.transaction_id = transaction_id
        self.type = "m.key.verification.cancel"
        self.reason = "user cancelled"


def _factory_holder():
    created: dict[str, FakeNio] = {}

    def factory(*args, **kwargs):
        nio = FakeNio(*args, **kwargs)
        nio.user_id = BOT
        created["nio"] = nio
        return nio

    return factory, created


def _client(tmp_path: Path, fire, factory):
    return MatrixE2EEClient(
        config_dir=tmp_path,
        homeserver="https://matrix.example.org",
        username=BOT,
        password="pw",
        allowed_rooms=[ROOM],
        allowed_users=[USER],
        command_prefix="!",
        fire_event=fire,
        nio_client_factory=factory,
    )


@pytest.mark.asyncio
async def test_encrypted_send_and_command_fail_before_sas(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    await client.async_sync_loop()
    nio = created["nio"]
    nio.rooms[ROOM] = SimpleNamespace(room_id=ROOM, encrypted=True)
    nio.add_device(USER, PEER_DEVICE, verified=False)

    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, "secret")
    assert err.value.code == ERROR_UNVERIFIED_DEVICE

    client.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=True),
        SimpleNamespace(sender=USER, body="!ping", decrypted=True, verified=False),
    )
    assert all(item[0] != EVENT_COMMAND for item in events)
    await client.async_stop()


@pytest.mark.asyncio
async def test_start_verification_missing_device(tmp_path):
    factory, _ = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_start_verification(USER, PEER_DEVICE)
    assert err.value.code == ERROR_DEVICE_MISSING
    assert any(item[0] == EVENT_ERROR and item[1]["code"] == ERROR_DEVICE_MISSING for item in events)
    await client.async_stop()


@pytest.mark.asyncio
async def test_outbound_sas_confirm_then_encrypted_send_and_command(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    await client.async_sync_loop()
    nio = created["nio"]
    nio.rooms[ROOM] = SimpleNamespace(room_id=ROOM, encrypted=True)
    nio.add_device(USER, PEER_DEVICE, verified=False)

    txn = await client.async_start_verification(USER, PEER_DEVICE)
    assert txn
    started = [item for item in events if item[0] == EVENT_VERIFICATION]
    assert started[-1][1]["stage"] == "started"
    assert started[-1][1]["user_id"] == USER
    assert started[-1][1]["device_id"] == PEER_DEVICE

    await client.handle_to_device_event(KeyVerificationKey(USER, txn))
    sas_events = [item[1] for item in events if item[0] == EVENT_VERIFICATION and item[1]["stage"] == "sas"]
    assert sas_events[-1]["emojis"] == [["⚓", "Anchor"], ["☎️", "Telephone"]]
    assert "body" not in sas_events[-1]

    await client.async_confirm_verification(txn)
    sas = nio.key_verifications[txn]
    sas.receive_mac()
    await client.handle_to_device_event(KeyVerificationMac(USER, txn))
    done = [item[1] for item in events if item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done"]
    assert done
    assert nio.device_store[USER][PEER_DEVICE].verified is True

    await client.async_send_message(ROOM, "hello")
    assert nio.sent[-1]["encrypted"] is True
    assert nio.sent[-1]["ignore_unverified_devices"] is False

    client.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=True),
        SimpleNamespace(sender=USER, body="!ping live", decrypted=True, verified=True),
    )
    commands = [item[1] for item in events if item[0] == EVENT_COMMAND]
    assert commands[-1]["command"] == "ping"
    await client.async_stop()


@pytest.mark.asyncio
async def test_inbound_sas_accept_is_not_trust_until_confirm(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    nio.rooms[ROOM] = SimpleNamespace(room_id=ROOM, encrypted=True)
    nio.add_device(USER, PEER_DEVICE, verified=False)
    nio.key_verifications[TXN] = FakeSas(TXN, USER, PEER_DEVICE, we_started_it=False)

    await client.handle_to_device_event(
        KeyVerificationStart(USER, TXN, PEER_DEVICE)
    )
    assert any(item[0] == EVENT_VERIFICATION and item[1]["stage"] == "started" for item in events)
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, "hello")
    assert err.value.code == ERROR_UNVERIFIED_DEVICE

    await client.async_confirm_verification(TXN)
    sas = nio.key_verifications[TXN]
    sas.receive_mac()
    await client.handle_to_device_event(KeyVerificationMac(USER, TXN))
    await client.async_send_message(ROOM, "hello")
    assert nio.sent[-1]["encrypted"] is True
    await client.async_stop()


@pytest.mark.asyncio
async def test_cancel_and_invalid_transaction(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    nio.add_device(USER, PEER_DEVICE)
    txn = await client.async_start_verification(USER, PEER_DEVICE)
    await client.async_cancel_verification(txn)
    canceled = [item[1] for item in events if item[0] == EVENT_VERIFICATION and item[1]["stage"] == "canceled"]
    assert canceled
    assert nio.device_store[USER][PEER_DEVICE].verified is False

    with pytest.raises(MatrixE2EEError) as err:
        await client.async_confirm_verification("missing-txn")
    assert err.value.code == ERROR_INVALID_TRANSACTION

    await client.handle_to_device_event(KeyVerificationCancel(USER, txn))
    await client.async_stop()


@pytest.mark.asyncio
async def test_verification_timeout(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    nio.add_device(USER, PEER_DEVICE)
    client._verification_timeout = 0
    txn = await client.async_start_verification(USER, PEER_DEVICE)
    client._sas_started_at[txn] = 0
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_confirm_verification(txn)
    assert err.value.code == ERROR_VERIFICATION_TIMEOUT
    assert any(item[0] == EVENT_VERIFICATION and item[1]["stage"] == "timeout" for item in events)
    await client.async_stop()


@pytest.mark.asyncio
async def test_mac_without_confirm_does_not_verify(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    nio.rooms[ROOM] = SimpleNamespace(room_id=ROOM, encrypted=True)
    nio.add_device(USER, PEER_DEVICE, verified=False)
    sas = FakeSas(TXN, USER, PEER_DEVICE)
    nio.key_verifications[TXN] = sas
    sas.receive_mac()
    await client.handle_to_device_event(KeyVerificationMac(USER, TXN))
    assert sas.verified is False
    assert all(not (item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done") for item in events)
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, "hello")
    assert err.value.code == ERROR_UNVERIFIED_DEVICE
    await client.async_stop()


@pytest.mark.asyncio
async def test_self_verification_auto_completes(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    nio.rooms[ROOM] = SimpleNamespace(room_id=ROOM, encrypted=True)
    nio.add_device(BOT, PEER_DEVICE, verified=False)
    sas = FakeSas(TXN, BOT, PEER_DEVICE, we_started_it=False)
    nio.key_verifications[TXN] = sas

    await client.handle_to_device_event(KeyVerificationStart(BOT, TXN, PEER_DEVICE))
    await client.handle_to_device_event(KeyVerificationKey(BOT, TXN))
    sas.receive_mac()
    await client.handle_to_device_event(KeyVerificationMac(BOT, TXN))

    assert sas.verified is True
    assert nio.device_store[BOT][PEER_DEVICE].verified is True
    assert any(
        item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done" for item in events
    )
    await client.async_stop()


@pytest.mark.asyncio
async def test_cross_user_mac_before_confirm_waits_for_admin(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    nio.rooms[ROOM] = SimpleNamespace(room_id=ROOM, encrypted=True)
    nio.add_device(USER, PEER_DEVICE, verified=False)
    sas = FakeSas(TXN, USER, PEER_DEVICE, we_started_it=False)
    nio.key_verifications[TXN] = sas

    await client.handle_to_device_event(KeyVerificationStart(USER, TXN, PEER_DEVICE))
    await client.handle_to_device_event(KeyVerificationKey(USER, TXN))
    sas.receive_mac()
    await client.handle_to_device_event(KeyVerificationMac(USER, TXN))

    assert sas.verified is False
    assert nio.device_store[USER][PEER_DEVICE].verified is False
    assert all(
        not (item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done")
        for item in events
    )

    await client.async_confirm_verification(TXN)
    assert sas.verified is True
    assert nio.device_store[USER][PEER_DEVICE].verified is True
    await client.async_stop()


@pytest.mark.asyncio
async def test_unknown_sender_verification_ignored(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    stranger = "@stranger:example.org"
    nio.add_device(stranger, PEER_DEVICE, verified=False)
    nio.key_verifications[TXN] = FakeSas(TXN, stranger, PEER_DEVICE)

    await client.handle_to_device_event(
        KeyVerificationStart(stranger, TXN, PEER_DEVICE)
    )

    assert all(
        not (item[0] == EVENT_VERIFICATION and item[1]["stage"] == "started")
        for item in events
    )
    assert any(
        item[0] == EVENT_ERROR and item[1]["code"] == ERROR_VERIFICATION_PEER_DENIED
        for item in events
    )
    await client.async_stop()
