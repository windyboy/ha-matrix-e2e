# matrix_e2ee

Home Assistant **custom** integration that runs a dedicated Matrix bot with a persistent end-to-end encryption (E2EE) device identity.

- Unique domain: `matrix_e2ee`
- Does **not** override Home Assistant’s built-in `matrix` integration
- Python, `matrix-nio[e2e]==0.26.0` with its E2EE extras (`vodozemac`, `peewee`, `cachetools`, `atomicwrites`) declared explicitly in `manifest.json`
- Config Flow (UI) setup; not in HACS

## Table of contents

- [Documentation](#documentation)
- [Installation](#installation)
- [Security warning](#security-warning)
- [Configuration (UI)](#configuration-ui)
- [Services, events, and automations](#services-events-and-automations)
- [Entities and diagnostics](#entities-and-diagnostics)
- [Device verification](#device-verification)
- [Error codes](#error-codes)
- [Recovery runbook](#recovery-runbook)
- [Roadmap](#roadmap)
- [License](#license)

## Documentation

| Document | Audience | Purpose |
|---|---|---|
| [Device verification](docs/DEVICE_VERIFICATION.md) ([中文](docs/DEVICE_VERIFICATION.zh.md)) | Administrators | SAS and fingerprint walkthrough |
| [Security model](SECURITY.md) ([中文](SECURITY.zh.md)) | Everyone | Trust boundaries, storage, compromise / migration |
| [SAS architecture](docs/SAS_ARCHITECTURE.md) ([中文](docs/SAS_ARCHITECTURE.zh.md)) | Maintainers | Trust rules, message flow, component responsibilities |
| [matrix-nio compatibility](docs/NIO_COMPAT.md) ([中文](docs/NIO_COMPAT.zh.md)) | Maintainers | Runtime fixes and upgrade checklist |
| [Development notes](docs/DEVELOPMENT.md) ([中文](docs/DEVELOPMENT.zh.md)) | Contributors | Environment, tests, CI, local install |
| [Changelog](CHANGELOG.md) | Everyone | Release history |

Current release: **v0.3.11** (see `custom_components/matrix_e2ee/manifest.json`).

## Installation

This is a **manual** custom integration (not in HACS). It is configured through Home Assistant's Config Flow (UI), not YAML.

Use a dedicated **non-admin** Matrix bot account. Do not run Home Assistant’s built-in `matrix` integration and `matrix_e2ee` on the same bot account. Read the [security warning](#security-warning) before enabling it.

`<config>` is the Home Assistant configuration directory (`/config` on Home Assistant OS / Container).

### 1. Copy the integration

Copy only the `matrix_e2ee` package into `custom_components`. The GitHub repository root is not that folder — do not install the whole repo tree (docs/, tests/, etc.) under `custom_components`.

**From a release archive** (recommended):

1. Download the source zip of the latest release: https://github.com/windyboy/ha-matrix-e2ee/releases
2. Extract it, then copy `custom_components/matrix_e2ee` to the HA config directory:

```bash
mkdir -p /config/custom_components
cp -a custom_components/matrix_e2ee /config/custom_components/matrix_e2ee
```

**From git** (replace `v0.3.11` with the [release tag](https://github.com/windyboy/ha-matrix-e2ee/releases) you want):

```bash
git clone --depth 1 --branch v0.3.11 https://github.com/windyboy/ha-matrix-e2ee.git /tmp/ha-matrix-e2ee
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
4. On first load Home Assistant installs `matrix-nio[e2e]` and its E2EE dependencies from `manifest.json`. That can take a minute; if setup fails on the first attempt after a fresh copy, wait for the dependency install to finish and retry or restart once more.
5. On first login the integration creates the bot device and writes its crypto store. Later restarts restore the same device without any password.

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

### Homeserver URL rules

The integration normalizes and validates the homeserver URL before login:

- Bare hosts default to `https://`.
- **HTTPS is required** except for localhost / loopback (`localhost`, `127.0.0.1`, `::1`).
- Usernames or passwords embedded in the URL are rejected.
- Trailing slashes, paths, and default ports are stripped; only the origin (`scheme://host[:port]`) is kept.

Invalid input surfaces as Config Flow errors such as `homeserver_invalid`, `homeserver_http_not_allowed`, or `homeserver_credentials`.

### Access-control semantics

| Option | Empty value means |
|---|---|
| `allowed_rooms` | No send and no inbound commands |
| `allowed_users` | No inbound commands; send to allowed rooms is still permitted |
| `verification_peer_users` | No inbound SAS from other accounts (only the bot's own account may initiate) |

The old YAML block is no longer required. If a `matrix_e2ee:` block remains in `configuration.yaml`, it is imported into a config entry on startup (see [Migrating from the old YAML setup](#3-migrating-from-the-old-yaml-setup)).

## Services, events, and automations

### Services

| Service | Fields | Notes |
|---|---|---|
| `matrix_e2ee.send_message` | `message`, `room_id` | Room must be in `allowed_rooms`. Fails closed on unverified devices in encrypted rooms. |
| `matrix_e2ee.reauthenticate` | `password` | Soft logout only. Replaces access token; keeps `device_id` and crypto store. **Admin only.** |
| `matrix_e2ee.start_verification` | `user_id`, `device_id` | Device must already be in the crypto store. Does not trust until confirm. **Admin only.** |
| `matrix_e2ee.confirm_verification` | `transaction_id` | Only step that marks a device verified. **Admin only.** |
| `matrix_e2ee.cancel_verification` | `transaction_id` | Cancels in-progress SAS. **Admin only.** |
| `matrix_e2ee.get_fingerprint` | (none) | Emits `matrix_e2ee_fingerprint` with the bot's public keys. |
| `matrix_e2ee.verify_device_by_fingerprint` | `user_id`, `device_id`, `ed25519` | One-sided local trust on exact match. **Admin only.** |

Admin-only services are enforced by Home Assistant's admin-service helper.

### Events

| Event | Payload (no secrets) |
|---|---|
| `matrix_e2ee_command` | `room_id`, `sender`, `command`, `args` only — never the raw body |
| `matrix_e2ee_error` | `code` plus non-secret context fields (see [Error codes](#error-codes)) |
| `matrix_e2ee_verification` | `stage`, `transaction_id`, `user_id`, `device_id`; optional `emojis`, `expires_at` |
| `matrix_e2ee_fingerprint` | `user_id`, `device_id`, `ed25519`, `curve25519` — public keys only |

SAS emoji comparison is available in the Options Flow wizard or, for advanced use, through the `matrix_e2ee_verification` event (`stage: sas`) in Developer Tools. Confirming is the only step that marks a device verified. Accepting an inbound SAS start is protocol continuation, not trust.

Commands fire Home Assistant events only. This integration never calls `domain.service` itself. Map commands in automations.

`notify.matrix_e2ee` is deferred. There is no `matrix_e2ee_message` event.

### Example automations

Send a message when a binary sensor trips:

```yaml
automation:
  - alias: "Notify Matrix on front door"
    trigger:
      - platform: state
        entity_id: binary_sensor.front_door
        to: "on"
    action:
      - service: matrix_e2ee.send_message
        data:
          room_id: "!yourRoomId:example.org"
          message: "Front door opened"
```

React to an inbound Matrix command (map `!ping` to a reply):

```yaml
automation:
  - alias: "Matrix !ping"
    trigger:
      - platform: event
        event_type: matrix_e2ee_command
        event_data:
          command: ping
    action:
      - service: matrix_e2ee.send_message
        data:
          room_id: "{{ trigger.event.data.room_id }}"
          message: "pong"
```

Reauthenticate after soft logout (admin only; prefer the UI reauth prompt when available):

```yaml
automation:
  - alias: "Matrix soft-logout reauth"
    trigger:
      - platform: event
        event_type: matrix_e2ee_error
        event_data:
          code: soft_logout
    action:
      - service: matrix_e2ee.reauthenticate
        data:
          password: !secret matrix_bot_password
```

## Entities and diagnostics

### Connection binary sensor

The integration exposes a diagnostic connectivity entity:

- **Entity**: `binary_sensor.*_connection` (name: **Connection**)
- **Device class**: connectivity
- **Category**: diagnostic
- **On**: bot is connected and not soft-logged-out
- **Attributes** (no secrets): `soft_logged_out`, `device_id`, `known_device_count`, `verified_peer_count`, `verified_peers` (up to 10 `{user_id, device_id}` pairs; peer devices only, never the bot itself)

### Config Entry diagnostics

**Settings → Devices & Services → Matrix E2EE → ⋮ → Download diagnostics** returns a redacted snapshot:

| Field | Meaning |
|---|---|
| `integration_version` | Version from `manifest.json` |
| `nio_version` | Installed `matrix-nio` version (if importable) |
| `client.user_id` | Bot Matrix user ID |
| `client.device_id` | Bot device ID |
| `client.session_present` | Session file restored |
| `client.store_present` | Crypto store directory present |
| `client.soft_logged_out` | Soft-logout latch |
| `client.encryption_enabled` | Always `true` for this integration |
| `client.store_sync_tokens` | Always `true` for this integration |
| `client.known_device_count` | Devices in the bot's device store |
| `client.verified_peer_count` | Devices this bot marked verified |

Never includes access tokens, pickle keys, passwords, message bodies, or crypto material.

## Device verification

Use **Settings → Devices & Services → Matrix E2EE → Configure → Verify device** and initiate verification for `Home Assistant matrix_e2ee` from Element. Compare every emoji and confirm only when both sides match. Devices are never trusted automatically, even when they belong to the same account.

See [Device verification](docs/DEVICE_VERIFICATION.md) ([中文](docs/DEVICE_VERIFICATION.zh.md)) for the complete walkthrough, [Security](SECURITY.md) for the trust model, and [SAS architecture](docs/SAS_ARCHITECTURE.md) for implementation details.

## Error codes

`matrix_e2ee_error` events carry a `code` field (never secrets). Common codes:

| Code | Meaning | Typical action |
|---|---|---|
| `soft_logout` | Access token invalid; same device may sign in again | Call `reauthenticate` or use the UI reauth prompt |
| `hard_logout` | Token invalid and not soft logout | Delete session + store, remove integration, add again, re-verify |
| `store_missing` | Crypto store directory missing | Treat as new device; do not reuse leftover session alone |
| `session_missing` | Session file missing on restore path | First login / re-setup with password |
| `session_corrupt` | Session file unreadable or invalid | Delete session (+ store if inconsistent), re-setup |
| `restore_failed` | Could not restore client from session/store | Check logs; often ends in re-setup |
| `password_required` | Password needed but not provided | Supply password to login / reauth |
| `login_failed` | Homeserver rejected login | Check credentials and homeserver URL |
| `device_mismatch` | Reauth would change `device_id` | Token not written; not a new-device upgrade path |
| `room_not_allowed` | `room_id` not in `allowed_rooms` | Add room or send elsewhere |
| `send_failed` | Send failed for a non-trust reason | Check connectivity and room membership |
| `unverified_device` | Encrypted send blocked: unverified/unknown devices | Complete SAS (or fingerprint) for those devices |
| `encryption_unavailable` | E2EE path not available | Check nio/store; should not happen on a healthy install |
| `device_missing` | Target device not in crypto store | Wait for device keys / start verification only for known devices |
| `fingerprint_mismatch` | Manual fingerprint did not match store | Recheck key; do not force trust |
| `invalid_transaction` | Unknown or expired SAS `transaction_id` | Start a new verification |
| `invalid_state` | Service called in an unexpected client state | Check soft-logout / connection; retry after recovery |
| `verification_timeout` | Integration 240s verification window expired | Restart SAS from Element |
| `verification_peer_denied` | Initiator not bot account and not in `verification_peer_users` | Adjust allowlist or initiate from an allowed user |
| `refresh_token_unsupported` | Server offered refresh/short-lived token | Use long-lived password login without token refresh |

## Recovery runbook

Session JSON and the crypto store stay on the Home Assistant host. They are gitignored. This is a public repository: never commit tokens, pickle keys, passwords, or store files.

Safe diagnostics (no token, pickle key, password, or message body) are available via **Download diagnostics** and the **Connection** binary sensor — see [Entities and diagnostics](#entities-and-diagnostics).

**Session file and crypto store must always be backed up and restored together.** The session JSON alone cannot recover Megolm history; the store alone is useless without the matching `pickle_key` in the session file. Treat a mismatched pair as a new device.

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

### Migrating to a new Home Assistant host (legitimate move)

1. Stop Home Assistant on the old host (or disable the integration) so the bot is not connected from two places.
2. Copy **both** `<config>/.storage/matrix_e2ee_session.json` and `<config>/.storage/matrix_e2ee_store/` to the same paths on the new host. They must stay a matched pair.
3. Install the same `matrix_e2ee` version under `custom_components` on the new host and restart.
4. Re-add the integration through **Settings → Devices & Services** (the flow still asks for a password; with the session file present, the client restores the same `device_id` instead of creating a new one). If the store or session is missing or mismatched, treat it as a new device and re-verify.

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
| **v0.3** | Options Flow device-verification wizard for Element-initiated SAS with live emoji comparison, `m.key.verification.done` handshake | Released (v0.3.8) |
| **v0.3.x** | UI polish + maintenance: single-config-entry enforcement, homeserver URL normalization (HTTPS-only, credential rejection), origin-change reconfigure isolation, verified-peer diagnostics, Connection binary sensor, numbered SAS emoji compare, brand assets, `nio_compat.py` extraction with version guard, CI quality gates (ruff + coverage + audit), docs alignment | Released (v0.3.10) |

M1 acceptance uses unencrypted test rooms. The first successful login still creates a full E2EE-capable Matrix device so M2 does not “upgrade” a non-crypto device.

M3 adds SAS so an already-known device can become `verified=True`. Tests mock nio; they do not use a live Element session. SAS has been manually confirmed on a real deployment; automated end-to-end SAS testing is still backlog (W1N-171).

## License

Apache License 2.0. Copyright 2026 windyboy.
