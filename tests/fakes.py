"""Mock nio.AsyncClient for matrix_e2ee tests. No real credentials or crypto store."""

from __future__ import annotations

from types import SimpleNamespace


class LoginError:
    """Name must end with Error so the client treats it as a failed nio response."""


class WhoamiError:
    """Name must end with Error so the client treats it as a failed nio response."""

    def __init__(self, *, soft_logout=False, status_code=401, errcode="M_UNKNOWN_TOKEN"):
        self.soft_logout = soft_logout
        self.status_code = status_code
        self.errcode = errcode
        self.message = "Invalid access token"

    def __str__(self):
        return self.message


class UnverifiedDeviceError(Exception):
    """Raised by fake room_send when the room has unverified devices."""


class FakeOlmDevice:
    def __init__(self, user_id, device_id, verified=False, ed25519="ED25519_DEVICE_KEY"):
        self.user_id = user_id
        self.device_id = device_id
        self.verified = verified
        self.ed25519 = ed25519


class FakeSas:
    def __init__(self, transaction_id, user_id, device_id, we_started_it=False):
        self.transaction_id = transaction_id
        self.other_olm_device = FakeOlmDevice(user_id, device_id)
        self.we_started_it = we_started_it
        self.sas_accepted = False
        self.mac_received = False
        self.canceled = False
        self.timed_out = False
        self.emojis = [("⚓", "Anchor"), ("☎️", "Telephone")]

    @property
    def verified(self):
        return self.sas_accepted and self.mac_received

    @property
    def verified_devices(self):
        return [self.other_olm_device.device_id] if self.verified else []

    def get_emoji(self):
        return list(self.emojis)

    def share_key(self):
        return SimpleNamespace(type="m.key.verification.key", transaction_id=self.transaction_id)

    def get_mac(self):
        if not self.sas_accepted:
            raise LocalProtocolError("SAS string wasn't yet accepted")
        return SimpleNamespace(type="m.key.verification.mac", transaction_id=self.transaction_id)

    def accept_sas(self):
        self.sas_accepted = True

    def receive_mac(self):
        self.mac_received = True


class LocalProtocolError(Exception):
    """Name matches nio.LocalProtocolError for error-code mapping."""


class FakeOlm:
    """Presence-only stand-in: nio encrypts only when client.olm is set."""

    def __init__(self):
        self.account = SimpleNamespace(
            identity_keys={
                "ed25519": "ED25519_PUB_KEY",
                "curve25519": "CURVE25519_PUB_KEY",
            }
        )
        self.users_for_key_query: set[str] = set()

    def verify_device(self, device):
        device.verified = True
        return True


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
        self.whoami_soft_logout = False
        self.whoami_hard_logout = False
        self.login_refresh_token = False
        self.login_expires_in_ms = None
        self.login_device_id = None
        self.refresh_token = None
        self.issued_access_tokens: list[str] = []
        self.send_error: Exception | None = None
        self.closed = False
        self.sync_calls = 0
        self.olm: FakeOlm | None = FakeOlm()
        self.keys_query_calls: list[str] = []
        self.rooms: dict[str, SimpleNamespace] = {}
        self.devices_trusted = False
        self.loaded_sync_token: str | None = None
        self.next_batch: str | None = None
        self.sync_forever_calls: list[dict] = []
        self.plaintext_fallback_attempted = False
        self.device_store: dict[str, dict[str, FakeOlmDevice]] = {}
        self.key_verifications: dict[str, FakeSas] = {}
        self.to_device_callbacks = []
        self.to_device_sent = []
        self.verified_devices: set[tuple[str, str]] = set()

    async def login(self, password, device_name=""):
        self.login_calls.append({"password": password, "device_name": device_name})
        if self.login_should_fail:
            return LoginError()
        self.user_id = self.user_id or self.user
        if self.login_device_id:
            self.device_id = self.login_device_id
        else:
            self.device_id = self.device_id or "HABOTABC"
        if self.access_token:
            token = f"syt_rotated_access_token_{len(self.issued_access_tokens) + 1}"
        else:
            token = "syt_test_access_token_value"
        self.access_token = token
        self.issued_access_tokens.append(token)
        if self.login_refresh_token:
            self.refresh_token = "syt_refresh_token_value"
            return SimpleNamespace(
                access_token=token,
                refresh_token=self.refresh_token,
                expires_in_ms=60_000,
            )
        if self.login_expires_in_ms is not None:
            return SimpleNamespace(
                access_token=token,
                refresh_token=None,
                expires_in_ms=self.login_expires_in_ms,
            )
        return object()

    def restore_login(self, user_id, device_id, access_token):
        self.restore_called_with = (user_id, device_id, access_token)
        self.user_id = user_id
        self.device_id = device_id
        self.access_token = access_token
        self.refresh_token = None

    async def whoami(self):
        if self.whoami_soft_logout:
            return WhoamiError(soft_logout=True)
        if self.whoami_hard_logout:
            return WhoamiError(soft_logout=False)
        return object()

    async def keys_query(self):
        self.keys_query_calls.append(self.user_id)
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
            trusted = self.devices_trusted or self._all_known_devices_verified()
            if not trusted:
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

    def add_to_device_callback(self, callback, event_type):
        self.to_device_callbacks.append((callback, event_type))

    def _all_known_devices_verified(self) -> bool:
        devices = [
            device
            for user_devices in self.device_store.values()
            for device in user_devices.values()
        ]
        return bool(devices) and all(device.verified for device in devices)

    def add_device(self, user_id, device_id, verified=False):
        device = FakeOlmDevice(user_id, device_id, verified=verified)
        self.device_store.setdefault(user_id, {})[device_id] = device
        return device

    async def start_key_verification(self, device, tx_id=None):
        txn = tx_id or f"txn-{device.device_id}"
        sas = FakeSas(txn, device.user_id, device.device_id, we_started_it=True)
        self.key_verifications[txn] = sas
        self.to_device_sent.append({"op": "start", "transaction_id": txn})
        return object()

    async def accept_key_verification(self, transaction_id, tx_id=None):
        sas = self.key_verifications.get(transaction_id)
        if sas is None:
            raise LocalProtocolError(
                f"Key verification with the transaction id {transaction_id} does not exist."
            )
        self.to_device_sent.append({"op": "accept", "transaction_id": transaction_id})
        return object()

    async def confirm_short_auth_string(self, transaction_id):
        sas = self.key_verifications.get(transaction_id)
        if sas is None:
            raise LocalProtocolError(
                f"Key verification with the transaction id {transaction_id} does not exist."
            )
        sas.accept_sas()
        sas.get_mac()  # raises unless accepted (mirrors matrix-nio)
        self.to_device_sent.append({"op": "confirm", "transaction_id": transaction_id})
        if sas.verified:
            self.verified_devices.add(
                (sas.other_olm_device.user_id, sas.other_olm_device.device_id)
            )
            user_devices = self.device_store.get(sas.other_olm_device.user_id, {})
            stored = user_devices.get(sas.other_olm_device.device_id)
            if stored is not None:
                stored.verified = True
        return object()

    async def cancel_key_verification(self, transaction_id, reject=False):
        sas = self.key_verifications.get(transaction_id)
        if sas is None:
            raise LocalProtocolError(
                f"Key verification with the transaction id {transaction_id} does not exist."
            )
        sas.canceled = True
        self.to_device_sent.append(
            {"op": "cancel", "transaction_id": transaction_id, "reject": reject}
        )
        return object()

    async def to_device(self, message, tx_id=None):
        self.to_device_sent.append({"op": "to_device", "message": message, "tx_id": tx_id})
        return object()

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
