"""Mock nio tests for the locked M4 contract. No real credentials."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.matrix_e2ee.client import MatrixE2EEClient, MatrixE2EEError
from custom_components.matrix_e2ee.const import (
    DEVICE_NAME,
    ERROR_DEVICE_MISMATCH,
    ERROR_HARD_LOGOUT,
    ERROR_INVALID_STATE,
    ERROR_REFRESH_TOKEN_UNSUPPORTED,
    ERROR_SOFT_LOGOUT,
    ERROR_STORE_MISSING,
    EVENT_COMMAND,
    EVENT_ERROR,
)
from custom_components.matrix_e2ee.storage import (
    MatrixSession,
    load_session,
    session_path,
    store_path,
)
from tests.fakes import FakeNio

ROOM = "!roomid:example.org"
USER = "@admin:example.org"
BOT = "@ha-bot:example.org"
TOKEN = "syt_test_access_token_value"
REAUTH_PASSWORD = "reauth-bootstrap-password-value"
SECRET_BODY = "super-secret-message-body"


def _factory_holder(configure=None):
    created: dict[str, FakeNio] = {}

    def factory(*args, **kwargs):
        nio = FakeNio(*args, **kwargs)
        nio.user_id = BOT
        if configure is not None:
            configure(nio)
        created["nio"] = nio
        return nio

    return factory, created


def _client(tmp_path: Path, fire, factory, password="pw"):
    return MatrixE2EEClient(
        config_dir=tmp_path,
        homeserver="https://matrix.example.org",
        username=BOT,
        password=password,
        allowed_rooms=[ROOM],
        allowed_users=[USER],
        verification_peer_users=[],
        command_prefix="!",
        fire_event=fire,
        nio_client_factory=factory,
    )


async def _seed_session(tmp_path: Path):
    factory, created = _factory_holder()
    events = []
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    await client.async_start()
    session = client.session
    store = created["nio"].store_path
    await client.async_stop()
    return session, store


@pytest.mark.asyncio
async def test_soft_logout_keeps_store_and_same_device(tmp_path):
    session, store = await _seed_session(tmp_path)
    events = []
    factory, created = _factory_holder(
        lambda nio: setattr(nio, "whoami_soft_logout", True)
    )
    client = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory, password=None
    )
    await client.async_start()
    nio = created["nio"]
    loaded = load_session(tmp_path)
    assert loaded is not None
    assert loaded.device_id == session.device_id
    assert loaded.pickle_key == session.pickle_key
    assert loaded.access_token == session.access_token
    assert nio.store_path == store
    assert nio.closed is False
    assert client.session.device_id == session.device_id
    assert (ERROR_SOFT_LOGOUT,) == tuple(
        data["code"] for event, data in events if event == EVENT_ERROR
    )
    diag = client.safe_diagnostics()
    assert diag["soft_logged_out"] is True
    assert diag["device_id"] == session.device_id
    assert diag["store_present"] is True
    dumped = json.dumps(diag)
    assert session.access_token not in dumped
    assert session.pickle_key not in dumped
    await client.async_stop()


@pytest.mark.asyncio
async def test_reauthenticate_replaces_token_only(tmp_path, caplog):
    session, store = await _seed_session(tmp_path)
    events = []
    factory, created = _factory_holder(
        lambda nio: setattr(nio, "whoami_soft_logout", True)
    )
    client = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory, password=None
    )
    await client.async_start()
    nio = created["nio"]
    nio.whoami_soft_logout = False
    await client.async_reauthenticate(REAUTH_PASSWORD)
    loaded = load_session(tmp_path)
    assert loaded is not None
    assert loaded.device_id == session.device_id
    assert loaded.pickle_key == session.pickle_key
    assert loaded.access_token != session.access_token
    assert loaded.access_token == nio.access_token
    assert nio.store_path == store
    assert nio.login_calls == [
        {"password": REAUTH_PASSWORD, "device_name": DEVICE_NAME}
    ]
    assert client.safe_diagnostics()["soft_logged_out"] is False
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert REAUTH_PASSWORD not in text
    assert loaded.access_token not in text
    assert session.pickle_key not in text
    for _event, data in events:
        blob = json.dumps(data)
        assert REAUTH_PASSWORD not in blob
        assert loaded.access_token not in blob
        assert session.pickle_key not in blob
    await client.async_stop()


@pytest.mark.asyncio
async def test_reauthenticate_device_mismatch_does_not_write_token(tmp_path):
    session, _store = await _seed_session(tmp_path)
    events = []
    factory, created = _factory_holder(
        lambda nio: setattr(nio, "whoami_soft_logout", True)
    )
    client = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory, password=None
    )
    await client.async_start()
    nio = created["nio"]
    nio.whoami_soft_logout = False
    nio.login_device_id = "OTHERDEVICE"
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_reauthenticate(REAUTH_PASSWORD)
    assert err.value.code == ERROR_DEVICE_MISMATCH
    loaded = load_session(tmp_path)
    assert loaded is not None
    assert loaded.access_token == session.access_token
    assert loaded.device_id == session.device_id
    assert nio.device_id == session.device_id
    assert nio.access_token == session.access_token
    assert client.safe_diagnostics()["soft_logged_out"] is True
    await client.async_stop()


@pytest.mark.asyncio
async def test_hard_logout_fails_closed_and_does_not_reuse_store(tmp_path):
    session, store = await _seed_session(tmp_path)
    events = []
    factory, created = _factory_holder(
        lambda nio: setattr(nio, "whoami_hard_logout", True)
    )
    client = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory, password=None
    )
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_start()
    assert err.value.code == ERROR_HARD_LOGOUT
    assert created["nio"].closed is True
    assert session_path(tmp_path).exists()
    assert Path(store).is_dir()
    loaded = load_session(tmp_path)
    assert loaded is not None
    assert loaded.device_id == session.device_id
    assert client.session is None


@pytest.mark.asyncio
async def test_refresh_token_first_login_does_not_write_session(tmp_path):
    events = []
    factory, created = _factory_holder(
        lambda nio: setattr(nio, "login_refresh_token", True)
    )
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_start()
    assert err.value.code == ERROR_REFRESH_TOKEN_UNSUPPORTED
    assert not session_path(tmp_path).exists()
    assert created["nio"].closed is True


@pytest.mark.asyncio
async def test_short_lived_token_first_login_does_not_write_session(tmp_path):
    events = []
    factory, created = _factory_holder(
        lambda nio: setattr(nio, "login_expires_in_ms", 60_000)
    )
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_start()
    assert err.value.code == ERROR_REFRESH_TOKEN_UNSUPPORTED
    assert not session_path(tmp_path).exists()
    assert created["nio"].closed is True


@pytest.mark.asyncio
async def test_reauthenticate_rejects_refresh_token_without_replacing(tmp_path):
    session, _store = await _seed_session(tmp_path)
    events = []
    factory, created = _factory_holder(
        lambda nio: setattr(nio, "whoami_soft_logout", True)
    )
    client = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory, password=None
    )
    await client.async_start()
    nio = created["nio"]
    nio.whoami_soft_logout = False
    nio.login_refresh_token = True
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_reauthenticate(REAUTH_PASSWORD)
    assert err.value.code == ERROR_REFRESH_TOKEN_UNSUPPORTED
    loaded = load_session(tmp_path)
    assert loaded is not None
    assert loaded.access_token == session.access_token
    assert nio.access_token == session.access_token
    assert client.safe_diagnostics()["soft_logged_out"] is True
    await client.async_stop()


@pytest.mark.asyncio
async def test_store_loss_still_demands_new_device(tmp_path):
    await _seed_session(tmp_path)
    store = store_path(tmp_path)
    if store.exists():
        store.rmdir()
    events = []
    factory, _ = _factory_holder()
    client = _client(tmp_path, lambda t, d: events.append((t, d)), factory)
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_start()
    assert err.value.code == ERROR_STORE_MISSING
    assert session_path(tmp_path).exists()


@pytest.mark.asyncio
async def test_soft_logout_blocks_send_commands_and_sync(tmp_path):
    await _seed_session(tmp_path)
    events = []
    factory, created = _factory_holder(
        lambda nio: setattr(nio, "whoami_soft_logout", True)
    )
    client = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory, password=None
    )
    await client.async_start()
    nio = created["nio"]
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_send_message(ROOM, SECRET_BODY)
    assert err.value.code == ERROR_SOFT_LOGOUT
    assert nio.sent == []
    client.enable_command_callbacks()
    client.handle_incoming_event(
        SimpleNamespace(room_id=ROOM, encrypted=False),
        SimpleNamespace(
            sender=USER,
            body="!ping",
            verified=True,
            decrypted=False,
            type="m.room.message",
        ),
    )
    assert all(item[0] != EVENT_COMMAND for item in events)
    await client.async_sync_loop()
    assert nio.sync_calls == 0
    assert nio.sync_forever_calls == []
    await client.async_stop()


@pytest.mark.asyncio
async def test_reauthenticate_rejected_when_not_soft_logged_out(tmp_path):
    await _seed_session(tmp_path)
    events = []
    factory, _ = _factory_holder()
    client = _client(
        tmp_path, lambda t, d: events.append((t, d)), factory, password=None
    )
    await client.async_start()
    with pytest.raises(MatrixE2EEError) as err:
        await client.async_reauthenticate(REAUTH_PASSWORD)
    assert err.value.code == ERROR_INVALID_STATE
    await client.async_stop()


@pytest.mark.asyncio
async def test_async_stop_handles_cancelled_error(tmp_path):
    await _seed_session(tmp_path)
    factory, _ = _factory_holder()
    client = _client(tmp_path, lambda t, d: None, factory, password=None)
    await client.async_start()
    client._sync_task = asyncio.create_task(asyncio.sleep(3600))
    await client.async_stop()
    assert client.nio is None


def test_session_with_access_token_keeps_device_and_pickle():
    session = MatrixSession(
        version=1,
        user_id=BOT,
        device_id="HABOTABC",
        access_token=TOKEN,
        pickle_key="test-pickle-key-value",
    )
    replaced = session.with_access_token("syt_new_access_token_value")
    assert replaced.device_id == session.device_id
    assert replaced.pickle_key == session.pickle_key
    assert replaced.user_id == session.user_id
    assert replaced.access_token == "syt_new_access_token_value"
    assert session.access_token == TOKEN
