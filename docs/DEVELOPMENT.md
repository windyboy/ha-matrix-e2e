# Development notes

This repository is a Home Assistant custom integration (`custom_components/matrix_e2ee`). It does **not** override the built-in `matrix` domain.

## Tests

- Use **pytest** with a mocked `nio.AsyncClient` and a temporary Home Assistant config/storage directory.
- Do **not** add `pytest-homeassistant-custom-component` unless that is an explicit later decision.
- Do **not** use a real Matrix homeserver, access token, pickle key, crypto store, or SAS transcript in tests or in git.

From the repository root:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
```

These tests mock `nio.AsyncClient` and do not install Home Assistant or `pytest-homeassistant-custom-component`. If a required Home Assistant test API is missing for a later check, stop and ask. Do not invent a workaround or install packages into Home Assistant OS.

M1 contract: `tests/test_m1_contract.py`. M2 contract: `tests/test_m2_contract.py`. M3 contract: `tests/test_m3_contract.py`. Shared fake client: `tests/fakes.py`.

## Local layout

Copy `custom_components/matrix_e2ee` into `<config>/custom_components/matrix_e2ee` on a Home Assistant instance. Configure YAML only (no Config Flow in M1–M3).

Session and crypto store are written under `<config>/.storage/` at runtime. Those paths are gitignored and must stay on the same persistent volume as Home Assistant.

## Secrets

Never log or commit:

- access tokens
- pickle keys
- message bodies
- crypto-store contents
- homeserver passwords or test credentials
