# matrix_e2ee

Home Assistant **custom** integration that runs a dedicated Matrix bot with a persistent end-to-end encryption (E2EE) device identity.

- Unique domain: `matrix_e2ee`
- Does **not** override Home Assistant’s built-in `matrix` integration
- Python, `matrix-nio[e2e]==0.26.0`
- YAML setup only in M1–M4 (no Config Flow)

Install by copying `custom_components/matrix_e2ee` into `<config>/custom_components/matrix_e2ee`.

## Security warning

**Home Assistant is an E2EE endpoint.** If this integration is enabled, this host decrypts Matrix text and holds device keys. Compromise of the Home Assistant host, backups, logs, or the crypto store is compromise of the bot device.

Protect, back up, and revoke together:

- `.storage/matrix_e2ee_session.json` (`user_id`, `device_id`, `access_token`, `pickle_key`)
- `.storage/matrix_e2ee_store/` (Olm/Megolm, device trust, sync token)

Rules that this project will not violate:

- Dedicated **non-admin** Matrix bot account only
- Never use Synapse admin login tokens for E2EE
- Never set `ignore_unverified_devices=True` by default
- Never auto-trust unknown devices
- Never fall back to plaintext
- Never process commands from unverified devices
- Never log access tokens, pickle keys, message bodies, or crypto-store secrets
- Crypto store loss is a **new device**. Old history is not recoverable
- Do not run built-in `matrix` and `matrix_e2ee` on the same bot account

Do not claim production E2EE support until a real Element/homeserver SAS has been confirmed on a deployment.

## Configuration (YAML)

Password is required only when no session exists. After first successful login, the session is written atomically; later starts restore the same Matrix device.

```yaml
matrix_e2ee:
  homeserver: https://matrix.example.org
  username: "@ha-bot:example.org"
  password: !secret matrix_e2ee_password
  allowed_rooms:
    - "!roomid:example.org"
  allowed_users:
    - "@admin:example.org"
  command_prefix: "!"
```

Empty `allowed_rooms`: no send, no inbound commands. Empty `allowed_users`: no inbound commands; send to allowed rooms is still permitted.

## Services and events (M1–M4)

- Service: `matrix_e2ee.send_message` (`message`, `room_id`)
- Service: `matrix_e2ee.reauthenticate` (`password`) — after a **soft logout** only; replaces the access token, keeps `device_id` and the crypto store
- Event: `matrix_e2ee_command` (`room_id`, `sender`, `command`, `args` only — never the raw body)
- Event: `matrix_e2ee_error` (error codes, no secrets)
- Event: `matrix_e2ee_verification` (`stage`, `transaction_id`, `user_id`, `device_id`, optional `emojis`)
- Services: `matrix_e2ee.start_verification` (`user_id`, `device_id`), `confirm_verification` (`transaction_id`), `cancel_verification` (`transaction_id`)

SAS emoji comparison happens in Developer Tools via `matrix_e2ee_verification` (`stage: sas`). Confirming is the only step that marks a device verified. Accepting an inbound SAS start is protocol continuation, not trust.

Commands fire Home Assistant events only. This integration never calls `domain.service` itself. Map commands in automations.

`notify.matrix_e2ee` is deferred (no Linear ticket). There is no `matrix_e2ee_message` event.

## Recovery runbook

Session JSON and the crypto store stay on the Home Assistant host. They are gitignored. This is a public repository: never commit tokens, pickle keys, passwords, or store files.

Safe diagnostics (no token, pickle key, password, or message body) are the fields below. There is no Config Entry diagnostics platform in v1.

- `user_id`
- `device_id`
- `session_present`
- `store_present`
- `soft_logged_out`
- `encryption_enabled` (always true for this integration)
- `store_sync_tokens` (always true for this integration)

### Soft logout (`matrix_e2ee_error` code `soft_logout`)

The access token is invalid, but the homeserver still allows the same device to sign in again. The integration **keeps** `.storage/matrix_e2ee_store/` and the existing `device_id`. Setup still loads the client and registers services (including `reauthenticate`). Send, inbound commands, SAS, and sync stay blocked until reauthentication succeeds.

1. Call `matrix_e2ee.reauthenticate` with the bot account password (Developer Tools → Services, or an automation). Provide the password only to this service.
2. On success the session file is rewritten with a **new access token only**. `device_id` and `pickle_key` are unchanged. The crypto store is reused.
3. If the homeserver would return a different `device_id`, the new token is **not** written (`device_mismatch`). The old session remains. This is not a new-device upgrade path.

The password never appears in events, log lines, or service return values.

### Hard logout (`hard_logout`)

The token is invalid and this is **not** a soft logout. Setup **fails**. Do **not** reuse the old crypto store.

1. Revoke the old device on the homeserver if you still can.
2. Delete `<config>/.storage/matrix_e2ee_session.json` **and** `<config>/.storage/matrix_e2ee_store/`.
3. Restart Home Assistant with `password` in YAML so first login creates a **new** device.
4. Run SAS again (`start_verification` / `confirm_verification`). Old history cannot be decrypted.

### Crypto store missing (`store_missing`)

Treat this as a new device. The session JSON is not enough to recover Megolm history. Delete the leftover session file, then follow the hard-logout steps (new login + SAS). Do not copy an old store onto a new device.

### Leaked keys or stolen host

1. Revoke the old Matrix device on the homeserver.
2. Destroy the local session file and crypto store (same paths as above).
3. First login creates a new device.
4. SAS-verify devices that should be trusted. Previous ciphertext is not recoverable.

### Short-lived / refresh tokens (`refresh_token_unsupported`)

v1 does not rotate refresh tokens. If login or `reauthenticate` would receive a refresh token or a short-lived access token (`expires_in_ms`), the integration refuses to persist the session. Use a long-lived access token (standard password login without token refresh).

## Roadmap

| Milestone | Intent | Status |
|---|---|---|
| **M1** | Independent YAML integration, unencrypted-room send/commands, allowlist, startup/shutdown, mock tests | Merged (#2) |
| **M2** | E2EE lifecycle: first login writes a full crypto device, restart restores the same device, encrypted text path, fail-closed unverified send/commands | Onto main (#4) |
| **M3** | SAS services/events (`start_verification`, `confirm_verification`, `cancel_verification`) so encrypted send/commands can succeed with verified devices | Onto M2 (#5) |
| **M4** | Soft logout / `reauthenticate`, store-loss runbook, diagnostics | This delivery |

M1 acceptance uses unencrypted test rooms. The first successful login still creates a full E2EE-capable Matrix device so M2 does not “upgrade” a non-crypto device.

M3 adds SAS so an already-known device can become `verified=True`. Tests mock nio; they do not use a live Element session. Do not claim production E2EE support until a real Element/homeserver SAS has been confirmed on a deployment.

## License

Apache License 2.0. Copyright 2026 windyboy.
