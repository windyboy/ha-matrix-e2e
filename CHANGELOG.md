# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.10] - 2026-08-19

### Documentation

- Ship bilingual EN/ZH operator and maintainer docs under `docs/`.
- Align README with released v0.3.x behavior: error-code table, Connection diagnostic entity, diagnostics fields, URL normalization rules, automation examples, recovery/migration notes.

## [0.3.9] - 2026-08-18

### Added / Changed (W1N-190)

- Single config-entry enforcement.
- Homeserver URL normalization (HTTPS-only except localhost; reject embedded credentials).
- Origin-change reconfigure isolation (quarantine old session/store).
- Verified-peer diagnostics and Connection health binary sensor.
- Numbered SAS emoji comparison in the Options Flow wizard.
- Brand assets under `custom_components/matrix_e2ee/brand/`.
- `nio_compat.py` extraction with version guard.
- CI quality gates: ruff, coverage threshold, pip-audit on runtime requirements.

## [0.3.8] - 2026-08

### Added

- Options Flow device-verification wizard for Element-initiated SAS with live emoji comparison.
- `m.key.verification.done` handshake completion for request-based SAS.

## [0.3.0] – [0.3.7]

Incremental SAS wizard, peer-initiated verification, and related fixes. See git history and Linear W1N-180…W1N-189.

## [0.2.0] - M5

### Added

- Config Flow UI setup, options / reconfigure / reauth flows, YAML import, and related tests.

## [0.1.x] - M1–M4

### Added

- M1: independent YAML integration, unencrypted send/commands, allowlists, mock tests.
- M2: E2EE lifecycle, encrypted text path, fail-closed unverified send/commands.
- M3: SAS services and events.
- M4: soft logout / `reauthenticate`, store-loss runbook, diagnostics foundation.

[0.3.10]: https://github.com/windyboy/ha-matrix-e2ee/releases/tag/v0.3.10
[0.3.9]: https://github.com/windyboy/ha-matrix-e2ee/releases/tag/v0.3.9
[0.3.8]: https://github.com/windyboy/ha-matrix-e2ee/releases/tag/v0.3.8
