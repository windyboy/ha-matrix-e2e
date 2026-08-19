"""Mock nio.AsyncClient tests for the locked M1 contract. No real credentials."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.matrix_e2ee.client import (
    MatrixE2EEClient,
    MatrixE2EEError,
    parse_command,
    room_allowed,
    user_allowed,
)
from custom_components.matrix_e2ee.const import (
    ERROR_LOGIN_FAILED,
    ERROR_ROOM_NOT_ALLOWED,
    ERROR_SESSION_CORRUPT,
    ERROR_STORE_MISSING,
    ERROR_UNVERIFIED_DEVICE,
    EVENT_COMMAND,
    EVENT_ERROR,
)
from custom_components.matrix_e2ee.storage import (
    MatrixSession,
    atomic_save_session,
    load_session,
    session_path,
    store_path,
)
from tests.fakes import FakeNio, UnverifiedDeviceError

ROOM = "!roomid:example.org"
USER = "@admin:example.org"
BOT = "@ha-bot:example.org"
DEVICE = "HABOTABC"
TOKEN = "syt_test_access_token_value"
PICKLE = "test-pickle-key-value"
SECRET_BODY = "super-secret-message-body"


def _factory_holder():
    created: dict[str, FakeNio] = {}

    def factory(*args, **kwargs):
        nio = FakeNio(*args, **kwargs)
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


def test_room_user_prefix_allowlist():
    assert room_allowed(ROOM, [ROOM])
    assert not room_allowed(ROOM, [])
    assert not room_allowed("!other:example.org", [ROOM])
    assert user_allowed(USER, [USER])
    assert not user_allowed(USER, [])
    assert not user_allowed("@other:example.org", [USER])
    assert parse_command("!ping", "!") == ("ping", [])
    assert parse_command("!light bath on", "!") == ("light", ["bath", "on"])
    assert parse_command("hello", "!") is None
    assert parse_command("!", "!") is None


@pytest.mark.asyncio
async def test_first_login_writes_session_only_after_success(tmp_path, caplog):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    created_nio_fail = FakeNio("hs", BOT)
    created_nio_fail.login_should_fail = True

    def fail_factory(*args, **kwargs):
        return created_nio_fail

    client._nio_client_factory = fail_factory
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_start()
    assert err.value.code == ERROR_LOGIN_FAILED
    assert not session_path(tmp_path).exists()

    factory, created = _factory_holder()
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    session = load_session(tmp_path)
    assert session is not None
    assert session.user_id == BOT
    assert session.device_id == DEVICE
    assert session.access_token == TOKEN
    assert session.pickle_key
    assert store_path(tmp_path).is_dir()
    text = "\n".join(r.message for r in caplog.records)
    assert TOKEN not in text
    assert session.pickle_key not in text
    await client.async_stop()


@pytest.mark.asyncio
async def test_restart_restores_same_device_id(tmp_path):
    factory, created = _factory_holder()
    events = []
    first = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await first.async_start()
    device_id = first.session.device_id
    pickle_key = first.session.pickle_key
    await first.async_stop()

    factory2, created2 = _factory_holder()
    second = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory2, password=None
    )
    await second.async_start()
    nio = created2["nio"]
    assert nio.restore_called_with == (BOT, device_id, TOKEN)
    assert nio.login_calls == []
    assert second.session.device_id == device_id
    assert second.session.pickle_key == pickle_key
    await second.async_stop()


@pytest.mark.asyncio
async def test_missing_and_corrupt_session_fail_safely(tmp_path):
    events = []
    factory, _ = _factory_holder()
    missing = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory, password=None
    )
    with pytest.raises(MatrixE2EEError):
        await missing.async_start()
    assert not session_path(tmp_path).exists()

    session_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    session_path(tmp_path).write_text("{not json", encoding="utf-8")
    factory, _ = _factory_holder()
    corrupt = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    with pytest.raises(MatrixE2EEError) as err:
        await corrupt.async_start()
    assert err.value.code == ERROR_SESSION_CORRUPT

    atomic_save_session(
        tmp_path,
        MatrixSession(
            version=1,
            user_id=BOT,
            device_id=DEVICE,
            access_token=TOKEN,
            pickle_key=PICKLE,
        ),
    )
    # Session JSON exists but crypto store directory does not.
    store = store_path(tmp_path)
    if store.exists():
        store.rmdir()
    factory, _ = _factory_holder()
    no_store = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    with pytest.raises(MatrixE2EEError) as err:
        await no_store.async_start()
    assert err.value.code == ERROR_STORE_MISSING


@pytest.mark.asyncio
async def test_send_rejects_non_allowlisted_room_and_does_not_plaintext_fallback(
    tmp_path, caplog
):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    nio = created["nio"]

    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message("!other:example.org", SECRET_BODY)
    assert err.value.code == ERROR_ROOM_NOT_ALLOWED
    assert nio.sent == []
    assert events[-1][0] == EVENT_ERROR
    assert events[-1][1]["code"] == ERROR_ROOM_NOT_ALLOWED
    assert SECRET_BODY not in str(events)

    nio.send_error = UnverifiedDeviceError("room contains unverified devices")
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, SECRET_BODY)
    assert err.value.code == ERROR_UNVERIFIED_DEVICE
    assert nio.sent == []
    assert all(item.get("ignore_unverified_devices") is not True for item in nio.sent)

    nio.send_error = None
    await client.async_send_message(ROOM, SECRET_BODY)
    assert nio.sent[-1]["ignore_unverified_devices"] is False
    assert nio.sent[-1]["content"]["body"] == SECRET_BODY

    text = "\n".join(r.message for r in caplog.records)
    assert SECRET_BODY not in text
    assert TOKEN not in text
    await client.async_stop()


@pytest.mark.asyncio
async def test_command_event_allowlist_and_no_raw_body(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    room = SimpleNamespace(room_id=ROOM)
    event = SimpleNamespace(
        sender=USER,
        body="!ping secret-arg",
        decrypted=False,
        verified=False,
    )
    client.handle_incoming_event(room, event)
    assert events == []  # historical / pre-callback

    await client.async_sync_loop()
    client.handle_incoming_event(room, event)
    command_events = [item for item in events if item[0] == EVENT_COMMAND]
    assert len(command_events) == 1
    payload = command_events[0][1]
    assert payload == {
        "room_id": ROOM,
        "sender": USER,
        "command": "ping",
        "args": ["secret-arg"],
    }
    assert "body" not in payload
    assert SECRET_BODY not in str(payload)
    assert "!ping" not in str(payload)

    client.handle_incoming_event(SimpleNamespace(room_id="!other:example.org"), event)
    client.handle_incoming_event(
        room,
        SimpleNamespace(
            sender="@other:example.org", body="!ping", decrypted=False, verified=False
        ),
    )
    client.handle_incoming_event(
        room,
        SimpleNamespace(sender=USER, body="hello", decrypted=False, verified=False),
    )
    assert len([item for item in events if item[0] == EVENT_COMMAND]) == 1
    await client.async_stop()


@pytest.mark.asyncio
async def test_unverified_encrypted_command_does_not_fire(tmp_path):
    factory, _ = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    await client.async_sync_loop()
    client.handle_incoming_event(
        SimpleNamespace(room_id=ROOM),
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
    await client.async_stop()


@pytest.mark.asyncio
async def test_initial_sync_does_not_replay_historical_commands(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    assert client._first_setup is True
    assert client._commands_enabled is False
    historical = SimpleNamespace(
        sender=USER, body="!lights_off", decrypted=False, verified=False
    )
    client.handle_incoming_event(SimpleNamespace(room_id=ROOM), historical)
    assert events == []
    await client.async_sync_loop()
    assert created["nio"].sync_calls == 1
    assert client._commands_enabled is True
    await client.async_stop()


@pytest.mark.asyncio
async def test_empty_allowlist_blocks_send_and_commands(tmp_path):
    factory, created = _factory_holder()
    events = []
    client = _client(
        tmp_path,
        lambda t, d: events.append((t, d)),
        factory,
        rooms=[],
        users=[],
    )
    await client.async_start()
    await client.async_sync_loop()
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, "hi")
    assert err.value.code == ERROR_ROOM_NOT_ALLOWED
    assert created["nio"].sent == []
    client.handle_incoming_event(
        SimpleNamespace(room_id=ROOM),
        SimpleNamespace(sender=USER, body="!ping", decrypted=False, verified=False),
    )
    assert all(item[0] != EVENT_COMMAND for item in events)
    await client.async_stop()
