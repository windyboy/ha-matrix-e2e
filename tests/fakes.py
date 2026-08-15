"""Mock nio.AsyncClient for matrix_e2ee tests. No real credentials or crypto store."""

from __future__ import annotations

from types import SimpleNamespace


class LoginError:
    """Name must end with Error so the client treats it as a failed nio response."""


class UnverifiedDeviceError(Exception):
    """Raised by fake room_send when the room has unverified devices."""


class FakeOlm:
    """Presence-only stand-in: nio encrypts only when client.olm is set."""


class FakeNio:
    def __init__(
        self,
        homeserver,
        user,
        device_id="",
        store_path="",
        pickle_key=None,
        encryption_enabled=True,
        store_sync_tokens=True,
        **kwargs,
    ):
        self.homeserver = homeserver
        self.user = user
        self.device_id = device_id or ""
        self.store_path = store_path
        self.pickle_key = pickle_key
        self.encryption_enabled = encryption_enabled
        self.store_sync_tokens = store_sync_tokens
        self.user_id = ""
        self.access_token = ""
        self.should_upload_keys = False
        self.callbacks = []
        self.sent = []
        self.login_calls = []
        self.restore_called_with = None
        self.login_should_fail = False
        self.send_error: Exception | None = None
        self.closed = False
        self.sync_calls = 0
        self.olm: FakeOlm | None = FakeOlm()
        self.rooms: dict[str, SimpleNamespace] = {}
        self.devices_trusted = False
        self.loaded_sync_token: str | None = None
        self.next_batch: str | None = None
        self.sync_forever_calls: list[dict] = []
        self.plaintext_fallback_attempted = False

    async def login(self, password, device_name=""):
        self.login_calls.append({"password": password, "device_name": device_name})
        if self.login_should_fail:
            return LoginError()
        self.user_id = self.user_id or self.user
        self.device_id = self.device_id or "HABOTABC"
        self.access_token = self.access_token or "syt_test_access_token_value"
        return object()

    def restore_login(self, user_id, device_id, access_token):
        self.restore_called_with = (user_id, device_id, access_token)
        self.user_id = user_id
        self.device_id = device_id
        self.access_token = access_token

    async def whoami(self):
        return object()

    async def room_send(
        self, room_id, message_type, content, ignore_unverified_devices=False
    ):
        if self.send_error is not None:
            raise self.send_error
        room = self.rooms.get(room_id)
        encrypted_room = bool(room and getattr(room, "encrypted", False))
        if encrypted_room:
            if self.olm is None:
                self.plaintext_fallback_attempted = True
                raise RuntimeError("refusing plaintext fallback into an encrypted room")
            if not self.devices_trusted:
                raise UnverifiedDeviceError("room contains unverified devices")
        self.sent.append(
            {
                "room_id": room_id,
                "message_type": message_type,
                "content": content,
                "ignore_unverified_devices": ignore_unverified_devices,
                "encrypted": encrypted_room,
            }
        )
        return object()

    def add_event_callback(self, callback, event_type):
        self.callbacks.append((callback, event_type))

    async def sync(self, timeout=0, full_state=None):
        self.sync_calls += 1
        self.sync_callback_count = len(self.callbacks)
        if not self.next_batch:
            self.next_batch = "s_after_catchup"
        return object()

    async def sync_forever(self, timeout=0, since=None, **kwargs):
        self.sync_forever_calls.append(
            {
                "timeout": timeout,
                "since": since,
                "callback_count": len(self.callbacks),
            }
        )
        return None

    async def close(self):
        self.closed = True
