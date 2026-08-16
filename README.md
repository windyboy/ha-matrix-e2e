# matrix_e2ee

Home Assistant **custom** integration that runs a dedicated Matrix bot with a persistent end-to-end encryption (E2EE) device identity.

- Unique domain: `matrix_e2ee`
- Does **not** override Home Assistant’s built-in `matrix` integration
- Python, `matrix-nio[e2e]==0.26.0` with its E2EE extras (`vodozemac`, `peewee`, `cachetools`, `atomicwrites`) declared explicitly in `manifest.json`
- YAML setup only (no Config Flow, not in HACS)

## Installation

This is a **manual** custom integration. There is no HACS listing and no Config Flow.

Use a dedicated **non-admin** Matrix bot account. Do not run Home Assistant’s built-in `matrix` integration and `matrix_e2ee` on the same bot account. Read the [security warning](#security-warning) before enabling it.

`<config>` is the Home Assistant configuration directory (`/config` on Home Assistant OS / Container).

### 1. Copy the integration

Copy only the `matrix_e2ee` package into `custom_components`. The GitHub repository root is not that folder.

**From a release archive** (recommended):

1. Download the source zip of the latest release: https://github.com/windyboy/ha-matrix-e2e/releases
2. Extract it, then copy `custom_components/matrix_e2ee` to the HA config directory:

```bash
mkdir -p /config/custom_components
cp -a custom_components/matrix_e2ee /config/custom_components/matrix_e2ee
```

**From git** (replace `v0.1.1` with the [release tag](https://github.com/windyboy/ha-matrix-e2e/releases) you want):

```bash
git clone --depth 1 --branch v0.1.1 https://github.com/windyboy/ha-matrix-e2e.git /tmp/ha-matrix-e2e
mkdir -p /config/custom_components
cp -a /tmp/ha-matrix-e2e/custom_components/matrix_e2ee /config/custom_components/matrix_e2ee
```

The installed tree must be:

```text
<config>/custom_components/matrix_e2ee/manifest.json
<config>/custom_components/matrix_e2ee/__init__.py
<config>/custom_components/matrix_e2ee/client.py
<config>/custom_components/matrix_e2ee/const.py
<config>/custom_components/matrix_e2ee/storage.py
<config>/custom_components/matrix_e2ee/services.yaml
```

### 2. Add YAML and the bot password

Put the password in `secrets.yaml`, not in git:

```yaml
# secrets.yaml
matrix_e2ee_password: "your-bot-password"
```

Then add the block in `configuration.yaml` (see [Configuration](#configuration-yaml)). Password is required only on first login, when no session file exists yet.

### 3. Restart Home Assistant

1. Developer Tools → YAML → Check configuration.
2. Restart Home Assistant.
3. On first load, Home Assistant installs `matrix-nio[e2e]==0.26.0` and its explicit E2EE dependencies (`vodozemac`, `peewee`, `cachetools`, `atomicwrites`) from `manifest.json`. The E2EE deps are listed explicitly because Home Assistant's requirement manager drops the `[e2e]` extra and would otherwise skip them. If any requirement fails to install, setup fails closed. Do not work around it with OS-level `pip` on Home Assistant OS.

After a successful first start, HA writes:

- `<config>/.storage/matrix_e2ee_session.json`
- `<config>/.storage/matrix_e2ee_store/`

Those files must stay on the same persistent volume as Home Assistant. They are gitignored and must never be committed.

If setup fails, check `matrix_e2ee_error` events and the Home Assistant log (tokens, pickle keys, and passwords must not appear there). Soft logout recovery is in the [runbook](#recovery-runbook).

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

## Services and events (M1–M5)

- Service: `matrix_e2ee.send_message` (`message`, `room_id`)
- Service: `matrix_e2ee.reauthenticate` (`password`) — after a **soft logout** only; replaces the access token, keeps `device_id` and the crypto store
- Services: `matrix_e2ee.start_verification` (`user_id`, `device_id`), `confirm_verification` (`transaction_id`), `cancel_verification` (`transaction_id`)
- Services: `matrix_e2ee.get_fingerprint` (no fields), `matrix_e2ee.verify_device` (`user_id`, `device_id`)
- Event: `matrix_e2ee_command` (`room_id`, `sender`, `command`, `args` only — never the raw body)
- Event: `matrix_e2ee_error` (error codes, no secrets)
- Event: `matrix_e2ee_verification` (`stage`, `transaction_id`, `user_id`, `device_id`, optional `emojis`, optional `expires_at`)
- Event: `matrix_e2ee_fingerprint` (`user_id`, `device_id`, `ed25519`, `curve25519` — public keys only)

`start_verification`, `confirm_verification`, `cancel_verification`, `verify_device`, and `reauthenticate` are admin-only actions; do not expose them to non-admin users or automations.

SAS emoji comparison happens in Developer Tools via `matrix_e2ee_verification` (`stage: sas`). Confirming is the only step that marks a device verified. Accepting an inbound SAS start is protocol continuation, not trust.

Commands fire Home Assistant events only. This integration never calls `domain.service` itself. Map commands in automations.

`notify.matrix_e2ee` is deferred (no Linear ticket). There is no `matrix_e2ee_message` event.

## Device verification

Two paths are supported; see [SECURITY.md](SECURITY.md) for the trust model.

### 1. SAS auto-completion (mutual, recommended)

1. Log in to the bot account in Element (this becomes the bootstrap/admin device) and bootstrap its cross-signing identity.
2. Start the server bot — it creates its device and uploads device keys.
3. In Element, open the bot account's Sessions and verify the server bot device.
4. The integration auto-completes the SAS handshake when the initiator is the bot's own account; confirm the emoji match in Element.

Only the bot's own account or users in `allowed_users` may initiate SAS (`verification_peer_denied` otherwise).

### 2. One-sided fingerprint (fallback)

1. Call `matrix_e2ee.get_fingerprint` (or read `matrix_e2ee_fingerprint`) for the bot's `ed25519` device key.
2. In Element, open the bot user's sessions and use "Manually verify by text".
3. Compare the session key with the fingerprint. This trusts the bot from Element's side only; call `matrix_e2ee.verify_device` to trust a device from the bot's side.

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
| **M1** | Independent YAML integration, unencrypted-room send/commands, allowlist, startup/shutdown, mock tests | Released ([#2](https://github.com/windyboy/ha-matrix-e2e/pull/2)) |
| **M2** | E2EE lifecycle: first login writes a full crypto device, restart restores the same device, encrypted text path, fail-closed unverified send/commands | Released ([#4](https://github.com/windyboy/ha-matrix-e2e/pull/4)) |
| **M3** | SAS services/events (`start_verification`, `confirm_verification`, `cancel_verification`) so encrypted send/commands can succeed with verified devices | Released ([#5](https://github.com/windyboy/ha-matrix-e2e/pull/5)) |
| **M4** | Soft logout / `reauthenticate`, store-loss runbook, diagnostics | Released ([#6](https://github.com/windyboy/ha-matrix-e2e/pull/6)) |

M1 acceptance uses unencrypted test rooms. The first successful login still creates a full E2EE-capable Matrix device so M2 does not “upgrade” a non-crypto device.

M3 adds SAS so an already-known device can become `verified=True`. Tests mock nio; they do not use a live Element session. Do not claim production E2EE support until a real Element/homeserver SAS has been confirmed on a deployment.

## License

Apache License 2.0. Copyright 2026 windyboy.
