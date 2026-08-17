# Security

`matrix_e2ee` runs a dedicated Matrix bot as a Home Assistant custom integration with end-to-end encryption. This document records the trust model and the two supported device-verification paths.

## Trust boundary

- The Home Assistant host is an E2EE endpoint. It holds the bot device's private keys (`pickle_key`, Olm/Megolm sessions) and decrypts Matrix text.
- **Cross-signing authority never lives on the host.** The bot's `master` / `self-signing` / `user-signing` keys stay on a trusted Element device (or offline recovery material). The integration holds only the bot's own device keys and cannot sign new trusted devices.
- Compromise of the host, its backups, logs, or the crypto store is compromise of the bot device. Protect them together. Home Assistant's private storage is not disk encryption; use host/volume-level encryption if you must resist offline theft.

## Device verification (two paths)

Device verification remains service/event-based in v0.2 (`start_verification` / `confirm_verification` / `cancel_verification`). A live SAS emoji UI in Home Assistant is deferred to v0.3; compare emojis via the `matrix_e2ee_verification` event today.

### 1. SAS (mutual, manual confirmation)

1. Log in to the bot account in Element. This device becomes the admin/bootstrap device.
2. Bootstrap the bot's cross-signing identity in Element.
3. Start the server bot; it creates its device (`BOT_SERVER_01` is an example identifier — the actual device display name is `Home Assistant matrix_e2ee`, `const.py` `DEVICE_NAME`, with a server-generated random device ID) and uploads device keys.
4. In Element, open the bot account's Sessions and verify `BOT_SERVER_01`.
5. Compare the SAS emojis on both sides, then call `matrix_e2ee.confirm_verification` from Home Assistant. Only this explicit step marks the device verified.
6. Element cross-signs `BOT_SERVER_01`, which then shows as verified.

There is no auto-confirm. A second device of the bot's own account is not trusted just because it shares the account; it must go through the same manual emoji comparison and explicit `confirm_verification`. Only the bot's own account or users in `allowed_users` may initiate SAS. Everything else is rejected with error code `verification_peer_denied`.

### 2. One-sided fingerprint (fallback)

1. Call `matrix_e2ee.get_fingerprint` (or read the `matrix_e2ee_fingerprint` event) to get the bot's `ed25519` device key.
2. In Element, open the bot user's sessions and use "Manually verify by text".
3. Compare the session key with the fingerprint. This trusts the bot from Element's side only.

To trust a device from the bot's side without SAS, call `matrix_e2ee.verify_device_by_fingerprint` with the device's `ed25519` key. It is trusted only when the fingerprint matches exactly (error `fingerprint_mismatch` otherwise). This is local trust, not SAS or cross-signing.

## Why not cross-signing self-sign?

matrix-nio 0.26 does not implement cross-signing key bootstrap or self-signing. Self-signing would also put the bot's cross-signing authority on the host, which this integration intentionally avoids.

## Leaked keys or stolen host

1. Revoke the bot's Matrix device on the homeserver.
2. Delete `.storage/matrix_e2ee_session.json` and `.storage/matrix_e2ee_store/`.
3. Delete the integration and add it again through the UI; first login creates a new device.
4. Re-verify via SAS (or fingerprint). Old ciphertext is not recoverable.
