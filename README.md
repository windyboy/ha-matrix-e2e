# matrix_e2ee

Home Assistant **custom** integration that runs a dedicated Matrix bot with a persistent end-to-end encryption (E2EE) device identity.

- Unique domain: `matrix_e2ee`
- Does **not** override Home Assistant’s built-in `matrix` integration
- Python, `matrix-nio[e2e]==0.26.0` with its E2EE extras (`vodozemac`, `peewee`, `cachetools`, `atomicwrites`) declared explicitly in `manifest.json`
- Config Flow (UI) setup; not in HACS

## Installation

This is a **manual** custom integration (not in HACS). It is configured through Home Assistant's Config Flow (UI), not YAML.

Use a dedicated **non-admin** Matrix bot account. Do not run Home Assistant’s built-in `matrix` integration and `matrix_e2ee` on the same bot account. Read the [security warning](#security-warning) before enabling it.

`<config>` is the Home Assistant configuration directory (`/config` on Home Assistant OS / Container).

### 1. Copy the integration

Copy only the `matrix_e2ee` package into `custom_components`. The GitHub repository root is not that folder.

**From a release archive** (recommended):

1. Download the source zip of the latest release: https://github.com/windyboy/ha-matrix-e2ee/releases
2. Extract it, then copy `custom_components/matrix_e2ee` to the HA config directory:

```bash
mkdir -p /config/custom_components
cp -a custom_components/matrix_e2ee /config/custom_components/matrix_e2ee
```

**From git** (replace `v0.3.10` with the [release tag](https://github.com/windyboy/ha-matrix-e2ee/releases) you want):

```bash
git clone --depth 1 --branch v0.3.10 https://github.com/windyboy/ha-matrix-e2ee.git /tmp/ha-matrix-e2ee
mkdir -p /config/custom_components
cp -a /tmp/ha-matrix-e2ee/custom_components/matrix_e2ee /config/custom_components/matrix_e2ee
```

The installed tree must be:

```text
<config>/custom_components/matrix_e2ee/manifest.json
<config>/custom_components/matrix_e2ee/__init__.py
<config>/custom_components/matrix_e2ee/binary_sensor.py
<config>/custom_components/matrix_e2ee/client.py
<config>/custom_components/matrix_e2ee/config_flow.py
<config>/custom_components/matrix_e2ee/const.py
<config>/custom_components/matrix_e2ee/diagnostics.py
<config>/custom_components/matrix_e2ee/nio_compat.py
<config>/custom_components/matrix_e2ee/storage.py
<config>/custom_components/matrix_e2ee/url.py
<config>/custom_components/matrix_e2ee/services.yaml
<config>/custom_components/matrix_e2ee/strings.json
<config>/custom_components/matrix_e2ee/translations/en.json
<config>/custom_components/matrix_e2ee/translations/zh-Hans.json
<config>/custom_components/matrix_e2ee/brand/icon.png
<config>/custom_components/matrix_e2ee/brand/icon@2x.png
<config>/custom_components/matrix_e2ee/brand/logo.png
<config>/custom_components/matrix_e2ee/brand/logo@2x.png
```

### 2. Add the integration in the UI

1. Restart Home Assistant once after copying the files (so the integration is discovered).
2. **Settings → Devices & Services → Add Integration → Matrix E2EE**.
3. Enter the **homeserver URL**, the bot **username**, and the **password**. The password is used only to log in and test the connection; it is never stored.
4. On first login the integration creates the bot device and writes its crypto store. Later restarts restore the same device without any password.

The password is required only on the very first login, when no session file exists yet. It is not saved to the config entry.

**One bot per Home Assistant.** This integration supports a single config entry (`"single_config_entry": true`). The session file and crypto store are global to the integration, so a second entry would silently rebind them to a different account. Adding the integration a second time — even with a different username — is rejected with "Already configured".

### 3. Migrating from the old YAML setup

If a `matrix_e2ee:` block is still present in `configuration.yaml`, Home Assistant imports it into a config entry on startup. The existing `.storage/matrix_e2ee_session.json` and `.storage/matrix_e2ee_store/` are reused — the device is **not** recreated. Once the import has created the entry, you can remove the YAML block.

### 4. Dependencies

On first load, Home Assistant installs `matrix-nio[e2e]==0.26.0` and its explicit E2EE dependencies (`vodozemac`, `peewee`, `cachetools`, `atomicwrites`) from `manifest.json`. The E2EE deps are listed explicitly because Home Assistant's requirement manager drops the `[e2e]` extra and would otherwise skip them. If any requirement fails to install, setup fails closed. Do not work around it with OS-level `pip` on Home Assistant OS.

After a successful first setup, HA writes:

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
- Never fall back to plaintext when an encrypted send fails (unverified/unknown devices block the send; it is never downgraded to plaintext). Unencrypted rooms on the allowlist are still sent unencrypted.
- Never process commands from unverified devices
- Never log access tokens, pickle keys, message bodies, or crypto-store secrets
- Crypto store loss is a **new device**. Old history is not recoverable
- Do not run built-in `matrix` and `matrix_e2ee` on the same bot account

Device verification has been manually confirmed on a real deployment (Element SAS with the `m.key.verification.done` handshake). An automated end-to-end SAS test against a real homeserver is still backlog (W1N-171).

## Configuration (UI)

All configuration is done through the Config Flow:

- **homeserver** and **username** are entered when the integration is added. The username is read-only afterwards; the homeserver can be changed via **Reconfigure**. Changing to a **different server origin** (scheme, host, or port) quarantines the old session file and crypto store and logs in as a fresh device — the old token is never sent to the new origin, and you must re-enter the bot password and re-run device verification. Changing only the trailing slash or path keeps the same device.
- **allowed_rooms**, **allowed_users**, **verification_peer_users**, and **command_prefix** are edited via **Configure** (Options). Changing them reloads the integration and reuses the existing crypto store.

Empty `allowed_rooms`: no send, no inbound commands. Empty `allowed_users`: no inbound commands; send to allowed rooms is still permitted. Empty `verification_peer_users`: no inbound SAS (only the bot's own account may initiate it).

The old YAML block is no longer required. If a `matrix_e2ee:` block remains in `configuration.yaml`, it is imported into a config entry on startup (see [Migrating from the old YAML setup](#3-migrating-from-the-old-yaml-setup)).

## Services and events (M1–M5)

- Service: `matrix_e2ee.send_message` (`message`, `room_id`)
- Service: `matrix_e2ee.reauthenticate` (`password`) — after a **soft logout** only; replaces the access token, keeps `device_id` and the crypto store
- Services: `matrix_e2ee.start_verification` (`user_id`, `device_id`), `confirm_verification` (`transaction_id`), `cancel_verification` (`transaction_id`)
- Services: `matrix_e2ee.get_fingerprint` (no fields), `matrix_e2ee.verify_device_by_fingerprint` (`user_id`, `device_id`, `ed25519`)
- Event: `matrix_e2ee_command` (`room_id`, `sender`, `command`, `args` only — never the raw body)
- Event: `matrix_e2ee_error` (error codes, no secrets)
- Event: `matrix_e2ee_verification` (`stage`, `transaction_id`, `user_id`, `device_id`, optional `emojis`, optional `expires_at`)
- Event: `matrix_e2ee_fingerprint` (`user_id`, `device_id`, `ed25519`, `curve25519` — public keys only)

`start_verification`, `confirm_verification`, `cancel_verification`, `verify_device_by_fingerprint`, and `reauthenticate` are admin-only and enforced by Home Assistant's admin-service helper; non-admin users cannot call them.

SAS emoji comparison happens in Developer Tools via `matrix_e2ee_verification` (`stage: sas`). Confirming is the only step that marks a device verified. Accepting an inbound SAS start is protocol continuation, not trust.

Commands fire Home Assistant events only. This integration never calls `domain.service` itself. Map commands in automations.

`notify.matrix_e2ee` is deferred (no Linear ticket). There is no `matrix_e2ee_message` event.

## Device verification

Device verification has an in-UI wizard (v0.3+): **Settings → Devices & Services → Matrix E2EE → Configure → Verify device**. Two paths are supported; see [SECURITY.md](SECURITY.md) for the trust model and [docs/DEVICE_VERIFICATION.md](docs/DEVICE_VERIFICATION.md) ([中文](docs/DEVICE_VERIFICATION.zh.md)) for a step-by-step walkthrough.

### 1. SAS (mutual, manual confirmation)

1. Log in to the bot account in Element (this becomes the bootstrap/admin device) and bootstrap its cross-signing identity.
2. Start the server bot — it creates its device and uploads device keys.
3. In Element, open the bot account's Sessions and verify the server bot device.
4. Compare the SAS emojis on both sides, then confirm from Home Assistant — either via the Options Flow → Verify device wizard (v0.3, recommended) or the `matrix_e2ee.confirm_verification` service. Only this explicit step marks the device verified.

Every device — including another device of the bot's own account — requires this manual emoji comparison and explicit confirmation (wizard or `confirm_verification`). There is no auto-confirm. Only the bot's own account or users in `verification_peer_users` may initiate SAS (`verification_peer_denied` otherwise).

### 2. One-sided fingerprint (fallback)

1. Call `matrix_e2ee.get_fingerprint` (or read `matrix_e2ee_fingerprint`) for the bot's `ed25519` device key.
2. In Element, open the bot user's sessions and use "Manually verify by text".
3. Compare the session key with the fingerprint. This trusts the bot from Element's side only; to trust a device from the bot's side, call `matrix_e2ee.verify_device_by_fingerprint` with the device's `ed25519` key. It is trusted only on an exact match.

SAS interoperability with Element relies on four runtime patches to `matrix-nio` 0.26.0; see [docs/NIO_COMPAT.md](docs/NIO_COMPAT.md) ([中文](docs/NIO_COMPAT.zh.md)).

## Recovery runbook

Session JSON and the crypto store stay on the Home Assistant host. They are gitignored. This is a public repository: never commit tokens, pickle keys, passwords, or store files.

Safe diagnostics (no token, pickle key, password, or message body) are exposed through the Config Entry diagnostics platform — **Settings → Devices & Services → Matrix E2EE → ⋮ → Download diagnostics**:

- `user_id`
- `device_id`
- `session_present`
- `store_present`
- `soft_logged_out`
- `encryption_enabled` (always true for this integration)
- `store_sync_tokens` (always true for this integration)
- `known_device_count` (devices in the bot's device store)
- `verified_peer_count` (devices this bot marked verified)

### Soft logout (`matrix_e2ee_error` code `soft_logout`)

The access token is invalid, but the homeserver still allows the same device to sign in again. The integration **keeps** `.storage/matrix_e2ee_store/` and the existing `device_id`. Send, inbound commands, SAS, and sync stay blocked until reauthentication succeeds.

At setup time a soft logout shows a **Re-authenticate** prompt on the integration (the native reauth flow). At runtime, reauthenticate via the service:

1. Call `matrix_e2ee.reauthenticate` with the bot account password (Developer Tools → Services, or an automation). Provide the password only to this service.
2. On success the session file is rewritten with a **new access token only**. `device_id` and `pickle_key` are unchanged. The crypto store is reused.
3. If the homeserver would return a different `device_id`, the new token is **not** written (`device_mismatch`). The old session remains. This is not a new-device upgrade path.

The password never appears in events, log lines, or service return values.

### Hard logout (`hard_logout`)

The token is invalid and this is **not** a soft logout. Setup **fails**. Do **not** reuse the old crypto store.

1. Revoke the old device on the homeserver if you still can.
2. Delete `<config>/.storage/matrix_e2ee_session.json` **and** `<config>/.storage/matrix_e2ee_store/`.
3. Delete the integration in **Settings → Devices & Services**, then add it again through the UI so first login creates a **new** device.
4. Run SAS again (`start_verification` / `confirm_verification`). Old history cannot be decrypted.

### Crypto store missing (`store_missing`)

Treat this as a new device. The session JSON is not enough to recover Megolm history. Delete the leftover session file, then follow the hard-logout steps (new login + SAS). Do not copy an old store onto a new device.

### Leaked keys or stolen host

1. Revoke the old Matrix device on the homeserver.
2. Destroy the local session file and crypto store (same paths as above).
3. First login creates a new device.
4. SAS-verify devices that should be trusted. Previous ciphertext is not recoverable.

### Short-lived / refresh tokens (`refresh_token_unsupported`)

This integration does not rotate refresh tokens. If login or `reauthenticate` would receive a refresh token or a short-lived access token (`expires_in_ms`), the integration refuses to persist the session. Use a long-lived access token (standard password login without token refresh).

## Roadmap

| Milestone | Intent | Status |
|---|---|---|
| **M1** | Independent YAML integration, unencrypted-room send/commands, allowlist, startup/shutdown, mock tests | Released ([#2](https://github.com/windyboy/ha-matrix-e2ee/pull/2)) |
| **M2** | E2EE lifecycle: first login writes a full crypto device, restart restores the same device, encrypted text path, fail-closed unverified send/commands | Released ([#4](https://github.com/windyboy/ha-matrix-e2ee/pull/4)) |
| **M3** | SAS services/events (`start_verification`, `confirm_verification`, `cancel_verification`) so encrypted send/commands can succeed with verified devices | Released ([#5](https://github.com/windyboy/ha-matrix-e2ee/pull/5)) |
| **M4** | Soft logout / `reauthenticate`, store-loss runbook, diagnostics | Released ([#6](https://github.com/windyboy/ha-matrix-e2ee/pull/6)) |
| **M5** | Config Flow migration: UI setup, options / reconfigure / reauth flows, YAML import, tests | Released (v0.2.0) |
| **v0.3** | Options Flow device-verification wizard (bot- and peer-initiated) with live SAS emoji UI, `m.key.verification.done` handshake | Released (v0.3.8) |
| **v0.3.x** | UI polish + maintenance: single-config-entry enforcement, homeserver URL normalization (HTTPS-only, credential rejection), origin-change reconfigure isolation, verified-peer diagnostics, numbered SAS emoji compare, brand assets, `nio_compat.py` extraction with version guard, CI quality gates (ruff + coverage + audit), docs alignment | In progress ([W1N-190](https://linear.app/w1ndy/issue/W1N-190)) |

M1 acceptance uses unencrypted test rooms. The first successful login still creates a full E2EE-capable Matrix device so M2 does not “upgrade” a non-crypto device.

M3 adds SAS so an already-known device can become `verified=True`. Tests mock nio; they do not use a live Element session. SAS has been manually confirmed on a real deployment; automated end-to-end SAS testing is still backlog (W1N-171).

## License

Apache License 2.0. Copyright 2026 windyboy.
