# matrix-nio 0.26.0 compatibility patches

This integration pins `matrix-nio[e2e]==0.26.0` (see `manifest.json`) and applies
four runtime patches to `nio.crypto.sas.Sas` before every client is created.
The patches live in `custom_components/matrix_e2ee/nio_compat.py` and are applied
via `apply_nio_compat_patches()`, which `client.py` calls from `_make_nio()`.
Each patch fixes a nio 0.26.0 bug that breaks SAS interoperability with
Element / matrix-rust-sdk.

`apply_nio_compat_patches()` also emits a warning when the installed
`matrix-nio` release drifts from the `0.26.0` pin, since the patches are only
known to be correct for that release.

Every patch is:

- **Idempotent** — guarded by a `_matrix_e2ee_*_patched` flag on the class.
- **No-op when nio is absent** — each `_patch_nio_sas_*()` wrapper wraps the
  `from nio … import …` in `try/except` so the unit tests (which use `FakeNio`)
  run without nio installed.

## Patch matrix

| Patch | nio 0.26.0 bug | Symptom if removed | Ref |
|---|---|---|---|
| SAS timeout | `Sas._last_event_time` is assigned once in `__init__` and never refreshed, so `timed_out` is `True` 60 s after creation regardless of activity | SAS dies mid-emoji-comparison | W1N-172, PR #23 |
| SAS commitment | `hashlib.sha256(...).hexdigest()` instead of the spec's unpadded base64 | Element rejects `accept` with `m.key_mismatch` | PR #28, v0.2.8 |
| SAS emoji | re-slices vodozemac's final indices with the old libolm bit-slicing | emoji disagree → `m.mismatched_sas` | W1N-175, PR #29 |
| Legacy MAC | negotiates `hkdf-hmac-sha256` (v1) but computes/verifies with `.v2` valid base64 | Element rejects `mac` with `m.key_mismatch` | W1N-177, PR #30 |

## Patch details

### 1. SAS timeout — `_apply_sas_timeout_patch` (`nio_compat.py`)

nio 0.26.0 assigns `Sas._last_event_time` once in `__init__` and never updates
it, so `timed_out` flips `True` exactly 60 s after creation no matter how
recent the activity. A human comparing emoji typically needs more than 60 s.

The patch replaces the `timed_out` property with one keyed on
`creation_time + _max_age` (5 min). The integration's own 240 s timeout
(`VERIFICATION_TIMEOUT_SECONDS`) still fires first.

**If removed or nio is upgraded**: SAS will time out mid-comparison. On upgrade,
verify whether nio now refreshes `_last_event_time`; if so, drop this patch.

### 2. SAS commitment — `_apply_sas_commitment_patch` (`nio_compat.py`)

The Matrix spec requires the SAS commitment in `m.key.verification.accept` to be
the unpadded base64 of `SHA-256(pubkey || canonical_json)`. nio 0.26.0 switched
from `olm.sha256` (unpadded base64) to `hashlib.sha256(...).hexdigest()` in both
`from_key_verification_start` (responder) and `_check_commitment` (initiator).
The two changed together, so nio↔nio still agrees while Element (base64) cancels
with `m.key_mismatch`.

The patch restores unpadded base64 (`_sas_commitment`) in both directions.

**If removed or nio is upgraded**: Element rejects the SAS `accept`. On upgrade,
check whether nio emits base64 commitments again.

### 3. SAS emoji — `_apply_sas_emoji_patch` (`nio_compat.py`)

vodozemac's `EstablishedSas.bytes(info).emoji_indices` already returns the 7
final emoji indices (`[u8; 7]`, values 0–63). nio 0.26.0 still runs the old
libolm bit-slicing over them, treating the indices as raw bytes, so the rendered
emoji disagree with Element (`m.mismatched_sas`).

The patch returns the indices directly.

**If removed or nio is upgraded**: emoji mismatch. On upgrade, check whether nio
consumes `emoji_indices` directly.

### 4. Legacy MAC — `_apply_sas_mac_patch` (`nio_compat.py`)

nio 0.26.0 negotiates only `hkdf-hmac-sha256` (v1, no `.v2`), but computes and
verifies the MAC with the standard `calculate_mac` (the `.v2` wire format).
The v1 wire format is libolm's invalid-base64 output, so Element cancels with
`m.key_mismatch`.

The patch routes the legacy method to `calculate_mac_invalid_base64` in both
`get_mac` and `receive_mac_event` so generation and verification agree.

This patch also fixes a latent nio bug: `receive_mac_event`'s "no verified
devices" branch set `state = canceled` but then unconditionally overwrote it
with `mac_received` (missing `return`). The patched `receive_mac_event` returns
after that branch (W1N-179, PR #31). nio 0.26.0 upstream still has this bug.

**If removed or nio is upgraded**: legacy-MAC mismatch, and the missing-`return`
bug reappears. On upgrade, check whether nio provides `.v2` MAC and whether the
`receive_mac_event` missing-`return` was fixed upstream.

## Upgrade checklist

When bumping `matrix-nio` off `0.26.0`, before removing any patch confirm:

1. `Sas._last_event_time` is refreshed (or the timeout model changed).
2. The commitment is unpadded base64 (not hexdigest).
3. `_generate_emoji` consumes vodozemac `emoji_indices` directly.
4. Legacy `hkdf-hmac-sha256` uses `calculate_mac_invalid_base64`, and
   `receive_mac_event` no longer has the missing-`return` bug.

If any is still broken, keep that patch and update the "known to work with"
note in this file.
