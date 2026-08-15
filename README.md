# matrix_e2ee

Home Assistant **custom** integration that runs a dedicated Matrix bot with a persistent end-to-end encryption (E2EE) device identity.

- Unique domain: `matrix_e2ee`
- Does **not** override Home Assistant’s built-in `matrix` integration
- Python, `matrix-nio[e2e]==0.26.0`
- YAML setup only in M1/M2 (no Config Flow)

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

Do not claim production E2EE support until M3 real Element/homeserver interop has passed.

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

## Services and events (M1/M2)

- Service: `matrix_e2ee.send_message` (`message`, `room_id`)
- Event: `matrix_e2ee_command` (`room_id`, `sender`, `command`, `args` only — never the raw body)
- Event: `matrix_e2ee_error` (error codes, no secrets)

Commands fire Home Assistant events only. This integration never calls `domain.service` itself. Map commands in automations.

`notify.matrix_e2ee` is deferred (after M3). SAS services are M3. There is no `matrix_e2ee_message` event.

## Roadmap

| Milestone | Intent | Status |
|---|---|---|
| **M1** | Independent YAML integration, unencrypted-room send/commands, allowlist, startup/shutdown, mock tests | This delivery |
| **M2** | E2EE lifecycle: first login writes a full crypto device, restart restores the same device, encrypted text path, fail-closed unverified send/commands | This delivery |
| **M3** | SAS services/events (`start_verification`, `confirm_verification`, `cancel_verification`) so encrypted send/commands can succeed with verified devices | Backlog |
| **M4** | Soft logout / `reauthenticate`, store-loss runbook, diagnostics | Backlog |

M1 acceptance uses unencrypted test rooms. The first successful login still creates a full E2EE-capable Matrix device so M2 does not “upgrade” a non-crypto device.

M2 encrypted send to rooms with unverified devices **fails closed** (that is a pass). Real Element-visible encrypted notifications and verified commands require M3.

## License

Apache License 2.0. Copyright 2026 windyboy.
