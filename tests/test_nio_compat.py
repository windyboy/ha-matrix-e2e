"""Characterization tests for the nio_compat SAS monkey-patches.

These tests exercise the four ``_apply_sas_*`` patch functions (and the
version guard) without installing matrix-nio. Each patch takes the target
``Sas`` class (and its dependencies) as arguments, so we inject fake classes
and assert on the resulting behavior.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import custom_components.matrix_e2ee.nio_compat as nio_compat
from custom_components.matrix_e2ee.nio_compat import (
    _apply_sas_commitment_patch,
    _apply_sas_emoji_patch,
    _apply_sas_mac_patch,
    _apply_sas_timeout_patch,
    _sas_commitment,
)


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


class FakeSasState:
    """Stand-in for nio.crypto.sas.SasState members used by the patch."""

    canceled = "canceled"
    key_received = "key_received"
    mac_received = "mac_received"


def _fake_calc_mac(*_args: object) -> bytes:
    """Deterministic MAC so ``event.keys``/``event.mac`` can be precomputed."""
    return b"MAC"


class FakeSas:
    """Minimal Sas-like object consumed by the patched receive_mac_event."""

    def __init__(self, peer_device_id: str = "OTHER_DEVICE") -> None:
        self.verified = False
        self.state = FakeSasState.key_received
        self._unexpected_message_error = ("m.unexpected_message", "unexpected")
        self._key_mismatch_error = ("m.key_mismatch", "key mismatch")
        self._invalid_message_error = ("m.invalid_message", "invalid")
        self.other_olm_device = SimpleNamespace(
            user_id="@peer:example.org",
            id=peer_device_id,
            ed25519="PEER_ED25519_KEY",
        )
        self.own_user = "@hass:example.org"
        self.own_device = "HABOTABC"
        self.transaction_id = "txn-1"
        self.established_sas = SimpleNamespace(calculate_mac=_fake_calc_mac)
        # Not "hkdf-hmac-sha256", so _select_mac_func routes to calculate_mac.
        self.chosen_mac_method = "hkdf-hmac-sha256.v2"
        self.verified_devices: list[str] = []
        self.own_fp_key = "OWN_ED25519_KEY"

    def _event_ok(self, _event: object) -> bool:
        return True


# Apply the patch once to the fake class for the receive_mac_event tests below.
_apply_sas_mac_patch(
    FakeSas,
    to_device_message=lambda *args, **kwargs: None,
    local_protocol_error=RuntimeError,
    sas_state=FakeSasState,
)


def _mac_event(*key_ids: str) -> SimpleNamespace:
    return SimpleNamespace(
        keys=b"MAC",
        mac=dict.fromkeys(key_ids, b"MAC"),
    )


def test_receive_mac_event_cancels_when_no_device_matches() -> None:
    """A MAC whose key device_ids all mismatch must cancel, not reach mac_received."""
    sas = FakeSas(peer_device_id="OTHER_DEVICE")
    # A key for a device that is not the peer: the loop skips it (continue),
    # leaving verified_devices empty, which must cancel with m.key_mismatch.
    event = _mac_event("ed25519:SOME_OTHER_DEVICE")

    sas.receive_mac_event(event)

    assert sas.state == FakeSasState.canceled
    assert sas.cancel_code == "m.key_mismatch"
    assert sas.verified_devices == []


def test_receive_mac_event_reaches_mac_received_on_match() -> None:
    """A MAC whose key matches the peer device must transition to mac_received."""
    sas = FakeSas(peer_device_id="OTHER_DEVICE")
    event = _mac_event("ed25519:OTHER_DEVICE")

    sas.receive_mac_event(event)

    assert sas.state == FakeSasState.mac_received
    assert sas.verified_devices == ["OTHER_DEVICE"]


def test_version_guard_warns_on_mismatch(monkeypatch, caplog):
    """A drift from the pinned matrix-nio release must log a warning."""
    monkeypatch.setattr(nio_compat, "_installed_nio_version", lambda: "0.25.0")
    with caplog.at_level(
        logging.WARNING, logger="custom_components.matrix_e2ee.nio_compat"
    ):
        nio_compat._warn_version_mismatch()
    assert "matrix-nio==0.26.0 but 0.25.0" in caplog.text


def test_version_guard_silent_when_pinned(monkeypatch, caplog):
    """No warning when the installed release matches the pin."""
    monkeypatch.setattr(nio_compat, "_installed_nio_version", lambda: "0.26.0")
    with caplog.at_level(logging.WARNING):
        nio_compat._warn_version_mismatch()
    assert "may not apply cleanly" not in caplog.text
