# Security

`matrix_e2ee` runs a dedicated Matrix bot as a Home Assistant custom integration with end-to-end encryption. This document records the trust model and the two supported device-verification paths.

## Trust boundary

- The Home Assistant host is an E2EE endpoint. It holds the bot device's private keys (`pickle_key`, Olm/Megolm sessions) and decrypts Matrix text.
- **Cross-signing authority never lives on the host.** The bot's `master` / `self-signing` / `user-signing` keys stay on a trusted Element device (or offline recovery material). The integration holds only the bot's own device keys and cannot sign new trusted devices.
- Compromise of the host, its backups, logs, or the crypto store is compromise of the bot device. Protect them together. Home Assistant's private storage is not disk encryption; use host/volume-level encryption if you must resist offline theft.

## Single bot / one config entry

`matrix_e2ee` supports exactly one config entry (`"single_config_entry": true`). The session file (`.storage/matrix_e2ee_session.json`) and crypto store (`.storage/matrix_e2ee_store/`) are global to the integration, not per-entry: a second entry would rebind them to a different account and device. The Config Flow therefore aborts with "Already configured" for any second entry, regardless of username. Do not try to run two bot accounts through this integration.

## Reconfigure and server-origin change

Reconfigure lets you change the homeserver URL. The integration compares the **origin** (scheme, host, port), not the path or trailing slash:

- **Same origin** (only trailing slash or path changed): the session and crypto store are kept, so the same device and its trust state survive.
- **Different origin**: the integration **fails closed** — it quarantines the old session file and crypto store (renamed with a `.quarantined-*` suffix, never deleted), never sends the old access token to the new origin, and requires you to re-enter the password to log in as a **new device**. You must then re-run device verification; the new device does not inherit the old device's trust.

## Device verification (two paths)

Use **Settings → Devices & Services → Matrix E2EE → Configure → Verify device** for the live SAS emoji wizard. The wizard waits for Element to initiate verification; it does not start a transaction itself. The `start_verification` / `confirm_verification` / `cancel_verification` services remain available for advanced use, and `matrix_e2ee_verification` reports every stage.

### 1. SAS (mutual, manual confirmation)

1. Log in to the bot account in Element. This device becomes the admin/bootstrap device.
2. Bootstrap the bot's cross-signing identity in Element.
3. Start the integration; it creates a device with display name `Home Assistant matrix_e2ee` and a server-generated device ID.
4. Open the wizard in Home Assistant, then verify that device from Element's Sessions page.
5. Compare every SAS emoji and confirm from Home Assistant only when both sides match.
6. After the MAC exchange completes, Element shows the device as verified.

There is no auto-confirm. A second device of the bot's own account is not trusted just because it shares the account; it must go through the same manual emoji comparison and explicit `confirm_verification`. Only the bot's own account or users in `verification_peer_users` may initiate SAS. Unauthorized framework requests are ignored; unauthorized SAS events emit `verification_peer_denied`.

### 2. One-sided fingerprint (fallback)

1. Listen for `matrix_e2ee_fingerprint`, then call `matrix_e2ee.get_fingerprint` and read the bot's `ed25519` device key from the event.
2. In Element, open the bot user's sessions and use "Manually verify by text".
3. Compare the session key with the fingerprint. This trusts the bot from Element's side only.

To trust a device from the bot's side without SAS, call `matrix_e2ee.verify_device_by_fingerprint` with the device's `ed25519` key. It is trusted only when the fingerprint matches exactly; otherwise `matrix_e2ee_error` emits `fingerprint_mismatch`. This is local trust, not SAS or cross-signing.

## Why not cross-signing self-sign?

matrix-nio 0.26 does not implement cross-signing key bootstrap or self-signing. Self-signing would also put the bot's cross-signing authority on the host, which this integration intentionally avoids.

For operational steps, see [Device verification](docs/DEVICE_VERIFICATION.md). For implementation boundaries, see [SAS architecture](docs/SAS_ARCHITECTURE.md).

## Leaked keys or stolen host

1. Revoke the bot's Matrix device on the homeserver.
2. Delete `.storage/matrix_e2ee_session.json` and `.storage/matrix_e2ee_store/`.
3. Delete the integration and add it again through the UI; first login creates a new device.
4. Re-verify via SAS (or fingerprint). Old ciphertext is not recoverable.
