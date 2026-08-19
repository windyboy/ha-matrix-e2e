# SAS device-verification architecture

> Audience: maintainers changing `matrix_e2ee` verification flows, trust policy, or matrix-nio compatibility.
>
> Language: [English](SAS_ARCHITECTURE.md) | [中文](SAS_ARCHITECTURE.zh.md)

This document explains how the integration implements Matrix Short Authentication String (SAS) device verification. See the [device-verification guide](DEVICE_VERIFICATION.md) for user instructions and [matrix-nio compatibility](NIO_COMPAT.md) for the four runtime fixes.

## Scope

The integration uses to-device messages for `m.sas.v1` and supports both directions:

- Element initiates and the Home Assistant bot responds. The UI wizard uses this recommended flow.
- The bot initiates with the `start_verification` service for a known device.

Both directions require a person to compare the emoji and explicitly confirm. Protocol `accept` means only that negotiation may continue; it does not trust a device.

The integration does not:

- Create or manage cross-signing private keys on the Home Assistant host.
- Import Secure Secret Storage and Sharing (SSSS) or key backups.
- Automatically trust devices from the same account.
- Downgrade from an unverified device to plaintext.

The Matrix [Key verification framework](https://spec.matrix.org/latest/client-server-api/#key-verification-framework) and SAS method remain the protocol authority. This document records only project-specific behavior.

## Trust model

The Home Assistant host is an independent E2EE endpoint. It stores the bot device's private keys, Olm/Megolm sessions, and verified-device state. Cross-signing master, self-signing, and user-signing private keys stay on a trusted Element device or in offline recovery material.

This creates four constraints:

1. Compromise of the host or crypto store compromises the bot device.
2. Other devices on the same Matrix account still require independent verification.
3. SAS establishes mutual trust; fingerprint verification establishes only local, one-sided trust.
4. A new device created after crypto-store loss cannot inherit the old device's trust.

See [SECURITY.md](../SECURITY.md) for the complete threat boundary and compromise response.

## Component responsibilities

| Component | Responsibility |
|---|---|
| Element | Initiates the recommended flow, displays emoji, and holds the cross-signing identity |
| Options Flow | Waits for inbound SAS, displays the bot-side emoji, and collects match or cancel |
| `client.py` | Gates initiators, bridges missing framework messages, manages timeouts, events, and services |
| matrix-nio | Runs the `Sas` state machine, exchanges ephemeral keys, derives emoji, and generates and verifies MACs |
| `nio_compat.py` | Corrects four matrix-nio 0.26.0 interoperability defects |

`enable_verification_callbacks()` registers both a generic to-device callback and nio SAS event callbacks. `handle_to_device_event()` handles only `start`, `key`, `mac`, and `cancel`; matrix-nio handles `accept` internally.

## Recommended Element-initiated flow

Options Flow's `async_step_verify_device()` waits for an inbound request and never creates a transaction itself.

```text
Element                         Home Assistant / matrix-nio
   |  request  -------------------------------->  validate peer, device, method, timestamp
   |  <--------------------------------  ready
   |  start    -------------------------------->  build or repair SAS; send accept
   |  <------------------------------->  exchange keys
   |                                             fire stage: sas
   |             user compares both emoji sets
   |                                             confirm_short_auth_string()
   |  <------------------------------->  exchange MACs
   |                                             verify device
   |  <--------------------------------  done
   |                                             fire stage: done
```

matrix-nio 0.26.0 does not implement the `request`, `ready`, or `done` framework messages. The integration supplies them through `_handle_verification_request()`, `_send_verification_ready()`, and `_send_verification_done()`:

- `request` must come from the bot's own account or a user allowed by `verification_peer_users`.
- It must contain `m.sas.v1`, valid device and transaction IDs, and an acceptable timestamp.
- `ready` advertises SAS support but does not establish trust.
- After MAC verification, `done` moves Element from its waiting state to completion.

When a new device is not yet in nio's `device_store`, nio drops the first `start`. `_repair_dropped_start()` queries the sender's device keys and gives the same event back to nio, avoiding a manual retry.

## Bot-initiated flow

`async_start_verification(user_id, device_id)` starts only with a device already known to `device_store` and returns the transaction ID. nio still handles the later `accept`, `key`, and MAC processing.

This direction primarily supports service calls and automations. It never bypasses human confirmation: only `async_confirm_verification(transaction_id)` accepts the SAS and sends the bot's MAC.

## States, events, and timeout

The `matrix_e2ee_verification` event uses these stages:

| Stage | Meaning |
|---|---|
| `started` | The SAS transaction exists |
| `sas` | Emoji are available for comparison |
| `done` | MAC verification completed and the device is verified |
| `canceled` | Either side canceled the transaction |
| `timeout` | The integration's verification window expired |

The `sas` stage also carries `emojis` and `expires_at`. Other stages carry transaction and peer-device identifiers; cancellation may also carry a protocol code and reason.

The integration records a 240-second deadline when it first sees the transaction; later events do not refresh it. Confirmation and inbound-event handling check this deadline, while the Options Flow stops waiting after the same interval. The next checked interaction cancels an expired transaction and rejects its `transaction_id`; an otherwise idle transaction may remain until nio cleanup. The compatibility layer corrects nio's separate timeout defect.

## Security invariants

Verification changes must preserve these rules:

1. Call `confirm_short_auth_string()` only after a person confirms that the emoji match.
2. Never trust a device merely because it belongs to the same account.
3. Apply `_bootstrap_allowed()` to every inbound verification branch.
4. Compare fingerprints with exact, case-sensitive equality; never use `casefold()`.
5. Let nio send `key`; the integration must not call `share_key()` again.
6. Send MAC once, only through confirmation; change generation and verification together.
7. Unverified devices must not trigger command events or cause a plaintext fallback.
8. Logs and events must not contain access tokens, pickle keys, message bodies, or crypto-store contents.

## Test boundary

Existing tests use `FakeNio` and `FakeSas`. They cover services, events, timeout, gating, and Options Flow, but cannot expose wire-format differences between real clients. Changes to SAS, commitments, emoji, or MAC handling require an additional end-to-end check with Element on a real Matrix homeserver.

Relevant tests:

- `tests/test_m3_contract.py`: verification services, events, and trust policy.
- `tests/test_options_flow.py`: UI wizard.
- `tests/test_nio_compat.py`: matrix-nio compatibility layer.

## Related documentation

- [Device verification](DEVICE_VERIFICATION.md)
- [matrix-nio compatibility](NIO_COMPAT.md)
- [Development notes](DEVELOPMENT.md)
- [Security model](../SECURITY.md)
