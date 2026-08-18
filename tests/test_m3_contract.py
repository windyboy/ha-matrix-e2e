"""Mock nio SAS tests for the locked M3 contract. No real credentials."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from custom_components.matrix_e2ee import client as client_module
from custom_components.matrix_e2ee.client import (
    MatrixE2EEClient,
    MatrixE2EEError,
    _apply_sas_commitment_patch,
    _apply_sas_emoji_patch,
    _apply_sas_mac_patch,
    _apply_sas_timeout_patch,
    _sas_commitment,
)
from custom_components.matrix_e2ee.const import (
    ERROR_DEVICE_MISSING,
    ERROR_FINGERPRINT_MISMATCH,
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


class KeyVerificationCancel:
    def __init__(self, sender, transaction_id):
        self.sender = sender
        self.transaction_id = transaction_id
        self.type = "m.key.verification.cancel"
        self.reason = "user cancelled"


def _now_ms():
    return int(time.time() * 1000)


class VerificationRequest:
    """Model nio's UnknownToDeviceEvent for an m.key.verification.request."""

    def __init__(
        self, sender, from_device, transaction_id, methods=None, timestamp=None
    ):
        content = {
            "from_device": from_device,
            "transaction_id": transaction_id,
            "methods": methods if methods is not None else ["m.sas.v1"],
            "timestamp": timestamp if timestamp is not None else _now_ms(),
        }
        self.source = {
            "type": "m.key.verification.request",
            "sender": sender,
            "content": content,
        }
        self.sender = sender
        self.type = "m.key.verification.request"


def _fake_to_device_message(type_, recipient, recipient_device, content):
    """Stand-in for nio's ToDeviceMessage (tests do not install nio)."""
    return SimpleNamespace(
        type=type_,
        recipient=recipient,
        recipient_device=recipient_device,
        content=content,
        as_dict=lambda: {"messages": {recipient: {recipient_device: content}}},
    )


def _sent_readies(nio):
    return [
        s["message"]
        for s in nio.to_device_sent
        if s["op"] == "to_device"
        and getattr(s["message"], "type", None) == "m.key.verification.ready"
    ]


def _sent_dones(nio):
    return [
        s["message"]
        for s in nio.to_device_sent
        if s["op"] == "to_device"
        and getattr(s["message"], "type", None) == "m.key.verification.done"
    ]


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
    assert any(
        item[0] == EVENT_ERROR and item[1]["code"] == ERROR_DEVICE_MISSING
        for item in events
    )
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
    sas_events = [
        item[1]
        for item in events
        if item[0] == EVENT_VERIFICATION and item[1]["stage"] == "sas"
    ]
    assert sas_events[-1]["emojis"] == [["⚓", "Anchor"], ["☎️", "Telephone"]]
    assert "body" not in sas_events[-1]
    assert "expires_at" in sas_events[-1]

    await client.async_confirm_verification(txn)
    nio.receive_verification_mac(txn)
    await client.handle_to_device_event(KeyVerificationMac(USER, txn))
    done = [
        item[1]
        for item in events
        if item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done"
    ]
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

    await client.handle_to_device_event(KeyVerificationStart(USER, TXN, PEER_DEVICE))
    assert any(
        item[0] == EVENT_VERIFICATION and item[1]["stage"] == "started"
        for item in events
    )
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, "hello")
    assert err.value.code == ERROR_UNVERIFIED_DEVICE

    await client.async_confirm_verification(TXN)
    nio.receive_verification_mac(TXN)
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
    canceled = [
        item[1]
        for item in events
        if item[0] == EVENT_VERIFICATION and item[1]["stage"] == "canceled"
    ]
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
    assert any(
        item[0] == EVENT_VERIFICATION and item[1]["stage"] == "timeout"
        for item in events
    )
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
    nio.receive_verification_mac(TXN)
    await client.handle_to_device_event(KeyVerificationMac(USER, TXN))
    assert sas.verified is False
    assert all(
        not (item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done")
        for item in events
    )
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, "hello")
    assert err.value.code == ERROR_UNVERIFIED_DEVICE
    await client.async_stop()


@pytest.mark.asyncio
async def test_self_verification_requires_admin_confirm(tmp_path):
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
    nio.receive_verification_mac(TXN)
    await client.handle_to_device_event(KeyVerificationMac(BOT, TXN))

    assert sas.verified is False
    assert nio.device_store[BOT][PEER_DEVICE].verified is False
    assert all(
        not (item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done")
        for item in events
    )

    await client.async_confirm_verification(TXN)
    assert sas.verified is True
    assert nio.device_store[BOT][PEER_DEVICE].verified is True
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
    nio.receive_verification_mac(TXN)
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


@pytest.mark.asyncio
async def test_safe_fingerprint_exposes_public_keys(tmp_path):
    factory, _ = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    await client.async_start()
    fp = client.safe_fingerprint()
    assert fp is not None
    assert fp["user_id"] == BOT
    assert fp["ed25519"] == "ED25519_PUB_KEY"
    assert fp["curve25519"] == "CURVE25519_PUB_KEY"
    await client.async_stop()


@pytest.mark.asyncio
async def test_verify_device_by_fingerprint_requires_match(tmp_path):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    await client.async_start()
    nio = created["nio"]
    nio.add_device(USER, PEER_DEVICE, verified=False)

    with pytest.raises(MatrixE2EEError) as exc:
        await client.async_verify_device_by_fingerprint(USER, PEER_DEVICE, "wrong-key")
    assert exc.value.code == ERROR_FINGERPRINT_MISMATCH
    assert nio.device_store[USER][PEER_DEVICE].verified is False

    await client.async_verify_device_by_fingerprint(
        USER, PEER_DEVICE, "ED25519_DEVICE_KEY"
    )
    assert nio.device_store[USER][PEER_DEVICE].verified is True
    await client.async_stop()


@pytest.mark.asyncio
async def test_verify_device_by_fingerprint_is_case_sensitive(tmp_path):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    await client.async_start()
    nio = created["nio"]
    nio.add_device(USER, PEER_DEVICE, verified=False)

    # Unpadded Base64 is case-sensitive: a case-only difference is a different key.
    with pytest.raises(MatrixE2EEError) as exc:
        await client.async_verify_device_by_fingerprint(
            USER, PEER_DEVICE, "ed25519_device_key"
        )
    assert exc.value.code == ERROR_FINGERPRINT_MISMATCH
    assert nio.device_store[USER][PEER_DEVICE].verified is False
    await client.async_stop()


@pytest.mark.asyncio
async def test_first_login_queries_own_device_keys(tmp_path):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    await client.async_start()
    nio = created["nio"]
    assert BOT in nio.olm.users_for_key_query
    assert nio.keys_query_calls == [BOT]
    await client.async_stop()


@pytest.mark.asyncio
async def test_restore_queries_own_device_keys(tmp_path):
    factory, _ = _factory_holder()
    first = _client(tmp_path, lambda t, d: None, factory)
    await first.async_start()
    await first.async_stop()

    factory2, created2 = _factory_holder()
    second = _client(tmp_path, lambda t, d: None, factory2)
    await second.async_start()
    nio2 = created2["nio"]
    assert BOT in nio2.olm.users_for_key_query
    assert nio2.keys_query_calls == [BOT]
    await second.async_stop()


@pytest.mark.asyncio
async def test_keys_query_failure_does_not_block_setup(tmp_path):
    created: dict[str, FakeNio] = {}

    def factory(*args, **kwargs):
        nio = FakeNio(*args, **kwargs)
        nio.user_id = BOT

        async def fail_keys_query():
            raise RuntimeError("keys query failed")

        nio.keys_query = fail_keys_query
        created["nio"] = nio
        return nio

    client = _client(tmp_path, lambda t, d: None, factory)
    await client.async_start()
    nio = created["nio"]
    assert BOT in nio.olm.users_for_key_query
    await client.async_stop()


@pytest.mark.asyncio
async def test_inbound_start_accepts_without_sharing_key_directly(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    nio.add_device(USER, PEER_DEVICE, verified=False)
    nio.key_verifications[TXN] = FakeSas(TXN, USER, PEER_DEVICE, we_started_it=False)

    await client.handle_to_device_event(KeyVerificationStart(USER, TXN, PEER_DEVICE))

    # The bot must accept, but NOT share its key directly: nio's internal state
    # machine shares it when the peer's key arrives (avoiding a double-send).
    assert [s["op"] for s in nio.to_device_sent] == ["accept"]
    await client.async_stop()


@pytest.mark.asyncio
async def test_confirm_sends_mac_exactly_once(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    nio.add_device(USER, PEER_DEVICE, verified=False)
    nio.key_verifications[TXN] = FakeSas(TXN, USER, PEER_DEVICE, we_started_it=False)

    await client.async_confirm_verification(TXN)
    nio.receive_verification_mac(TXN)
    await client.handle_to_device_event(KeyVerificationMac(USER, TXN))

    # The MAC is sent exactly once (by confirm), not re-sent by the mac handler.
    confirms = [s for s in nio.to_device_sent if s["op"] == "confirm"]
    assert len(confirms) == 1
    assert any(
        item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done" for item in events
    )
    await client.async_stop()


@pytest.mark.asyncio
async def test_inbound_start_unknown_device_is_repaired(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    # Peer device was created after bot startup: known to the homeserver but not
    # yet in the device store (nio would have dropped the inbound start).
    nio.add_pending_device(USER, PEER_DEVICE, verified=False)

    await client.handle_to_device_event(KeyVerificationStart(USER, TXN, PEER_DEVICE))

    # The integration must query the sender's keys, re-feed the dropped start into
    # nio, and accept exactly once — no manual retry.
    assert USER in nio.olm.users_for_key_query
    assert any(
        getattr(c, "transaction_id", None) == TXN
        for c in nio.olm.handle_key_verification_calls
    )
    assert TXN in nio.key_verifications
    assert nio.key_verifications[TXN].other_olm_device.user_id == USER
    assert nio.key_verifications[TXN].other_olm_device.device_id == PEER_DEVICE
    assert [s["op"] for s in nio.to_device_sent] == ["accept"]
    assert any(
        item[0] == EVENT_VERIFICATION and item[1]["stage"] == "started"
        for item in events
    )
    await client.async_stop()


@pytest.mark.asyncio
async def test_inbound_start_unknown_device_still_missing_emits_error(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    # No pending device: even after the key query, the device stays unknown.
    await client.handle_to_device_event(KeyVerificationStart(USER, TXN, PEER_DEVICE))

    assert USER in nio.olm.users_for_key_query
    assert TXN not in nio.key_verifications
    assert any(
        item[0] == EVENT_ERROR and item[1]["code"] == ERROR_DEVICE_MISSING
        for item in events
    )
    await client.async_stop()


def test_sas_timeout_patch_ignores_event_timeout():
    """The nio workaround keeps SAS alive through a slow emoji check."""
    canceled = "canceled"

    class SasLike:
        _max_age = timedelta(minutes=5)
        _timeout_error = ("m.timeout", "timed out")

        def __init__(self):
            self.creation_time = datetime.now()
            self.state = "started"
            self.cancel_code = None
            self.cancel_reason = None
            self.sas_accepted = False

        @property
        def verified(self):
            return False

        @property
        def canceled(self):
            return self.state == "canceled"

    _apply_sas_timeout_patch(SasLike, canceled)

    sas = SasLike()
    assert sas.timed_out is False

    # 90 s old: buggy nio would time out (60 s event timeout); the patch keeps it.
    sas.creation_time = datetime.now() - timedelta(seconds=90)
    assert sas.timed_out is False

    # Past _max_age (5 min): the patch still honors the total-age timeout.
    sas.creation_time = datetime.now() - timedelta(minutes=6)
    assert sas.timed_out is True
    assert sas.state == "canceled"


def test_sas_commitment_is_unpadded_base64():
    """The commitment wire format is unpadded base64, never hexdigest."""
    # SHA-256("abc") as unpadded base64, matching pre-0.26.0 olm.sha256.
    assert _sas_commitment("abc", "") == "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0"
    assert _sas_commitment("abc", "") != (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_sas_commitment_patch_fixes_hex_encoding():
    """The patch rewrites nio 0.26.0 hexdigest commitments to unpadded base64."""
    import hashlib

    def to_canonical_json(obj):
        return obj

    class SasLike:
        @classmethod
        def from_key_verification_start(
            cls, own_user, own_device, own_fp_key, other_olm_device, event
        ):
            obj = cls()
            obj.commitment = hashlib.sha256(
                obj.pubkey.encode() + event.source["content"].encode()
            ).hexdigest()
            return obj

        def __init__(self):
            self.pubkey = "PK"
            self.commitment = None

        def start_verification(self):
            return SimpleNamespace(content="START")

        def _check_commitment(self, key):
            return (
                self.commitment
                == hashlib.sha256(
                    key.encode() + self.start_verification().content.encode()
                ).hexdigest()
            )

    _apply_sas_commitment_patch(SasLike, to_canonical_json)

    # Responder: from_key_verification_start now emits unpadded base64.
    event = SimpleNamespace(source={"content": "START"})
    sas = SasLike.from_key_verification_start("u", "d", "fp", None, event)
    assert sas.commitment == _sas_commitment("PK", "START")
    assert sas.commitment != hashlib.sha256(b"PKSTART").hexdigest()

    # Initiator: _check_commitment now verifies base64, and rejects a wrong key.
    sas.commitment = _sas_commitment("PK", "START")
    assert sas._check_commitment("PK") is True
    assert sas._check_commitment("WRONG") is False


def test_sas_emoji_patch_stops_double_conversion():
    """Patch returns vodozemac's emoji indices directly, no re-slicing."""
    indices = [5, 19, 28, 47, 14, 19, 53]

    class SasLike:
        emoji = [f"e{i}" for i in range(64)]

        def __init__(self):
            self.established_sas = SimpleNamespace(
                bytes=lambda info: SimpleNamespace(emoji_indices=indices)
            )

    _apply_sas_emoji_patch(SasLike)

    sas = SasLike()
    assert sas._generate_emoji("info") == [f"e{i}" for i in indices]

    # The old libolm bit-slicing would have produced different indices.
    wrong = [f"e{i}" for i in [1, 17, 12, 28, 11, 48, 56]]
    assert sas._generate_emoji("info") != wrong


def test_sas_mac_patch_routes_legacy_to_invalid_base64():
    """Legacy hkdf-hmac-sha256 uses the libolm invalid-base64 MAC, both ways."""
    invalid_calls = []

    class EstablishedSas:
        def calculate_mac(self, input, info):
            raise AssertionError("standard calculate_mac must not be used for legacy")

        def calculate_mac_invalid_base64(self, input, info):
            invalid_calls.append((input, info))
            return "legacy-mac"

    sas_state = SimpleNamespace(
        created=0, started=1, accepted=2, key_received=3, mac_received=4, canceled=5
    )

    class SasLike:
        _unexpected_message_error = ("m.unexpected_message", "Unexpected message")
        _key_mismatch_error = ("m.key_mismatch", "Key mismatch")
        _invalid_message_error = ("m.invalid_message", "Invalid message")

        def __init__(self):
            self.sas_accepted = True
            self.state = sas_state.key_received
            self.own_device = "BOTDEV"
            self.own_user = "@bot:example.org"
            self.own_fp_key = "BOTFP"
            self.other_olm_device = SimpleNamespace(
                user_id="@peer:example.org", id="PEERDEV", ed25519="PEERFP"
            )
            self.transaction_id = "txn"
            self.chosen_mac_method = "hkdf-hmac-sha256"
            self.verified = False
            self.verified_devices = []
            self.established_sas = EstablishedSas()
            self.cancel_code = None
            self.cancel_reason = None

        def _event_ok(self, event):
            return True

    def to_device_message(event_type, user_id, device_id, content):
        return (event_type, user_id, device_id, content)

    _apply_sas_mac_patch(SasLike, to_device_message, RuntimeError, sas_state)

    # Generation: get_mac routes through calculate_mac_invalid_base64.
    sas = SasLike()
    message = sas.get_mac()
    assert message[0] == "m.key.verification.mac"
    assert message[3]["keys"] == "legacy-mac"
    assert message[3]["mac"] == {"ed25519:BOTDEV": "legacy-mac"}
    assert invalid_calls

    # Verification: receive_mac_event routes through the same path.
    sas = SasLike()
    event = SimpleNamespace(mac={"ed25519:PEERDEV": "legacy-mac"}, keys="legacy-mac")
    sas.receive_mac_event(event)
    assert sas.state == sas_state.mac_received
    assert sas.verified_devices == ["PEERDEV"]

    # A wrong MAC is rejected with key_mismatch (exercises the compare path).
    sas = SasLike()
    event = SimpleNamespace(mac={"ed25519:PEERDEV": "wrong"}, keys="legacy-mac")
    sas.receive_mac_event(event)
    assert sas.state == sas_state.canceled
    assert sas.cancel_code == "m.key_mismatch"


@pytest.mark.asyncio
async def test_request_replies_ready_then_sas_flow(tmp_path, monkeypatch):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    monkeypatch.setattr(client_module, "_to_device_message", _fake_to_device_message)
    await client.async_start()
    nio = created["nio"]
    nio.add_device(USER, PEER_DEVICE, verified=False)

    # Element sends m.key.verification.request; the bot replies ready.
    await client._handle_verification_request(
        VerificationRequest(USER, PEER_DEVICE, TXN)
    )
    readies = _sent_readies(nio)
    assert len(readies) == 1
    assert readies[0].recipient == USER
    assert readies[0].recipient_device == PEER_DEVICE
    assert readies[0].content == {
        "from_device": "HABOTABC",
        "methods": ["m.sas.v1"],
        "transaction_id": TXN,
    }
    # The ready bridge must NOT create SAS state.
    assert TXN not in nio.key_verifications

    # Element then sends start; the existing SAS flow drives the rest.
    nio.key_verifications[TXN] = FakeSas(TXN, USER, PEER_DEVICE, we_started_it=False)
    await client.handle_to_device_event(KeyVerificationStart(USER, TXN, PEER_DEVICE))
    assert any(
        item[0] == EVENT_VERIFICATION and item[1]["stage"] == "started"
        for item in events
    )

    await client.handle_to_device_event(KeyVerificationKey(USER, TXN))
    assert any(
        item[0] == EVENT_VERIFICATION and item[1]["stage"] == "sas" for item in events
    )

    await client.async_confirm_verification(TXN)
    nio.receive_verification_mac(TXN)
    await client.handle_to_device_event(KeyVerificationMac(USER, TXN))
    assert any(
        item[0] == EVENT_VERIFICATION and item[1]["stage"] == "done" for item in events
    )
    assert nio.device_store[USER][PEER_DEVICE].verified is True
    # The wire `done` concludes the request-based flow for Element.
    dones = _sent_dones(nio)
    assert len(dones) == 1
    assert dones[0].recipient == USER
    assert dones[0].recipient_device == PEER_DEVICE
    assert dones[0].content == {"transaction_id": TXN}
    await client.async_stop()


@pytest.mark.asyncio
async def test_confirm_sends_done_after_peer_mac(tmp_path, monkeypatch):
    """Case B: the peer MAC arrives before the user confirms, so `done` is sent
    from `async_confirm_verification` (the other verified branch)."""
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    monkeypatch.setattr(client_module, "_to_device_message", _fake_to_device_message)
    await client.async_start()
    nio = created["nio"]
    nio.add_device(USER, PEER_DEVICE, verified=False)
    nio.key_verifications[TXN] = FakeSas(TXN, USER, PEER_DEVICE, we_started_it=False)

    await client.handle_to_device_event(KeyVerificationStart(USER, TXN, PEER_DEVICE))
    await client.handle_to_device_event(KeyVerificationKey(USER, TXN))

    # Peer MAC arrives before the user confirms → not verified yet, no `done`.
    nio.receive_verification_mac(TXN)
    await client.handle_to_device_event(KeyVerificationMac(USER, TXN))
    assert _sent_dones(nio) == []

    # The user confirms → the SAS becomes verified and `done` is sent.
    await client.async_confirm_verification(TXN)
    dones = _sent_dones(nio)
    assert len(dones) == 1
    assert dones[0].recipient == USER
    assert dones[0].recipient_device == PEER_DEVICE
    assert dones[0].content == {"transaction_id": TXN}
    assert nio.device_store[USER][PEER_DEVICE].verified is True
    await client.async_stop()


@pytest.mark.asyncio
async def test_request_unknown_sender_ignored(tmp_path, monkeypatch):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    monkeypatch.setattr(client_module, "_to_device_message", _fake_to_device_message)
    await client.async_start()
    nio = created["nio"]
    await client._handle_verification_request(
        VerificationRequest("@stranger:example.org", PEER_DEVICE, TXN)
    )
    assert _sent_readies(nio) == []
    await client.async_stop()


@pytest.mark.asyncio
async def test_request_unsupported_method_ignored(tmp_path, monkeypatch):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    monkeypatch.setattr(client_module, "_to_device_message", _fake_to_device_message)
    await client.async_start()
    nio = created["nio"]
    await client._handle_verification_request(
        VerificationRequest(USER, PEER_DEVICE, TXN, methods=["m.qr.scan.v1"])
    )
    assert _sent_readies(nio) == []
    await client.async_stop()


@pytest.mark.asyncio
async def test_request_missing_transaction_id_ignored(tmp_path, monkeypatch):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    monkeypatch.setattr(client_module, "_to_device_message", _fake_to_device_message)
    await client.async_start()
    nio = created["nio"]
    await client._handle_verification_request(
        VerificationRequest(USER, PEER_DEVICE, "")
    )
    assert _sent_readies(nio) == []
    await client.async_stop()


@pytest.mark.asyncio
async def test_request_missing_from_device_ignored(tmp_path, monkeypatch):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    monkeypatch.setattr(client_module, "_to_device_message", _fake_to_device_message)
    await client.async_start()
    nio = created["nio"]
    await client._handle_verification_request(VerificationRequest(USER, "", TXN))
    assert _sent_readies(nio) == []
    await client.async_stop()


@pytest.mark.asyncio
async def test_request_expired_timestamp_ignored(tmp_path, monkeypatch):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    monkeypatch.setattr(client_module, "_to_device_message", _fake_to_device_message)
    await client.async_start()
    nio = created["nio"]
    await client._handle_verification_request(
        VerificationRequest(
            USER, PEER_DEVICE, TXN, timestamp=_now_ms() - 11 * 60 * 1000
        )
    )
    assert _sent_readies(nio) == []
    await client.async_stop()


@pytest.mark.asyncio
async def test_request_future_timestamp_ignored(tmp_path, monkeypatch):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    monkeypatch.setattr(client_module, "_to_device_message", _fake_to_device_message)
    await client.async_start()
    nio = created["nio"]
    await client._handle_verification_request(
        VerificationRequest(USER, PEER_DEVICE, TXN, timestamp=_now_ms() + 6 * 60 * 1000)
    )
    assert _sent_readies(nio) == []
    await client.async_stop()


def test_request_timestamp_valid_rejects_non_int():
    from custom_components.matrix_e2ee.client import _request_timestamp_valid

    assert _request_timestamp_valid(_now_ms()) is True
    assert _request_timestamp_valid(True) is False
    assert _request_timestamp_valid(1.5) is False
    assert _request_timestamp_valid("123") is False
    assert _request_timestamp_valid(None) is False


@pytest.mark.asyncio
async def test_list_known_devices_excludes_self(tmp_path):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    await client.async_start()
    nio = created["nio"]
    # The bot's own device is registered alongside a peer device.
    nio.add_device(BOT, "HABOTABC", verified=False)
    nio.add_device(USER, PEER_DEVICE, verified=False)

    devices = client.list_known_devices()

    assert [(d["user_id"], d["device_id"]) for d in devices] == [(USER, PEER_DEVICE)]
    assert devices[0]["verified"] is False
    await client.async_stop()


@pytest.mark.asyncio
async def test_list_known_devices_empty_without_store(tmp_path):
    factory, _ = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    await client.async_start()

    assert client.list_known_devices() == []
    await client.async_stop()


@pytest.mark.asyncio
async def test_sas_snapshot_never_reads_timed_out(tmp_path):
    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory)
    await client.async_start()
    nio = created["nio"]

    class ExplodingTimedOutSas:
        """A SAS whose ``timed_out`` raises if the snapshot reads it (the patch mutates state)."""

        def __init__(self):
            self.other_olm_device = SimpleNamespace(
                user_id=USER, id=PEER_DEVICE, ed25519="PEERFP"
            )
            self.verified = False
            self.canceled = False

        @property
        def timed_out(self):
            raise AssertionError("sas_snapshot must not read timed_out")

        def get_emoji(self):
            return [("⚓", "Anchor"), ("☎️", "Telephone")]

    nio.key_verifications[TXN] = ExplodingTimedOutSas()

    snapshot = client.sas_snapshot(TXN)

    assert snapshot is not None
    assert snapshot["transaction_id"] == TXN
    assert snapshot["user_id"] == USER
    assert snapshot["device_id"] == PEER_DEVICE
    assert snapshot["verified"] is False
    assert snapshot["canceled"] is False
    assert snapshot["emojis"] == [["⚓", "Anchor"], ["☎️", "Telephone"]]
    await client.async_stop()
