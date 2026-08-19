# Device verification: step-by-step guide

> Language: [English](DEVICE_VERIFICATION.md) | [中文](DEVICE_VERIFICATION.zh.md)

In one sentence: verification confirms that **the bot device inside Home Assistant is actually yours**, so nobody who steals the bot password can impersonate it with another device.

## What you need

- A Matrix client **Element** (phone or desktop)
- The password for the bot account (for example `@ha-bot:example.org`)

## The idea

The bot account has two kinds of “keys”:

- **Cross-signing identity**: lives only in Element; it represents “who owns this account”.
- **Device keys**: the key pair belonging to the Home Assistant bot device `BOT_SERVER_01`.

> Note: `BOT_SERVER_01` is only an example identifier in this document. In a real deployment the bot device **display name** is `Home Assistant matrix_e2ee` (`DEVICE_NAME` in `const.py`). The device ID is assigned randomly by the server; look it up in Element’s session list by that display name.

Verification = Element confirming that the `BOT_SERVER_01` key is trusted. Confirmation is a string of emoji (like pairing a Bluetooth device).

## Path 1: HA UI wizard (v0.3+, recommended)

Compare and confirm emoji from the Home Assistant UI. You do not need to call services by hand in Developer Tools. **Element still starts the verification** (same as official clients); the wizard waits and then takes over the comparison:

1. Settings → Devices & Services → Matrix E2EE → Configure (Options).
2. Choose “Verify device”.
3. The wizard enters a waiting page and tells you to start verification in Element: **Element → Settings → Sessions → find `Home Assistant matrix_e2ee` → Verify**.
4. Accept the verification request in Element; both sides enter emoji comparison.
5. Back in HA, the wizard has already detected the verification and shows a string of emoji.
   - Both sides match → choose “They match”.
   - They do not match → choose “They do not match”, and check whether the account has been stolen.
6. Wait for both MACs to complete. The UI says “Device verification complete”, and in Element the device becomes a green “Verified”.

> Note: SAS verification is mutual — one successful verification makes Element trust the bot device **and** makes the bot trust this Element device. The wizard does not need to know your device in advance, and it does not start the flow; it only waits for Element to start verification and then takes over comparison.

## Path 2: Element starts verification

## Step 0 (once in the account’s lifetime): bootstrap the account identity

1. Log in to Element with the bot account.
2. On first login Element prompts you to set a security key / recovery — follow it. That creates the account’s cross-signing identity.
3. Write down and save the recovery key (important).

## Step 1: start Home Assistant

1. Install `matrix_e2ee` and add it in the UI (Settings → Devices & Services → Add Integration → Matrix E2EE).
2. Once add succeeds, you are done. The bot device `BOT_SERVER_01` is online and has uploaded its device keys.

## Step 2: start verification in Element

1. Element → Settings → Security & Privacy → Sessions / Devices.
2. Find `BOT_SERVER_01` (usually an unverified grey shield).
3. Tap it → Verify.

The whole flow is **started by Element**. The HA side does not need `matrix_e2ee.start_verification`.

## Step 3: compare emoji

1. Element shows a string of emoji (for example 🐶 🌙 🔑).
2. In Home Assistant → Developer Tools → Events, listen for `matrix_e2ee_verification`. You will see `stage: started` (verification began), then `stage: sas` (comparison). The `emojis` field is what HA shows on this side.
3. **Compare the emoji on both sides one by one. They must match exactly.**

## Step 4: confirm

- Both sides match → Developer Tools → Services, call `matrix_e2ee.confirm_verification` with `transaction_id` (the one from the `matrix_e2ee_verification` event).
- They do not match → call `matrix_e2ee.cancel_verification`, and check whether the account has been stolen.

## Step 5: see Verified

After confirm, `BOT_SERVER_01` in Element becomes a green “Verified”. HA also fires a `stage: done` event.

## Does this survive a restart?

Yes. Restarting HA restores the **same** device `BOT_SERVER_01`, and the verification state is kept. If the device ID changes after a restart, the crypto store was lost and you must re-verify using the runbook.

## Fallback: trust by fingerprint (not recommended unless SAS is impossible)

This is “one-sided local trust”, not full SAS:

1. HA Developer Tools → service `matrix_e2ee.get_fingerprint`, or read the `matrix_e2ee_fingerprint` event, to get the bot’s `ed25519` fingerprint.
2. In Element, use “Manually verify” on the bot session and compare fingerprints.
3. To trust a device from the bot’s side: call `matrix_e2ee.verify_device_by_fingerprint` with `user_id`, `device_id`, and that device’s `ed25519`. **It only takes effect on an exact match**; a mismatch raises `fingerprint_mismatch`.

Note: this path does not go through emoji comparison. Safety depends on you comparing fingerprints yourself over a trusted channel.

## If something goes wrong

If an event arrives with `stage: canceled`: this verification is dead (the transaction is invalid). **Do not** call `confirm_verification`. Go back to step 2 and start again from Element.

| Symptom | Meaning | What to do |
|---|---|---|
| `verification_peer_denied` | The initiator is not the bot itself or `verification_peer_users` | Check who started it |
| `verification_timeout` | No confirm within 4 minutes | Start a new verification |
| `fingerprint_mismatch` | Fingerprints do not match | Stop, re-check the fingerprint, suspect a MITM |
| `invalid_transaction` | Wrong `transaction_id` | Copy it from the latest `matrix_e2ee_verification` event |

## One-sentence summary

Every device — **including another device of the bot’s own account** — is trusted only after “look at the emoji → you confirm”. There is no auto-trust.

---

## Appendix: SAS verification flow (source-level reference)

Technical reference for maintainers / reviewers. Event order follows the Matrix spec `m.key.verification.*` (to-device).

### Key source locations

**matrix-nio 0.26.0** (`matrix-nio` package):

| File | Symbol | Role |
|---|---|---|
| `nio/crypto/olm_machine.py` | `Olm.handle_key_verification()` | SAS state machine: consumes `KeyVerificationStart/Accept/Key/Mac/Cancel`, creates `Sas`, auto-shares key, verifies MAC, `verify_device()` |
| `nio/crypto/sas.py` | `Sas` (`SasState`) | State machine `created → started → accepted → key_received → mac_received → canceled`; `share_key()` / `get_mac()` / `accept_verification()` / `receive_key_event()` / `receive_mac_event()` / `accept_sas()`; `verified == (state == mac_received and sas_accepted)` |
| `nio/client/async_client.py` | `start_key_verification()` / `accept_key_verification()` / `confirm_short_auth_string()` / `cancel_key_verification()` | Application-facing API; internally `to_device()` sends immediately |
| `nio/client/async_client.py` | `sync_forever()` → `send_to_device_messages()` | Periodically drains the `outgoing_to_device_messages` queue |

**This integration** (`custom_components/matrix_e2ee/client.py`; SAS patches in `nio_compat.py`):

| Symbol | Role |
|---|---|
| `_query_device_keys()` | Prefetches the given account’s device keys into `device_store` (`_query_own_device_keys()` is the special case that queries the bot’s own account after login/restore) |
| `_repair_dropped_start()` | If a `start` arrived but nio dropped it because the device was unknown: query the sender’s keys, then re-feed the same `start` to `nio.olm.handle_key_verification()` |
| `apply_nio_compat_patches()` | Entry in `nio_compat.py`, called from `client.py` `_make_nio()`; includes `_patch_nio_sas_timeout()` and three other SAS monkeypatches (timeout / commitment / emoji / mac). `_patch_nio_sas_timeout()` ignores nio’s broken 60s event timeout (`_last_event_time` is never refreshed) and keeps only the 5 min overall timeout |
| `enable_verification_callbacks()` | Registers `handle_to_device_event` on `add_to_device_callback` |
| `handle_to_device_event()` | Integration callback for `start` / `key` / `mac` / `cancel` (**does not handle `accept`**) |
| `_handle_verification_request()` / `_send_verification_ready()` | Bridges `m.key.verification.request → ready` (missing in nio 0.26); validates sender / txn / methods / timestamp, then replies `ready` |
| `_send_verification_done()` | Sends the extra `m.key.verification.done` (nio 0.26 has no done support) so Element leaves `WaitingForDone` for `Done` |
| `async_start_verification()` / `async_confirm_verification()` / `async_cancel_verification()` | The three HA service entry points |

### Direction A: Element initiates (peer-initiated; bot is responder, `we_started_it=False`)

> The full flow starts with `request → ready` (bridged by the integration’s `_handle_verification_request()`). The table below starts at the SAS `start`.

| # | Event | Who handles it | Action |
|---|---|---|---|
| 1 | `start` (Element → bot) | nio `handle_key_verification` | Looks up `device_store` (warmed by `_query_device_keys`); on hit, `Sas.from_key_verification_start` builds `Sas(we_started_it=False, state=started)` and registers `key_verifications[txn]`; **on miss, drops the start and adds `users_for_key_query`** |
| 2 | — | integration `handle_to_device_event("start")` | If nio already dropped the start (unknown device), `_repair_dropped_start()` queries keys and re-feeds start so nio builds SAS; then `accept_key_verification()` sends `accept` |
| 3 | `key` (Element → bot) | nio `handle_key_verification` | `receive_key_event()` establishes the shared secret; `not we_started_it` → auto `share_key()` enqueues |
| 4 | — | `sync_forever` | `send_to_device_messages()` sends the bot’s `key` |
| 5 | — | integration `handle_to_device_event("key")` | Fires `stage: sas` (with emoji) |
| 6 | User calls `confirm_verification` | integration `async_confirm_verification` | `confirm_short_auth_string()` → `accept_sas()` + `get_mac()`, sends the bot’s `mac` |
| 7 | `mac` (Element → bot) | nio `handle_key_verification` | `receive_mac_event()` verifies; `verified` → `verify_device()` |
| 8 | — | integration `handle_to_device_event("mac")` | If `verified`, send extra `m.key.verification.done` (to-device) + fire `stage: done` |
| 9 | `done` (bot → Element) | Element | Receiving `done` moves `WaitingForDone` → `Done`; verification complete |

### Direction B: bot initiates (`we_started_it=True`)

| # | Event | Who handles it | Action |
|---|---|---|---|
| 1 | User calls `start_verification` | integration `async_start_verification` | `start_key_verification()` builds `Sas(we_started_it=True, state=created)` and sends `start` |
| 2 | `accept` (Element → bot) | nio `handle_key_verification` | `receive_accept_event()` → auto `share_key()` enqueues (initiator sends key first) |
| 3 | `key` (Element → bot) | nio `handle_key_verification` | `receive_key_event()`; `we_started_it` is true → no further share |
| 4 | — | integration `handle_to_device_event("key")` | Fires `stage: sas` |
| 5 | User calls `confirm_verification` | integration `async_confirm_verification` | `confirm_short_auth_string()` sends `mac` |
| 6 | `mac` (Element → bot) | nio `handle_key_verification` | `receive_mac_event()` → `verify_device()` |
| 7 | — | integration `handle_to_device_event("mac")` | `verified` → send extra `m.key.verification.done` + fire `stage: done` (on the start flow Element safely ignores done) |

### Key facts

- **request/ready is bridged by the integration**: matrix-nio 0.26.0 has no `m.key.verification.request` / `ready` handling (request is parsed as `UnknownToDeviceEvent` and dropped). Modern Element uses `request → ready → start …`. `_handle_verification_request()` validates the request and replies `ready`; nio’s SAS state machine then takes over at `start`.
- **The integration does not handle `accept`**: `_verification_kind()` only maps `start/key/mac/cancel`. `accept` is handled by nio’s internal state machine (direction B #2).
- **Sending `key` is nio’s job**: in both directions the bot’s `key` is enqueued by nio’s internal `share_key()` and sent by `sync_forever`. The integration **must not** call `share_key` itself.
- **`mac` is sent once, only by the `confirm_verification` service**: `confirm_short_auth_string()` already does `accept_sas()` + `get_mac()`.
- **`done` is sent by the integration when `sas.verified` becomes True**: matrix-nio 0.26.0 has no `m.key.verification.done` support. On the request flow, after MAC exchange Element enters `WaitingForDone` waiting for HA’s `done`. `_send_verification_done()` sends it unconditionally from two places (`async_confirm_verification` verified branch + `handle_to_device_event` mac branch). On the start flow Element’s `Done` state has no `done` transition and safely ignores it.
- **Known issues (see the matching tickets)**:
  - **W1N-169**: ~~The current implementation sent key and mac twice each (direction A: integration called `share_key` by hand; the `mac` handler’s `_try_confirm` sent mac again).~~ Fixed: key is sent by nio’s internal state machine when the peer key arrives; mac is sent only by `confirm_verification`.
  - **W1N-170**: ~~A device added only after the bot came online had its first SAS `start` dropped by nio (“unknown device”); you had to retry.~~ Fixed: `_repair_dropped_start()` queries keys and re-feeds `start` for an unknown device; no manual retry.
  - **W1N-172**: ~~nio 0.26.0 never refreshes `Sas._last_event_time`, so SAS always timed out 60s after creation (even while a human was still comparing emoji).~~ Fixed: `_patch_nio_sas_timeout()` monkeypatches `timed_out` to ignore the broken 60s event timeout and keep only the 5 min overall timeout; the integration’s own timeout is aligned at 4 min.
  - **W1N-173**: ~~matrix-nio 0.26.0 lacked the `request`/`ready` handshake, so Element’s verification request was dropped at step 1 and the only thing received was `cancel`.~~ Fixed (P0): `_handle_verification_request()` validates the request and replies `ready`, so Element continues with `start`.
  - **W1N-183**: ~~matrix-nio 0.26.0 has no `m.key.verification.done` support; after MAC exchange on the request flow Element stuck in `WaitingForDone` waiting for HA’s `done` until timeout.~~ Fixed: `_send_verification_done()` sends `done` when `sas.verified` becomes True, so Element finishes the last step.
  - **W1N-171**: SAS has never been end-to-end tested against a real Matrix homeserver (existing tests all use FakeNio). Deferred.
