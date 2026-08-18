# Development notes

This repository is a Home Assistant custom integration (`custom_components/matrix_e2ee`). It does **not** override the built-in `matrix` domain.

## Tests

- Use **pytest** with a mocked `nio.AsyncClient` and a temporary Home Assistant config/storage directory.
- Config Flow / Options Flow / reauth / entry lifecycle tests use the Home Assistant test harness (`pytest-homeassistant-custom-component` + `homeassistant`).
- Do **not** use a real Matrix homeserver, access token, pickle key, crypto store, or SAS transcript in tests or in git.

From the repository root:

```bash
uv run python -m pytest
```

`uv` manages the project's `.venv` (deps in `requirements-dev.txt`) and selects the `.venv` interpreter automatically.

These tests mock `nio.AsyncClient`; the flow/lifecycle tests use the Home Assistant test harness with a mocked nio client injected via the module-level `_NIO_CLIENT_FACTORY`. Do not invent a workaround or install packages into Home Assistant OS.

M1 contract: `tests/test_m1_contract.py`. M2 contract: `tests/test_m2_contract.py`. M3 contract: `tests/test_m3_contract.py`. M4 contract: `tests/test_m4_contract.py`. Config Flow: `tests/test_config_flow.py`, `tests/test_reauth.py`, `tests/test_options_flow.py`, `tests/test_import.py`, `tests/test_entry_lifecycle.py`, `tests/test_manifest.py`. SAS MAC patch: `tests/test_sas_mac_patch.py`. Shared fake client: `tests/fakes.py`.

## Local layout

Copy `custom_components/matrix_e2ee` into `<config>/custom_components/matrix_e2ee` on a Home Assistant instance. Configure via the Config Flow UI (**Settings → Devices & Services → Add Integration → Matrix E2EE**). A leftover `matrix_e2ee:` YAML block is imported into a config entry on startup.

Session and crypto store are written under `<config>/.storage/` at runtime. Those paths are gitignored and must stay on the same persistent volume as Home Assistant.

## Secrets

Never log or commit:

- access tokens
- pickle keys
- message bodies
- crypto-store contents
- homeserver passwords or test credentials
