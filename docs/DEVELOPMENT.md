# Development notes

This repository is a Home Assistant custom integration (`custom_components/matrix_e2ee`). It does **not** override the built-in `matrix` domain.

## Supported versions

| Component | Version |
| --- | --- |
| Python | 3.14 |
| Home Assistant | 2026.8.x (test harness `2026.8.2`) |
| matrix-nio | `0.26.0` (pinned; `matrix-nio[e2e]==0.26.0`) |
| vodozemac | `>=0.9.0.post2` |
| peewee | `~=3.14` |
| cachetools | `>=5.3` |
| atomicwrites | `~=1.4` |

The runtime dependency pins live in `custom_components/matrix_e2ee/manifest.json`; the dev/test pins live in `requirements-dev.txt`. CI runs on Python 3.14 (GitHub Actions `ubuntu-latest`). Do not bump `matrix-nio` without following the upgrade checklist in [NIO_COMPAT.md](NIO_COMPAT.md).

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

M1 contract: `tests/test_m1_contract.py`. M2 contract: `tests/test_m2_contract.py`. M3 contract: `tests/test_m3_contract.py`. M4 contract: `tests/test_m4_contract.py`. Config Flow: `tests/test_config_flow.py`, `tests/test_reauth.py`, `tests/test_options_flow.py`, `tests/test_import.py`, `tests/test_entry_lifecycle.py`, `tests/test_manifest.py`. SAS patches: `tests/test_nio_compat.py`. Shared fake client: `tests/fakes.py`.

## CI quality gates

`.github/workflows/tests.yml` runs three jobs on every push and pull request:

- **lint** — `ruff check` and `ruff format --check` (config in `ruff.toml`).
- **pytest** — `pytest --cov=custom_components.matrix_e2ee --cov-fail-under=81` (the coverage floor tracks the measured baseline).
- **audit** — `pip-audit` against the runtime requirements extracted from `manifest.json`.

Run the same checks locally before pushing:

```bash
uv run python -m ruff check custom_components/ tests/
uv run python -m ruff format --check custom_components/ tests/
uv run python -m pytest --cov=custom_components.matrix_e2ee --cov-fail-under=81
uv run python -m pip_audit --requirement <(uv run python -c "import json; print('\n'.join(json.load(open('custom_components/matrix_e2ee/manifest.json'))['requirements']))")
```

## nio compatibility

The integration applies four runtime patches to `matrix-nio` 0.26.0 `Sas` for
Element SAS interoperability. See [NIO_COMPAT.md](NIO_COMPAT.md) for the patch
matrix and the upgrade checklist before bumping `matrix-nio`.

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
