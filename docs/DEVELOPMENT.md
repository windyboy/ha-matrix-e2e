# Development notes

> Audience: contributors developing, testing, and releasing `matrix_e2ee`.
>
> Language: [English](DEVELOPMENT.md) | [中文](DEVELOPMENT.zh.md)

This repository provides the Home Assistant custom integration `custom_components/matrix_e2ee`. It does not override Home Assistant's built-in `matrix` domain.

## Environment and dependencies

| Component | Current constraint |
|---|---|
| Python | 3.14 |
| Home Assistant test harness | `2026.8.2` |
| matrix-nio | `matrix-nio[e2e]==0.26.0` |

`custom_components/matrix_e2ee/manifest.json` is the source of truth for runtime dependencies. `requirements-dev.txt` is the source of truth for development and test dependencies. Do not maintain another complete dependency copy in documentation.

The project uses `uv` and the `.venv` at the repository root. Run every Python command through `uv run`:

```bash
uv run python --version
uv run python -m pytest
```

The first command must report Python 3.14.x. Before upgrading matrix-nio, complete the checklist in [NIO_COMPAT.md](NIO_COMPAT.md).

## Test structure

Tests do not connect to a real Matrix homeserver. They inject a simulated `nio.AsyncClient` through the module-level `_NIO_CLIENT_FACTORY` and use temporary Home Assistant configuration and storage directories.

| Category | Files |
|---|---|
| M1–M4 contracts | `tests/test_m1_contract.py` through `tests/test_m4_contract.py` |
| Config, Options, Reauth, Import | `tests/test_config_flow.py`, `tests/test_options_flow.py`, `tests/test_reauth.py`, `tests/test_import.py` |
| Config Entry lifecycle | `tests/test_entry_lifecycle.py`, `tests/test_manifest.py` |
| matrix-nio compatibility | `tests/test_nio_compat.py` |
| Shared test doubles | `tests/fakes.py` |

Run the complete suite:

```bash
uv run python -m pytest
```

`FakeNio` cannot expose protocol-encoding differences between real clients. Changes to SAS, commitments, emoji, or MAC handling also require a manual end-to-end check with Element on a real homeserver; [SAS architecture](SAS_ARCHITECTURE.md) records this test boundary.

## CI quality checks

`.github/workflows/tests.yml` runs on every push and pull request:

- `lint`: `ruff check` and `ruff format --check`.
- `pytest`: the test suite with a minimum of 81% coverage.
- `audit`: extracts runtime dependencies from `manifest.json` and runs `pip-audit`.

Run the equivalent checks before pushing:

```bash
uv run python -m ruff check custom_components/ tests/
uv run python -m ruff format --check custom_components/ tests/
uv run python -m pytest --cov=custom_components.matrix_e2ee --cov-fail-under=81
uv run python -c "import json; print('\n'.join(json.load(open('custom_components/matrix_e2ee/manifest.json'))['requirements']))" > runtime-requirements.txt
uv run python -m pip_audit --requirement runtime-requirements.txt
rm runtime-requirements.txt
```

The coverage threshold is the current project quality baseline. Lowering it requires a documented reason; do not reduce it only to make CI pass.

## Local installation

Copy `custom_components/matrix_e2ee` to `custom_components/matrix_e2ee` under the Home Assistant configuration directory, then configure it through **Settings → Devices & Services → Add Integration → Matrix E2EE**.

A legacy `matrix_e2ee:` YAML block is imported into a Config Entry at startup. Session and crypto-store data live under `<config>/.storage/` and must persist with the Home Assistant configuration directory.

Do not install development dependencies into Home Assistant OS's system Python. Local development and tests use only the project virtual environment.

## Sensitive information

Tests, logs, and Git history must not contain:

- Matrix access tokens or account passwords
- pickle keys
- message bodies
- crypto-store contents
- real SAS transcripts or test credentials

Device public keys may be used for diagnostics, but avoid recording unrelated account or device information.

## Related documentation

- [SAS architecture](SAS_ARCHITECTURE.md)
- [matrix-nio compatibility](NIO_COMPAT.md)
- [Security model](../SECURITY.md)
