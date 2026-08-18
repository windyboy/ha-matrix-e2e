"""Unit tests for the nio SAS MAC monkeypatch (W1N-179).

These tests exercise the patched ``receive_mac_event`` injected by
``_apply_sas_mac_patch`` without installing matrix-nio. The patch function
takes the Sas class as its first argument, so we can inject a fake Sas class
and assert on the resulting state transitions.

The regression under test: after the "no verified devices" branch sets the
SAS to ``canceled``, the original nio 0.26.0 code falls through and
unconditionally overwrites ``state`` with ``mac_received`` (missing ``return``).
This hides the ``m.key_mismatch`` cancel that the integration should send.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.matrix_e2ee.client import _apply_sas_mac_patch


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


# Apply the patch once to the fake class for all tests in this module.
_apply_sas_mac_patch(
    FakeSas,
    to_device_message=lambda *args, **kwargs: None,
    local_protocol_error=RuntimeError,
    sas_state=FakeSasState,
)


def _mac_event(*key_ids: str) -> SimpleNamespace:
    return SimpleNamespace(
        keys=b"MAC",
        mac={key_id: b"MAC" for key_id in key_ids},
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
