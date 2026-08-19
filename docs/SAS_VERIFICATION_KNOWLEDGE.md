# Matrix device verification (SAS) — knowledge dump

> Language: [English](SAS_VERIFICATION_KNOWLEDGE.md) | [中文](SAS_VERIFICATION_KNOWLEDGE.zh.md)

> This document is the **full dump of known knowledge** about Matrix device verification in `ha-matrix-e2ee` (`custom_components/matrix_e2ee`, matrix-nio 0.26.0 + vodozemac). Scope: official spec, official docs / implementation drift, verification flows, code map, and pitfalls. Goal: avoid re-investigating, re-implementing, and re-hitting the same bugs.
>
> Companion docs: [`docs/DEVICE_VERIFICATION.md`](DEVICE_VERIFICATION.md) (plain-language walkthrough + source-level flow), [`SECURITY.md`](../SECURITY.md) (trust model and threat boundary), [`docs/DEVELOPMENT.md`](DEVELOPMENT.md) (test discipline).
>
> Maintenance rule: after finishing or discovering a verification-related issue, sync the conclusion into the matching section here **and** the Issue index table. Do not leave it only in Linear.

---

## 1. Official spec (Matrix Spec)

Source: matrix-spec `content/client-server-api/modules/end_to_end_encryption.md` (note: **not** `sas_verification.md`, which 404s). Two mechanisms: the **verification framework** (`m.key.verification.request/ready/start/.../done/cancel`) and the **SAS method** (`m.sas.v1`).

### 1.1 Verification framework

- **Message channel**: same-account verification uses **to-device** messages; between different users, **in-room** messages are recommended. This project (the bot verifying another device of its own account) uses to-device.
- **Session ID**: to-device uses `transaction_id`; in-room uses the event ID.
- **Flow**: `request` (initiator declares supported methods) → the other side is prompted to accept → `ready` (responder replies with the method intersection) → user picks a method → `start` → in-method exchange → `done`. **At any point** either side may send `cancel` (with a code).
- **Multi-device broadcast**: a to-device `request` is broadcast to all of the other user’s devices (same txn). After one device accepts, the others get `cancel` (code `m.accepted`); if the user rejects on another device the code is `m.user`. in-room sends the request once and does not cancel-to-others.
- **Prompt auto-dismiss**: to-device is **10 minutes** from `timestamp` (in-room from `origin_server_ts`), or **2 minutes** after receipt, whichever comes first. Rejecting a request **must** send `cancel` with code=`m.user`.

### 1.2 SAS method `m.sas.v1` — security idea

Inspired by **ZRTP hash commitment**: the responder first sends a **hash of its own public key (commitment)** in `accept`; the initiator sends its own public key only after receiving the commitment. An attacker gets one guess: verifying n bits succeeds with probability `1/2^n` (full SAS is 40+ bits → ~1/10^12). Two phases:

1. **Key agreement**: each side generates an ephemeral Curve25519 key pair, exchanges public keys (protected by the commitment), ECDH yields a shared secret.
2. **Key verification**: HMAC derived from the shared secret mutually authenticates each side’s device ed25519 key.

### 1.3 Full SAS flow (18 steps, initiator = Alice / responder = Bob)

| # | Action | Message | Content highlights |
|---|---|---|---|
| 1 | Offline safe meeting | — | Both sides compare what the devices display |
| 2 | Start verification | — | Either side initiates |
| 3 | Alice sends start | `m.key.verification.start` | **Must already have Bob’s device key**; includes key_agreement / hash / mac_method / short_authentication_string |
| 4 | Bob picks algorithms | — | From Alice’s supported lists: key agreement / hash / MAC / SAS method |
| 5 | Bob must already hold Alice’s device key | — | Otherwise key query first |
| 6 | Bob generates an ephemeral Curve25519 pair | — | SHA-256 of the public key |
| 7 | Bob replies accept | `m.key.verification.accept` | Contains `commitment` (hash of its own public key) |
| 8 | Alice stores the commitment | — | Checked later |
| 9 | Alice generates an ephemeral pair, sends key | `m.key.verification.key` | **Public key only** |
| 10 | Bob sends its own key | `m.key.verification.key` | Commitment-protection risk is gone |
| 11 | Alice checks the commitment | — | `commitment == hash(Bob public key + Alice’s start content)` |
| 12 | Both sides ECDH | — | Ephemeral keys → shared secret |
| 13 | Both sides display SAS | — | emoji / decimal (user picks if several methods) |
| 14 | User compares | — | Continue only if both sides match |
| 15 | Compute MAC | — | For each key to verify + the key-ID list |
| 16 | Both sides send mac | `m.key.verification.mac` | |
| 17 | Verify the peer MAC | — | Every key MAC + the key-list MAC match → device verified |
| 18 | Both sides send done | `m.key.verification.done` | |

**Which keys to verify**: this device’s ed25519 key + master signing key (cross-user verification **should** include the MSK; “verify a single device of someone else” is deprecated).

### 1.4 Error handling and cancel codes

- Cancel is allowed at any time; **10 minute timeout** (a txn idle for 10 min also expires).
- Multiple starts from the same device → recipient cancels all of them.
- **Unknown txn → cancel** (inbound `start` / `cancel` excepted).
- No shared methods → cancel; SAS mismatch → cancel; out of order → cancel.
- SAS-specific cancel codes: `m.unknown_method`, `m.mismatched_commitment`, `m.mismatched_sas`.
- Framework codes: `m.user` (user rejected), `m.accepted`, `m.timeout`, `m.unexpected_message`, `m.key_mismatch`.

### 1.5 MAC computation (wire format — easy to get wrong)

- **HKDF parameters**: HKDF-SHA256, IKM = shared secret, **no salt**.
- **MAC info string** (byte-concatenated, no separators):
  `MATRIX_KEY_VERIFICATION_MAC` + MAC-sender `user_id` + `device_id` + peer `user_id` + `device_id` + `transaction_id`
  + `key_id` (single key); for the key list use the string `KEY_IDS`.
- **HMAC object**:
  - single key → public key encoded as **unpadded base64**;
  - key list → lexicographically sorted, **comma-joined, no spaces** list of `alg:id`, e.g.
    `ed25519:Cross+Signing+Key,ed25519:DEVICEID`.
- MAC values are base64-encoded into the `mac` and `keys` fields of the `mac` message.
- **Version (critical)**: the spec says “all current implementations should use `hkdf-hmac-sha256.v2`”. Legacy `hkdf-hmac-sha256` (v1) used **invalid base64 encoding** because of a libolm implementation bug; v2 corrects it. **v1 is deprecated; if both sides support v2 they MUST NOT use v1**.

### 1.6 SAS derivation

- **HKDF info (`curve25519-hkdf-sha256`)**:
  `MATRIX_KEY_VERIFICATION_SAS|` + start-initiator `user_id|` + `device_id|` + start-side public key (unpadded base64) `|`
  + accept-side `user_id|` + `device_id|` + accept-side public key (unpadded base64) `|` + `transaction_id`.
  Deprecated `curve25519` method: no `|` separators, no public keys.
- **decimal**: take 5 bytes → three 13-bit numbers (0–8191) each +1000 → three numbers in 1000–9191.
  Bit ops: `(B0<<5|B1>>3)+1000`; `((B1&0x7)<<10|B2<<2|B3>>6)+1000`; `((B3&0x3F)<<7|B4>>1)+1000`.
- **emoji**: take 6 bytes → first 42 bits → seven 6-bit groups → seven indices 0–63 → look up the 64-slot emoji table
  (JSON in the `matrix-org/matrix-spec` repo at `data-definitions/sas-emoji.json`).

### 1.7 Cross-signing (why this project does **not** do it)

Three ed25519 pairs: **MSK** (master signing key, signs USK/SSK, represents user identity), **USK** (user-signing key, visible only to yourself, signs others’ MSK), **SSK** (self-signing key, signs your own device keys). Effect: verify the MSK once and you can trust all of that user’s devices. This integration **does not** import SSSS / Key Backup (W1N-147 conclusion) because **nio 0.26 cannot self-sign cross-signing keys**, and self-signing would put cross-signing authority on the HA host (violates the trust boundary). It can verify another user’s MSK, but the bot itself cannot bootstrap a new device.

---

## 2. Official spec vs nio 0.26 drift (docs / implementation — easiest to re-hit)

| # | Spec / expectation | nio 0.26.0 actual | Fix | Source |
|---|---|---|---|---|
| 1 | `request → ready` handled by the framework | **Not implemented**; request is parsed as `UnknownToDeviceEvent` and dropped → Element shows cancelled | Integration-built `_handle_verification_request` + `_send_verification_ready` | W1N-173, PR #25 |
| 2 | Session has a 10-minute activity window | `Sas._last_event_time` is assigned only in `__init__` and **never refreshed** → `timed_out` is always true after 60s; `clear_verifications()` cancels every sync | monkeypatch `Sas.timed_out`: keep only 5 min `_max_age`; integration timeout 240s fires first | W1N-172, PR #23 |
| 3 | commitment is **unpadded base64** | After the vodozemac migration it sends **hexdigest**; Element rejects (`m.key_mismatch`) | `_apply_sas_commitment_patch`: both `from_key_verification_start` + `_check_commitment` restored to unpadded base64 | PR #28, v0.2.8 |
| 4 | emoji indices come from the crypto layer | vodozemac already returns 7 final indices; nio still **re-slices** them with libolm-era bit-slicing → emoji disagree, `m.mismatched_sas` | monkeypatch `_generate_emoji` to use the indices directly | W1N-175, PR #29 |
| 5 | Negotiating `hkdf-hmac-sha256` (v1) means libolm **invalid base64** on the wire | nio uses standard `calculate_mac` → MAC stage fails | monkeypatch `get_mac` + `receive_mac_event`, pick `calculate_mac_invalid_base64` from `chosen_mac_method` (libolm-compat feature) | W1N-177, PR #30 |
| 6 | Prefer / only use `.v2` MAC | nio 0.26 **only negotiates v1, no `.v2`** | Keep v1 + invalid_base64 to interop with rust-sdk / Element; **do not** add `.v2` yourself (widens negotiation while nio has no implementation) | W1N-177 |
| 7 | `keys_query` should cover all parties in the session | Only users in shared encrypted rooms → bot does not share a room with its own devices → **never queries itself** → inbound SAS cannot be built | After login / restore, add own `user_id` to `users_for_key_query` and warm the cache | W1N-166, PR #18 |
| 8 | Device keys should exist before receiving start | First start for a new device hits device_store KeyError → drop + no SAS | `_repair_dropped_start`: query keys, then re-feed the same start to `nio.olm.handle_key_verification` | W1N-170, PR #23 |
| 9 | Verification failure should send `m.key_mismatch` cancel | `receive_mac_event` “no verified devices” branch sets canceled then **misses `return`**, next line overwrites with `mac_received` (upstream nio bug) | Integration monkeypatch already adds `return` (W1N-179, PR #31); **upstream nio 0.26.0 is still unfixed**, re-check on upgrade | W1N-179 |
| 10 | After MAC on the request flow both sides send `done` to finish | **Not implemented**; done is neither sent nor parsed (`to_device.py` only dispatches start/accept/key/mac/cancel) → Element stuck in `WaitingForDone` until timeout | Integration `_send_verification_done`: send `done` when `sas.verified` becomes True (content is only `transaction_id`; send unconditionally; start flow is safely ignored by Element) | W1N-183 |

Also: nio’s `Sas.verified = (state == mac_received) ∧ sas_accepted`. `get_mac()` raises `LocalProtocolError` when `sas_accepted=False` — integration code once called `get_mac` bare and the `except Exception` swallowed it, so the flow hung (W1N-142).

---

## 3. Verification flows (what this project actually does)

### 3.1 External interface (settled)

**Services** (admin-only enforced with `async_register_admin_service`, W1N-150):

| Service | Permission | Notes |
|---|---|---|
| `start_verification` | **admin** | bot starts SAS (not needed when Element initiates inbound) |
| `confirm_verification` | **admin** | confirm after human emoji comparison; internally = `accept_sas` + `get_mac` (then `verify_device` when verified) |
| `cancel_verification` | **admin** | cancel |
| `verify_device_by_fingerprint` | **admin** | one-sided trust; `user_id` + `device_id` + `ed25519` **exact match**, else `fingerprint_mismatch` |
| `reauthenticate` | **admin** | Config Flow reauth |
| `get_fingerprint` | ordinary registered | read-only; returns the bot’s `ed25519` / `curve25519` |
| `send_message` | ordinary registered | send a message |

**Events**:
- `matrix_e2ee_verification` — stages: `started` / `sas` / `done` / `canceled` / `timeout`. Only `sas` carries `emojis` + `expires_at`; other stages carry only `transaction_id` / `user_id` / `device_id` (`canceled` may also carry `code` / `reason`)
  (W1N-145 `VerificationPrompt`: `transaction_id/user_id/device_id/emojis/expires_at`).
- `matrix_e2ee_fingerprint` — emit the bot fingerprint after startup.
- `matrix_e2ee_command` / `matrix_e2ee_error` — command events (only when `verified=True` and the allowlist matches).

**Error codes** (`const.py`): `verification_peer_denied` (initiator is not the bot’s own account or `verification_peer_users`), `verification_timeout`
(integration timeout 4 min), `fingerprint_mismatch`, `invalid_transaction`, `device_missing`.

### 3.2 Bidirectional flow (wire timing)

**Direction A: Element initiates (inbound)** — driven by `handle_to_device_event` + `_handle_verification_request`:

```
Element                      HA bot (matrix_e2ee)
   │  m.key.verification.request {txn, methods:[m.sas.v1], timestamp}
   │ ──────────────────────────────────────────────►  _handle_verification_request
   │                                                    validate: sender allowed / from_device, txn non-empty /
   │                                                    methods include m.sas.v1 / timestamp in bounds
   │ ◄──────────────────────────────────────────────  m.key.verification.ready {methods:[m.sas.v1]}
   │  m.key.verification.start {txn, ...}
   │ ──────────────────────────────────────────────►  handle_to_device_event (start branch)
   │                                                    _get_sas==None → _repair_dropped_start (query keys, re-feed)
   │                                                    accept_key_verification(txn) → emit started
   │ ◄──────────────────────────────────────────────  m.key.verification.accept (inside nio, with commitment)
   │  m.key.verification.key ...                       key exchange → both sides show emoji
   │ ──────────────────────────────────────────────►  (key branch) emit sas {emojis, expires_at}
   │   HA-side human compares emoji → confirm_verification(txn)
   │                                                    confirm_short_auth_string(txn)
   │                                                    = accept_sas + get_mac + (verified→verify_device)
   │ ◄──────────────────────────────────────────────  m.key.verification.mac (sent once, W1N-169)
   │  m.key.verification.mac → (mac branch) sas.verified → emit done
   │ ◄──────────────────────────────────────────────  m.key.verification.done (_send_verification_done, W1N-183)
   │  m.key.verification.done → Element WaitingForDone → Done, verification complete
```

**Direction B: bot initiates (outbound)** — `start_verification(user_id, device_id)` → `nio.start_key_verification(device)` sends
start; later accept / key / emoji same as direction A; after comparison, the same `confirm_verification` finishes it. Sending key is **entirely nio’s job**
(`if not sas.we_started_it: share_key()` inside `handle_key_verification`); the integration no longer calls `share_key()` itself (W1N-169).

**State-machine notes**:
- The integration records a monotonic timestamp **the first time a verification is established** (`_mark_sas_started` uses `setdefault`; outbound `async_start_verification`, inbound start, and the key/mac branches all call it, but it writes only on first insert and is not refreshed later); timeout = `sas.timed_out` **or**
  monotonic delta ≥ 240s (`_sas_is_timed_out`). On timeout → `_timeout_verification`: cancel + emit `verification_timeout`.
- `confirm_verification` is the **only** path that completes verification: check timeout first, then `nio.confirm_short_auth_string(txn)`; `sas.verified`
  → emit `done`, else emit `sas` (wait for the user to confirm again).
- Inbound gating: every branch (start/key/mac/cancel) uses the same `_bootstrap_allowed(sender)` — `sender == session.user_id`
  **or** `sender ∈ verification_peer_users`, else emit `verification_peer_denied` (W1N-143).

### 3.3 Path 2: one-sided fingerprint verification (degraded / fallback)

`get_fingerprint` reads the bot fingerprint → Element “Manually Verify by Text”; trust the peer with `verify_device_by_fingerprint`
(after exact match, `nio.olm.verify_device`; **local one-sided trust, not SAS, no SAS events**).

---

## 4. Code map (`custom_components/matrix_e2ee/`)

### client.py (~1450 lines; SAS patches moved to `nio_compat.py`; models stay in this file)

**nio patch area** (moved to `nio_compat.py`, entry `apply_nio_compat_patches()`):

| Function | Role |
|---|---|
| `_apply_sas_timeout_patch` | monkeypatch `Sas.timed_out`: verified/canceled → False; `now-creation ≥ _max_age(5min)` → canceled + `_timeout_error` + True. **Ignores nio’s 60s event-timeout bug** |
| `_sas_commitment(pubkey, canonical)` | unpadded base64 of SHA-256(pubkey+canonical) |
| `_apply_sas_commitment_patch` | patch `from_key_verification_start` (accept commitment) + `_check_commitment` (initiator check) → unpadded base64 |
| `_apply_sas_emoji_patch` | `_generate_emoji` maps `established_sas.bytes(info).emoji_indices` directly, no bit-slicing |
| `_apply_sas_mac_patch` | see below |
| `_select_mac_func` | `chosen_mac_method=="hkdf-hmac-sha256"` → `calculate_mac_invalid_base64`, else `calculate_mac` |
| `get_mac` (patch) | `sas_accepted=False` / canceled raises `LocalProtocolError`; builds info per spec, `ed25519:{own_device}` + `KEY_IDS` |
| `receive_mac_event` (patch) | verified → return; `state!=key_received` → canceled; KEY_IDS check → `_key_mismatch_error`; per-key device_id match + MAC check → verified_devices; empty → canceled (**`return` added, W1N-179 fixed**) |
| `_patch_nio_sas_timeout/_commitment/_emoji/_mac` | `try: from nio … except Exception: return` (no-op in tests without nio) |

**Verification services (952–1690)**:

| Function | Line | Role |
|---|---|---|
| `enable_verification_callbacks` | 952 | Register `handle_to_device_event` (KeyVerificationEvent) + `_handle_verification_request` (ToDeviceEvent) |
| `_emit_verification(stage, **extra)` | 963 | fire `matrix_e2ee_verification` + warning log |
| `_verification_expires_at` | 971 | now UTC + `_verification_timeout` as ISO string |
| `_lookup_device` / `_get_sas` | 977 / 990 | look up device in device_store / `nio.key_verifications.get(txn)` |
| `_sas_party` / `_sas_emojis` | 996 / 1010 | extract peer (user, device) / `sas.get_emoji()` (exceptions swallowed → None, `list[list[str]]`) |
| `_mark_sas_started` / `_sas_is_timed_out` | 1026 / 1029 | record monotonic time / timeout check |
| `async_start_verification` | 1113 | reject soft logout; look up device → `nio.start_key_verification`; emit `started` |
| `async_verify_device_by_fingerprint` | 1163 | `actual.strip()!=ed25519.strip()` exact equality (W1N-159) → `fingerprint_mismatch`; `nio.olm.verify_device` |
| `async_confirm_verification` | 1195 | timeout check → `nio.confirm_short_auth_string(txn)`; verified → emit `done`, else emit `sas`. **Only path that verifies a device** |
| `async_cancel_verification` | 1249 | `nio.cancel_key_verification(txn, reject=False)` → emit `canceled` |
| `_timeout_verification` | 1281 | cancel + emit `verification_timeout` + `timeout` |
| `_bootstrap_allowed(sender)` | 1314 | `sender==session.user_id` or `sender in verification_peer_users` (W1N-143 gate) |
| `_repair_dropped_start` | 1312 | `_query_device_keys(sender)` then re-feed `nio.olm.handle_key_verification(event)` (W1N-170) |
| `_handle_verification_request` | 1338 | parse request; validate sender/txn/methods/timestamp → `_send_verification_ready`; **does not build SAS state** (W1N-173) |
| `_send_verification_ready` | 1401 | reply `ready` {from_device: own, methods:[m.sas.v1], transaction_id} |
| `_send_verification_done` | 1432 | reply `done` {transaction_id} (unconditional; on the request flow Element leaves `WaitingForDone` for `Done`; start flow safely ignores) (W1N-183) |
| `handle_to_device_event` | 1477 | main dispatch: gate → cancel → timeout → start (emoji check / repair / accept / emit) → key (emit sas) → mac (verified → send done → emit done) |
| `_verification_kind` | 1624 | class name / type string → start/key/mac/cancel |
| `_request_timestamp_valid` | 1613 | future ≤ 5 min and age ≤ 10 min |
| `_transaction_id_from_verifications` | 1646 | match other user+device; unique match is the fallback |
| `_verification_error_code` | 1662 | timeout → `verification_timeout`; LocalProtocolError / does not exist → `invalid_transaction`; unverified → `unverified_device`; else `send_failed` |

### const.py (80 lines)

`SERVICE_START/CONFIRM/CANCEL_VERIFICATION`, `SERVICE_VERIFY_DEVICE_BY_FINGERPRINT`; `ATTR_ED25519/TRANSACTION_ID/
USER_ID/DEVICE_ID`; `EVENT_VERIFICATION` / `EVENT_FINGERPRINT`; error codes in §3.1; `VERIFICATION_TIMEOUT_SECONDS=240`;
`VERIFICATION_REQUEST_MAX_FUTURE_MS` / `MAX_AGE_MS`; `SAS_METHOD_V1="m.sas.v1"`; `VERIFICATION_REQUEST/READY/START/
ACCEPT/KEY/MAC/DONE/CANCEL` type strings.

### __init__.py

`_register_services`(143) service → client method map; `_fire_event`(127); `_options`(130) reads `allowed_users/allowed_rooms/verification_peer_users`;
`async_setup_entry`(268) / `async_unload_entry`(321).

---

## 5. Things to watch (checklist / lessons)

**Protocol / wire**
1. **MAC generation and verification must change together** (`get_mac` + `receive_mac_event` monkeypatched in lockstep). Changing only one side = mutual incompatibility.
2. **Do not add `.v2` yourself**: nio has no `_mac_v2`; do not widen negotiation.
3. **Who is allowed to send accept/key/mac**: key belongs to nio internals (`we_started_it` decides); mac is sent only by `confirm_verification`;
   the integration only maps events and triggers human confirm (W1N-169 double-send lesson).
4. commitment must be unpadded base64 (not hexdigest); emoji indices must not be re-sliced; legacy MAC uses invalid base64.
5. SAS HKDF info / MAC info are both **byte-concatenated with no separators**; field order (initiator first) and direction must not be swapped.

**nio state-machine traps**
6. `Sas.timed_out` is polluted by nio’s 60s bug → must monkeypatch; fire the 240s integration timeout first.
7. `get_mac()` raises when `sas_accepted=False` → go through `confirm_short_auth_string`; do not call it bare.
8. Inbound start needs device keys: when `_get_sas==None`, `_repair_dropped_start` first (query keys, re-feed); do not give up immediately.
9. `keys_query` does not include the bot itself by default → actively warm own keys after login / restore.

**Trust / security**
10. **No auto-trust**: same account ≠ trusted, including `@bot`’s own devices (W1N-153).
11. Fingerprint comparison **must be exact equality**; no `casefold()` (unpadded base64 is case-sensitive, W1N-159).
12. verification / fingerprint / reauth services must be admin-only (W1N-150); every inbound branch uses the same `_bootstrap_allowed` gate (W1N-143).
13. request timestamp check: future ≤ 5 min, age ≤ 10 min (W1N-173).
14. fail-closed: unverified devices do not fire command events, never fall back to plaintext, `ignore_unverified_devices` is never enabled, logs never record secrets (W1N-136).

**Ops / tests**
15. Device ID changed = store lost → re-verify using the runbook; do not silently create a new device (W1N-134/138).
16. Tests use only FakeNio/FakeSas; **protocol-level bugs are invisible to unit tests** → missing real homeserver e2e (W1N-171 Backlog).
17. Upstream nio `receive_mac_event` missing-`return` bug: our monkeypatch already adds `return` (W1N-179, PR #31); upstream nio 0.26.0 is still unfixed; re-check when upgrading nio.

---

## 6. Issue index (Linear → knowledge)

Status: ✅ Done ｜ ⏳ Backlog

| Issue | Topic | Knowledge section | Status |
|---|---|---|---|
| W1N-134 | M2 E2EE login / atomic session / restore the same device | §5-15 | ✅ |
| W1N-135 | M2 encrypted send/receive + sync tokens (consumes verification state) | §3 | ✅ |
| W1N-136 | M2 fail-closed, allowlist, do not log secrets | §5-14 | ✅ |
| W1N-137 | M3 SAS services/events + verified-device policy | §3.1 | ✅ |
| W1N-138 | M4 soft logout / store-loss recovery / diagnostics | §5-15 | ✅ |
| W1N-141 | Integration device-verification guide (parent of A–F) | — | ✅ |
| W1N-142 | A SAS auto-completion (fix get_mac hang) → later undone by W1N-153 auto-confirm removal | §2 / §5-7 | ✅ |
| W1N-143 | B inbound SAS initiator gate | §3.2 / §5-12 | ✅ |
| W1N-144 | C one-sided fingerprint verification (get_fingerprint / verify_device) | §3.3 | ✅ |
| W1N-145 | D VerificationPrompt model + expires_at | §3.1 | ✅ |
| W1N-147 | F SECURITY.md + SAS/fingerprint guide (no SSSS for cross-signing) | §1.7 | ✅ |
| W1N-149 | Security hardening umbrella (P0 auto-confirm / P0 admin-only / P1 fingerprint gate) | §5 | ✅ |
| W1N-150 | B admin-only enforcement | §3.1 / §5-12 | ✅ |
| W1N-151 | C verify_device_by_fingerprint (ed25519 exact gate) | §3.3 | ✅ |
| W1N-153 | A remove same-account SAS auto-confirm | §5-10 | ✅ |
| W1N-154 | E docs switched to manual confirm + new fingerprint service names | — | ✅ |
| W1N-156 | Hardening follow-up: allowlist split (P1-2 done, P2 intentionally deferred) | §5 | ✅ |
| W1N-157 | F plain-language verification guide (docs/DEVICE_VERIFICATION.md) | — | ✅ |
| W1N-158 | M5 Config Flow (motivation for reauth / device-verification UI path) | — | ✅ |
| W1N-159 | casefold fingerprint compare bug → exact match | §5-11 | ✅ |
| W1N-162 | Config Flow reconfigure + reauth | — | ✅ |
| W1N-165 | Docs update + release 0.2.0 (SAS stays service/event-based) | §8 | ✅ |
| W1N-166 | keys_query skipped same account → warm own device keys | §2-7 / §5-9 | ✅ |
| W1N-169 | key/mac double-send (protocol correctness) | §5-3 | ✅ |
| W1N-170 | First start for a device added after boot was dropped → auto query keys and re-feed | §2-8 / §5-8 | ✅ |
| W1N-171 | Missing real-homeserver end-to-end SAS test | §5-16 | ⏳ |
| W1N-172 | nio 60s always-timeout (`_last_event_time` never refreshed) | §2-2 / §5-6 | ✅ |
| W1N-173 | request → ready bridge (nio missing the framework) | §2-1 / §5-13 | ✅ |
| W1N-174/176 | Deploy matrix_e2ee(e) to hass.windy.lan | — | ✅ |
| W1N-175 | emoji double conversion (vodozemac indices) | §2-4 | ✅ |
| W1N-177 | legacy MAC invalid-base64 | §2-5/6 / §5-1/2 | ✅ |
| W1N-178 | Deploy v0.2.10 (includes legacy MAC fix) | — | ✅ |
| W1N-179 | receive_mac_event missing return overwrote canceled | §2-9 / §5-17 | ✅ |
| W1N-180 | Options Flow device-verification wizard (bot/peer-initiated, live SAS emoji UI) | §3 | ✅ |
| W1N-182 | peer-initiated wizard cancelled before emoji arrived | §3 | ✅ |
| W1N-183 | After MAC on the request flow, send extra `m.key.verification.done` | §2-10 / §3.2 | ✅ |

---

## 7. Current version status and remaining work

- Released through **v0.3.10** (wire-fix line: commitment v0.2.8 → emoji v0.2.9 → legacy MAC v0.2.10; v0.3 adds the Options Flow
  device-verification wizard + `m.key.verification.done` finish; v0.3.4–v0.3.7 add docs, log noise reduction, Config Entry diagnostics,
  and a connection-health entity; v0.3.8 splits the allowlist; v0.3.9 UI polish + maintenance; v0.3.10 bilingual EN/ZH docs).
- SAS currently has two paths: **Options Flow → Verify device wizard** (v0.3, recommended) + service/event-based (`start_verification` /
  `confirm_verification` / `cancel_verification`).
- **Allowlist split (v0.3.8)**: `allowed_users` only gates “command permission”; `verification_peer_users` only gates “SAS initiate permission”;
  neither implies the other (W1N-156 P1-2).

**Intentionally deferred (P2 quality, not a security blocker)**:
- Device-level allowlist (`inbound_peer_devices`): SAS already checks the specific device via emoji + `confirm_verification`; limited value.
- SAS does not send an explicit cancel for unsupported algorithms: silently ignoring is safe today.
- setup/stop/reauth async lock: no observed race; low value.
- Transaction binding (expected user/device/created-at): already covered by nio `key_verifications` + integration timeout.
- CI manifest dependency check: HA already validates manifest requirements at install time.
- ruff / static checks: **already configured** (`ruff.toml` + CI lint job, W1N-198).

**Backlog (avoid duplicate work; check this 1 item before starting)**:
1. **W1N-171** — real-homeserver end-to-end SAS test (docker Synapse or a manual runbook).
