"""matrix-nio 0.26.0 SAS compatibility patches.

These monkey-patches fix known matrix-nio 0.26.0 bugs that break SAS
verification interoperability with Element / matrix-rust-sdk. They live in
their own module so the bulk of ``client.py`` stays free of nio internals.

Every patch is:

- **Idempotent** — guarded by a ``_matrix_e2ee_*_patched`` flag on the class.
- **No-op when nio is absent** — each ``_patch_nio_sas_*()`` wrapper wraps the
  ``from nio … import …`` in ``try/except`` so unit tests (which use ``FakeNio``)
  run without nio installed.

Call :func:`apply_nio_compat_patches` once before building a client.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

# The exact matrix-nio release these patches were written against. They are
# known to be unnecessary (or wrong) on other releases, so we warn when the
# installed version drifts from the pin in ``manifest.json``.
NIO_COMPAT_VERSION = "0.26.0"


def _installed_nio_version() -> str | None:
    """Return the installed matrix-nio version, or None when it is absent."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - Python < 3.8
        return None
    try:
        return version("matrix-nio")
    except PackageNotFoundError:
        return None


def _warn_version_mismatch() -> None:
    """Warn if the installed matrix-nio release drifts from the pin."""
    installed = _installed_nio_version()
    if installed is not None and installed != NIO_COMPAT_VERSION:
        _LOGGER.warning(
            "matrix_e2ee pins matrix-nio==%s but %s is installed; "
            "SAS compatibility patches may not apply cleanly",
            NIO_COMPAT_VERSION,
            installed,
        )


def _apply_sas_timeout_patch(sas_cls: Any, canceled_state: Any) -> None:
    """Patch ``sas_cls.timed_out`` to ignore the 60 s event timeout (nio bug).

    matrix-nio 0.26.0 assigns ``Sas._last_event_time`` once in ``__init__`` and
    never refreshes it, so ``timed_out`` returns True exactly 60 s after creation
    regardless of activity. Keep only ``_max_age`` (5 min) so the SAS survives a
    slow human emoji comparison.
    """
    if getattr(sas_cls, "_matrix_e2ee_timeout_patched", False):
        return

    def timed_out(self: Any) -> bool:
        if self.verified or self.canceled:
            return False
        if datetime.now() - self.creation_time >= self._max_age:
            self.state = canceled_state
            self.cancel_code, self.cancel_reason = self._timeout_error
            return True
        return False

    sas_cls.timed_out = property(timed_out)
    sas_cls._matrix_e2ee_timeout_patched = True


def _patch_nio_sas_timeout() -> None:
    """Work around nio 0.26.0 ``_last_event_time`` bug. No-op when nio is absent."""
    try:
        from nio.crypto.sas import Sas, SasState
    except Exception:  # noqa: BLE001 — nio may not be installed (tests)
        return
    _apply_sas_timeout_patch(Sas, SasState.canceled)


def _sas_commitment(pubkey: str, canonical: str) -> str:
    """Return SHA-256(pubkey || canonical) as unpadded base64.

    This is the wire format the Matrix spec requires for the SAS commitment in
    ``m.key.verification.accept``. nio 0.26.0 emits ``.hexdigest()`` instead,
    which Element rejects with ``m.key_mismatch``.
    """
    digest = hashlib.sha256(pubkey.encode() + canonical.encode()).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def _apply_sas_commitment_patch(
    sas_cls: Any, to_canonical_json: Callable[[Any], str]
) -> None:
    """Rewrite nio 0.26.0 hexdigest commitments back to unpadded base64.

    nio 0.26.0 replaced ``olm.sha256`` (unpadded base64) with
    ``hashlib.sha256(...).hexdigest()`` in both ``from_key_verification_start``
    (responder, builds the ``accept`` commitment) and ``_check_commitment``
    (initiator, verifies the peer's commitment). Both changed together, so
    nio↔nio still agrees while Element/rust-sdk (base64) cancels with
    ``m.key_mismatch``. Restore base64 in both directions so neither nio↔nio
    nor nio↔Element regresses.
    """
    if getattr(sas_cls, "_matrix_e2ee_commitment_patched", False):
        return

    original_from_start = sas_cls.from_key_verification_start.__func__

    @classmethod
    def patched_from_start(
        cls, own_user, own_device, own_fp_key, other_olm_device, event
    ):
        sas = original_from_start(
            cls, own_user, own_device, own_fp_key, other_olm_device, event
        )
        canonical = to_canonical_json(event.source["content"])
        sas.commitment = _sas_commitment(sas.pubkey, canonical)
        return sas

    def patched_check_commitment(self, key):
        canonical = to_canonical_json(self.start_verification().content)
        return self.commitment == _sas_commitment(key, canonical)

    sas_cls.from_key_verification_start = patched_from_start
    sas_cls._check_commitment = patched_check_commitment
    sas_cls._matrix_e2ee_commitment_patched = True


def _patch_nio_sas_commitment() -> None:
    """Fix nio 0.26.0 SAS commitment encoding. No-op when nio is absent."""
    try:
        from nio.api import Api
        from nio.crypto.sas import Sas
    except Exception:  # noqa: BLE001 — nio may not be installed (tests)
        return
    _apply_sas_commitment_patch(Sas, Api.to_canonical_json)


def _apply_sas_emoji_patch(sas_cls: Any) -> None:
    """Stop nio 0.26.0 from re-slicing vodozemac's emoji indices.

    vodozemac's ``EstablishedSas.bytes(info).emoji_indices`` already returns
    the 7 final emoji indices (``[u8; 7]``, values 0-63). nio 0.26.0 still
    runs the old libolm bit-slicing over them, treating the indices as raw
    bytes, so the rendered emoji disagree with Element (``m.mismatched_sas``).
    Return the indices directly.
    """
    if getattr(sas_cls, "_matrix_e2ee_emoji_patched", False):
        return

    def patched_generate_emoji(self, extra_info):
        assert self.established_sas
        indices = self.established_sas.bytes(extra_info).emoji_indices
        return [self.emoji[index] for index in indices]

    sas_cls._generate_emoji = patched_generate_emoji
    sas_cls._matrix_e2ee_emoji_patched = True


def _patch_nio_sas_emoji() -> None:
    """Fix nio 0.26.0 SAS emoji rendering. No-op when nio is absent."""
    try:
        from nio.crypto.sas import Sas
    except Exception:  # noqa: BLE001 — nio may not be installed (tests)
        return
    _apply_sas_emoji_patch(Sas)


def _apply_sas_mac_patch(
    sas_cls: Any,
    to_device_message: Any,
    local_protocol_error: Any,
    sas_state: Any,
) -> None:
    """Route legacy hkdf-hmac-sha256 to vodozemac's libolm-compat MAC.

    nio 0.26.0 negotiates ``hkdf-hmac-sha256`` (v1) but computes and verifies
    the MAC with the standard ``calculate_mac`` (valid base64, the ``.v2``
    wire format). The v1 wire format is libolm's invalid-base64 output, so
    Element cancels with ``m.key_mismatch``. Route the legacy method to
    ``calculate_mac_invalid_base64`` in both directions so generation and
    verification agree.
    """
    if getattr(sas_cls, "_matrix_e2ee_mac_patched", False):
        return

    def _select_mac_func(self):
        if self.chosen_mac_method == "hkdf-hmac-sha256":
            return self.established_sas.calculate_mac_invalid_base64
        return self.established_sas.calculate_mac

    def get_mac(self):
        if not self.sas_accepted:
            raise local_protocol_error("SAS string wasn't yet accepted")
        if self.state == sas_state.canceled:
            raise local_protocol_error(
                "SAS verification was canceled, can't generate MAC."
            )
        key_id = f"ed25519:{self.own_device}"
        assert self.established_sas
        assert self.chosen_mac_method
        calculate_mac = _select_mac_func(self)
        info = (
            "MATRIX_KEY_VERIFICATION_MAC"
            f"{self.own_user}{self.own_device}"
            f"{self.other_olm_device.user_id}{self.other_olm_device.id}"
            f"{self.transaction_id}"
        )
        mac = {key_id: calculate_mac(self.own_fp_key, info + key_id)}
        content = {
            "mac": mac,
            "keys": calculate_mac(key_id, info + "KEY_IDS"),
            "transaction_id": self.transaction_id,
        }
        return to_device_message(
            "m.key.verification.mac",
            self.other_olm_device.user_id,
            self.other_olm_device.id,
            content,
        )

    def receive_mac_event(self, event):
        if self.verified:
            return
        if not self._event_ok(event):
            return
        if self.state != sas_state.key_received:
            self.state = sas_state.canceled
            self.cancel_code, self.cancel_reason = self._unexpected_message_error
            return
        info = (
            f"MATRIX_KEY_VERIFICATION_MAC{self.other_olm_device.user_id}"
            f"{self.other_olm_device.id}{self.own_user}{self.own_device}"
            f"{self.transaction_id}"
        )
        key_ids = ",".join(sorted(event.mac.keys()))
        assert self.established_sas
        assert self.chosen_mac_method
        calculate_mac = _select_mac_func(self)
        if event.keys != calculate_mac(key_ids, info + "KEY_IDS"):
            self.state = sas_state.canceled
            self.cancel_code, self.cancel_reason = self._key_mismatch_error
            return
        for key_id, key_mac in event.mac.items():
            try:
                key_type, device_id = key_id.split(":", 2)
            except ValueError:
                self.state = sas_state.canceled
                self.cancel_code, self.cancel_reason = self._invalid_message_error
                return
            if key_type != "ed25519":
                self.state = sas_state.canceled
                self.cancel_code, self.cancel_reason = self._key_mismatch_error
                return
            if device_id != self.other_olm_device.id:
                continue
            other_fp_key = self.other_olm_device.ed25519
            if key_mac != calculate_mac(other_fp_key, info + key_id):
                self.state = sas_state.canceled
                self.cancel_code, self.cancel_reason = self._key_mismatch_error
                return
            self.verified_devices.append(device_id)
        if not self.verified_devices:
            self.state = sas_state.canceled
            self.cancel_code, self.cancel_reason = self._key_mismatch_error
            return
        self.state = sas_state.mac_received

    sas_cls.get_mac = get_mac
    sas_cls.receive_mac_event = receive_mac_event
    sas_cls._matrix_e2ee_mac_patched = True


def _patch_nio_sas_mac() -> None:
    """Fix nio 0.26.0 legacy SAS MAC encoding. No-op when nio is absent."""
    try:
        from nio.crypto.sas import Sas, SasState
        from nio.event_builders import ToDeviceMessage
        from nio.exceptions import LocalProtocolError
    except Exception:  # noqa: BLE001 — nio may not be installed (tests)
        return
    _apply_sas_mac_patch(Sas, ToDeviceMessage, LocalProtocolError, SasState)


def apply_nio_compat_patches() -> None:
    """Apply all four SAS patches; idempotent and no-op when nio is absent."""
    _warn_version_mismatch()
    _patch_nio_sas_timeout()
    _patch_nio_sas_commitment()
    _patch_nio_sas_emoji()
    _patch_nio_sas_mac()
