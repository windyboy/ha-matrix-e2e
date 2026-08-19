# matrix-nio 0.26.0 compatibility

> Audience: developers maintaining the SAS compatibility layer or upgrading matrix-nio.
>
> Language: [English](NIO_COMPAT.md) | [中文](NIO_COMPAT.zh.md)

The integration pins `matrix-nio[e2e]` to `0.26.0` and applies four runtime compatibility fixes to `nio.crypto.sas.Sas` before creating a client. They live in `custom_components/matrix_e2ee/nio_compat.py`; `_make_nio()` enables them through `apply_nio_compat_patches()`.

The fixes have been validated only against 0.26.0. The integration logs a warning if another version is installed. Each fix is idempotent and skips execution when nio is absent so unit tests can run with `FakeNio`.

## Summary

| Fix | Defect in 0.26.0 | User-visible result |
|---|---|---|
| SAS timeout | `_last_event_time` never refreshes, so the transaction always expires after 60 seconds | Verification is canceled during emoji comparison |
| Commitment | Uses a hexadecimal digest instead of the required unpadded Base64 | Element cancels with `m.key_mismatch` when it checks the commitment after key exchange |
| Emoji indices | Re-applies old bit slicing to final vodozemac indices | The two clients display different emoji |
| Legacy MAC | Negotiates v1 but uses the v2 Base64 wire encoding | Element rejects the MAC with `m.key_mismatch` |

## 1. SAS timeout

Entry point: `_apply_sas_timeout_patch()`.

matrix-nio 0.26.0 sets `_last_event_time` only when `Sas` is created. It never updates the value, so `timed_out` becomes `True` after 60 seconds even while messages are still being exchanged.

The replacement `timed_out` uses only `creation_time + _max_age` (5 minutes). The integration separately checks its shorter 240-second verification deadline during confirmation and inbound-event handling.

Before removing this fix, confirm that the new nio version refreshes activity time or has another correct timeout model.

## 2. SAS commitment

Entry point: `_apply_sas_commitment_patch()`.

Matrix defines the commitment in `m.key.verification.accept` as:

```text
Base64_without_padding(SHA-256(public_key || canonical_start_content))
```

matrix-nio 0.26.0 changed both generation and verification to `hexdigest()`. Two nio clients therefore agree with each other, but Element follows the specified Base64 representation and rejects the transaction.

The fix replaces both `from_key_verification_start()` and `_check_commitment()` to restore unpadded Base64. Check both directions when upgrading.

## 3. SAS emoji

Entry point: `_apply_sas_emoji_patch()`.

`EstablishedSas.bytes(info).emoji_indices` already returns seven final indices in the range 0–63. matrix-nio 0.26.0 applies the old libolm bit slicing again, producing a different display from Element.

The replacement `_generate_emoji()` maps those indices directly to the emoji table. Before removing it, confirm that the new nio version consumes `emoji_indices` directly.

## 4. Legacy MAC

Entry point: `_apply_sas_mac_patch()`.

matrix-nio 0.26.0 negotiates only `hkdf-hmac-sha256` (v1) but calls `calculate_mac()`, which produces the standard Base64 representation used by v2. The v1 wire format requires compatibility with libolm's non-standard Base64.

The replacement `get_mac()` and `receive_mac_event()` both select `calculate_mac_invalid_base64()` for the legacy method. Generation and verification must change together.

This fix also corrects a state error in `receive_mac_event()`: when no device passes MAC verification, the function must return after setting `canceled`; otherwise nio overwrites the state with `mac_received`.

## Upgrade checklist

Before upgrading `matrix-nio`:

1. Update `manifest.json` and development dependencies in an isolated branch.
2. Check that the new nio version maintains SAS activity time correctly.
3. Confirm that commitments use unpadded Base64, not `hexdigest()`.
4. Confirm that `_generate_emoji()` consumes vodozemac's final indices directly.
5. Inspect v1 and `.v2` MAC negotiation and encoding, and confirm that `receive_mac_event()` preserves cancellation.
6. Remove only fixes that upstream now implements correctly; do not remove the entire compatibility layer at once.
7. Run `tests/test_nio_compat.py` and the complete test suite.
8. Complete an end-to-end SAS verification with Element on a real Matrix homeserver.
9. Update the validated version range in this document.

## Related documentation

- [SAS architecture](SAS_ARCHITECTURE.md)
- [Development notes](DEVELOPMENT.md)
