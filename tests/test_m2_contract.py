"""Mock nio.AsyncClient tests for the locked M2 contract. No real credentials."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.matrix_e2ee import client as client_mod
from custom_components.matrix_e2ee.client import MatrixE2EEClient, MatrixE2EEError
from custom_components.matrix_e2ee.const import (
    DEVICE_NAME,
    ERROR_ENCRYPTION_UNAVAILABLE,
    ERROR_STORE_MISSING,
    ERROR_UNVERIFIED_DEVICE,
    EVENT_COMMAND,
    EVENT_ERROR,
    NIO_DEFAULT_PICKLE_KEY,
)
from custom_components.matrix_e2ee.storage import (
    load_session,
    store_path,
)
from tests.fakes import FakeNio

ROOM = "!roomid:example.org"
USER = "@admin:example.org"
BOT = "@ha-bot:example.org"
SECRET_BODY = "super-secret-message-body"
ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "matrix_e2ee"


def _factory_holder():
    created: dict[str, FakeNio] = {}

    def factory(*args, **kwargs):
        nio = FakeNio(*args, **kwargs)
        nio.user_id = BOT
        created["nio"] = nio
        return nio

    return factory, created


def _client(tmp_path: Path, fire, factory, password="pw", rooms=None, users=None):
    return MatrixE2EEClient(
        config_dir=tmp_path,
        homeserver="https://matrix.example.org",
        username=BOT,
        password=password,
        allowed_rooms=rooms if rooms is not None else [ROOM],
        allowed_users=users if users is not None else [USER],
        verification_peer_users=[],
        command_prefix="!",
        fire_event=fire,
        nio_client_factory=factory,
    )


def _mark_encrypted(nio: FakeNio, *, trusted: bool) -> None:
    nio.rooms[ROOM] = SimpleNamespace(room_id=ROOM, encrypted=True)
    nio.devices_trusted = trusted


def _src(*relative: str) -> str:
    return (PKG.joinpath(*relative)).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_first_login_is_full_e2ee_device_not_default_pickle(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    session = load_session(tmp_path)
    assert session is not None
    assert nio.encryption_enabled is True
    assert nio.store_sync_tokens is True
    assert nio.pickle_key == session.pickle_key
    assert session.pickle_key != NIO_DEFAULT_PICKLE_KEY
    assert nio.pickle_key != NIO_DEFAULT_PICKLE_KEY
    assert nio.store_path == str(store_path(tmp_path))
    assert nio.login_calls == [{"password": "pw", "device_name": DEVICE_NAME}]
    await client.async_stop()


@pytest.mark.asyncio
async def test_restore_reuses_same_device_and_store_path(tmp_path):
    factory, created = _factory_holder()
    events = []
    first = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await first.async_start()
    device_id = first.session.device_id
    pickle_key = first.session.pickle_key
    store = created["nio"].store_path
    await first.async_stop()

    factory2, created2 = _factory_holder()
    second = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory2, password=None
    )
    await second.async_start()
    nio = created2["nio"]
    assert nio.login_calls == []
    assert nio.restore_called_with[0] == BOT
    assert nio.restore_called_with[1] == device_id
    assert second.session.device_id == device_id
    assert second.session.pickle_key == pickle_key
    assert nio.store_path == store
    assert nio.encryption_enabled is True
    assert nio.store_sync_tokens is True
    await second.async_stop()


@pytest.mark.asyncio
async def test_store_loss_is_new_device_not_history_recovery(tmp_path):
    factory, _ = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    await client.async_stop()

    store = store_path(tmp_path)
    for child in store.iterdir():
        child.unlink()
    store.rmdir()

    factory, _ = _factory_holder()
    broken = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    with pytest.raises(MatrixE2EEError) as err:
        await broken.async_start()
    assert err.value.code == ERROR_STORE_MISSING
    message = str(err.value).lower()
    assert "new device" in message
    assert "cannot be decrypted" in message
    assert "recover" not in message


def test_no_admin_token_or_plaintext_message_event():
    client_src = _src("client.py")
    init_src = _src("__init__.py")
    const_src = _src("const.py")
    for blob in (client_src, init_src):
        assert "login_with_token" not in blob
        assert "login_raw" not in blob
        assert "admin_token" not in blob
        assert "ignore_unverified_devices=True" not in blob
        assert "matrix_e2ee_message" not in blob
    assert "EVENT_MESSAGE" not in const_src


@pytest.mark.asyncio
async def test_encrypted_send_succeeds_only_when_store_trusts_devices(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    _mark_encrypted(nio, trusted=True)
    await client.async_send_message(ROOM, SECRET_BODY)
    assert nio.plaintext_fallback_attempted is False
    assert nio.sent[-1]["encrypted"] is True
    assert nio.sent[-1]["ignore_unverified_devices"] is False
    assert nio.sent[-1]["message_type"] == "m.room.message"
    await client.async_stop()


@pytest.mark.asyncio
async def test_encrypted_send_unverified_fails_closed_no_retry(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    _mark_encrypted(nio, trusted=False)
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, SECRET_BODY)
    assert err.value.code == ERROR_UNVERIFIED_DEVICE
    assert nio.sent == []
    assert nio.plaintext_fallback_attempted is False
    assert events[-1][0] == EVENT_ERROR
    assert events[-1][1]["code"] == ERROR_UNVERIFIED_DEVICE
    assert SECRET_BODY not in str(events)
    await client.async_stop()


@pytest.mark.asyncio
async def test_encrypted_room_without_olm_never_sends_plaintext(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    _mark_encrypted(nio, trusted=True)
    nio.olm = None
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, SECRET_BODY)
    assert err.value.code == ERROR_ENCRYPTION_UNAVAILABLE
    assert nio.sent == []
    assert nio.plaintext_fallback_attempted is False
    await client.async_stop()


@pytest.mark.asyncio
async def test_non_command_encrypted_text_does_not_fire_message_event(tmp_path):
    factory, _ = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    await client.async_sync_loop()
    client.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=True),
        SimpleNamespace(
            sender=USER,
            body="hello there",
            decrypted=True,
            verified=True,
            type="m.room.message",
        ),
    )
    assert events == []
    assert all(item[0] != "matrix_e2ee_message" for item in events)
    await client.async_stop()


@pytest.mark.asyncio
async def test_first_sync_does_not_replay_then_restore_uses_sync_token(tmp_path):
    factory, created = _factory_holder()
    events = []
    first = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await first.async_start()
    historical = SimpleNamespace(
        sender=USER,
        body="!lights_off",
        decrypted=True,
        verified=True,
    )
    first.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=True), historical
    )
    assert events == []
    await first.async_sync_loop()
    nio = created["nio"]
    assert nio.sync_calls == 1
    assert getattr(nio, "sync_callback_count", None) == 0
    assert nio.sync_forever_calls[-1]["callback_count"] >= 1
    await first.async_stop()

    factory2, created2 = _factory_holder()
    second = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory2, password=None
    )
    await second.async_start()
    nio2 = created2["nio"]
    nio2.loaded_sync_token = "s_persisted_token"
    await second.async_sync_loop()
    assert nio2.sync_calls == 0
    assert nio2.sync_forever_calls[-1]["since"] == "s_persisted_token"
    assert nio2.sync_forever_calls[-1]["callback_count"] >= 1
    second.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=True),
        SimpleNamespace(
            sender=USER,
            body="!ping live",
            decrypted=True,
            verified=True,
        ),
    )
    commands = [item for item in events if item[0] == EVENT_COMMAND]
    assert commands[-1][1]["command"] == "ping"
    await second.async_stop()


@pytest.mark.asyncio
async def test_restore_without_sync_token_catchup_does_not_replay(tmp_path):
    factory, _ = _factory_holder()
    events = []
    first = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await first.async_start()
    await first.async_stop()

    factory2, created2 = _factory_holder()
    second = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory2, password=None
    )
    await second.async_start()
    nio2 = created2["nio"]
    historical = SimpleNamespace(
        sender=USER, body="!old", decrypted=True, verified=True
    )
    second.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=True), historical
    )
    assert all(item[0] != EVENT_COMMAND for item in events)
    await second.async_sync_loop()
    assert nio2.sync_calls == 1
    assert getattr(nio2, "sync_callback_count", None) == 0
    await second.async_stop()


@pytest.mark.asyncio
async def test_encrypted_command_requires_verified_and_allowlist(tmp_path):
    factory, _ = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    await client.async_sync_loop()
    room = SimpleNamespace(room_id=ROOM, encrypted=True)

    client.handle_incoming_event(
        room,
        SimpleNamespace(
            sender=USER,
            body="!ping",
            decrypted=True,
            verified=False,
        ),
    )
    assert all(item[0] != EVENT_COMMAND for item in events)
    assert any(
        item[0] == EVENT_ERROR and item[1]["code"] == ERROR_UNVERIFIED_DEVICE
        for item in events
    )

    events.clear()
    client.handle_incoming_event(
        room,
        SimpleNamespace(
            sender="@other:example.org",
            body="!ping",
            decrypted=True,
            verified=True,
        ),
    )
    assert all(item[0] != EVENT_COMMAND for item in events)

    client.handle_incoming_event(
        SimpleNamespace(room_id="!other:example.org", encrypted=True),
        SimpleNamespace(
            sender=USER,
            body="!ping",
            decrypted=True,
            verified=True,
        ),
    )
    assert all(item[0] != EVENT_COMMAND for item in events)

    client.handle_incoming_event(
        room,
        SimpleNamespace(
            sender=USER,
            body="!ping secret-arg",
            decrypted=True,
            verified=True,
        ),
    )
    commands = [item for item in events if item[0] == EVENT_COMMAND]
    assert len(commands) == 1
    assert commands[0][1] == {
        "room_id": ROOM,
        "sender": USER,
        "command": "ping",
        "args": ["secret-arg"],
    }
    assert "body" not in commands[0][1]
    assert "secret-arg" in commands[0][1]["args"]
    await client.async_stop()


@pytest.mark.asyncio
async def test_encrypted_room_plaintext_looking_event_still_requires_verified(
    tmp_path,
):
    factory, _ = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    await client.async_sync_loop()
    client.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=True),
        SimpleNamespace(
            sender=USER,
            body="!ping",
            decrypted=False,
            verified=False,
        ),
    )
    assert all(item[0] != EVENT_COMMAND for item in events)
    assert any(
        item[0] == EVENT_ERROR and item[1]["code"] == ERROR_UNVERIFIED_DEVICE
        for item in events
    )
    await client.async_stop()


@pytest.mark.asyncio
async def test_undecrypted_megolm_event_does_not_fire_command(tmp_path):
    class MegolmEvent:
        def __init__(self):
            self.sender = USER
            self.type = "m.room.encrypted"
            self.decrypted = False
            self.verified = False

    factory, _ = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    await client.async_sync_loop()
    client.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=True),
        MegolmEvent(),
    )
    assert all(item[0] != EVENT_COMMAND for item in events)
    await client.async_stop()


@pytest.mark.asyncio
async def test_logger_redacts_token_pickle_and_never_logs_body(tmp_path, caplog):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]
    token = client.session.access_token
    pickle_key = client.session.pickle_key
    _mark_encrypted(nio, trusted=False)
    with pytest.raises(MatrixE2EEError):
        await client.async_send_message(ROOM, SECRET_BODY)

    client_mod._LOGGER.error(
        "probe token=%s pickle=%s body=%s", token, pickle_key, SECRET_BODY
    )
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert token not in text
    assert pickle_key not in text
    # Body is not a session secret; production code must still never log it.
    # The probe line above is the test injecting it — production send must not.
    production = "\n".join(
        record.getMessage()
        for record in caplog.records
        if "probe " not in record.getMessage()
    )
    assert SECRET_BODY not in production
    assert "[redacted]" in text
    await client.async_stop()
