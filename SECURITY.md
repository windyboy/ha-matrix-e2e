# Security

`matrix_e2ee` runs a dedicated Matrix bot as a Home Assistant custom integration with end-to-end encryption. This document records the trust model and the two supported device-verification paths.

## Trust boundary

- The Home Assistant host is an E2EE endpoint. It holds the bot device's private keys (`pickle_key`, Olm/Megolm sessions) and decrypts Matrix text.
- **Cross-signing authority never lives on the host.** The bot's `master` / `self-signing` / `user-signing` keys stay on a trusted Element device (or offline recovery material). The integration holds only the bot's own device keys and cannot sign new trusted devices.
- Compromise of the host, its backups, logs, or the crypto store is compromise of the bot device. Protect them together. Home Assistant's private storage is not disk encryption; use host/volume-level encryption if you must resist offline theft.

## Device verification (two paths)

### 1. SAS auto-completion (mutual)

1. Log in to the bot account in Element. This device becomes the admin/bootstrap device.
2. Bootstrap the bot's cross-signing identity in Element.
3. Start the server bot; it creates its device (`BOT_SERVER_01`) and uploads device keys.
4. In Element, open the bot account's Sessions and verify `BOT_SERVER_01`.
5. The integration auto-completes the SAS handshake when the initiator is the bot's own account; confirm the emoji match in Element.
6. Element cross-signs `BOT_SERVER_01`, which then shows as verified.

Only the bot's own account (`sender == session.user_id`) or users in `allowed_users` may initiate SAS. Everything else is rejected with error code `verification_peer_denied`.

### 2. One-sided fingerprint (fallback)

1. Call `matrix_e2ee.get_fingerprint` (or read the `matrix_e2ee_fingerprint` event) to get the bot's `ed25519` device key.
2. In Element, open the bot user's sessions and use "Manually verify by text".
3. Compare the session key with the fingerprint. This trusts the bot from Element's side only.

To trust a device from the bot's side without SAS, call `matrix_e2ee.verify_device`.

## Why not cross-signing self-sign?

matrix-nio 0.26 does not implement cross-signing key bootstrap or self-signing. Self-signing would also put the bot's cross-signing authority on the host, which this integration intentionally avoids.

## Leaked keys or stolen host

1. Revoke the bot's Matrix device on the homeserver.
2. Delete `.storage/matrix_e2ee_session.json` and `.storage/matrix_e2ee_store/`.
3. Restart with a bootstrap password; first login creates a new device.
4. Re-verify via SAS (or fingerprint). Old ciphertext is not recoverable.
