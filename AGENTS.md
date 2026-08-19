# Project Instructions

## Running Python

The project's `.venv` contains the Python interpreter (Python 3.14, matching CI).
Run Python through `uv run` so it uses the interpreter inside this venv. Never
invoke `.venv/bin/python` directly.

```bash
uv run python --version        # must print Python 3.14.x
uv run python -m pytest
uv run python -c "import homeassistant; print('ok')"
```

### How uv picks the interpreter

- uv auto-discovers a `.venv` directory in the current directory / project root
  and uses its Python — even when the project has no `pyproject.toml` (verified:
  this project has none). A symlinked `.venv` works the same way.
- If no `.venv` is present (e.g. a git worktree without one), uv falls back to a
  throwaway managed interpreter (e.g. Python 3.13) that lacks the project
  dependencies — `pytest` is missing and tests fail.
- `VIRTUAL_ENV=/path/to/venv uv run ...` forces uv to use that venv regardless of
  `.venv` presence. `uv run --active` also prefers the `VIRTUAL_ENV` venv.

To run in a worktree, symlink the project's venv so uv keeps using it:

```bash
ln -sfn /path/to/project/.venv <worktree>/.venv
uv run python --version        # must still print Python 3.14.x
```

## Tests and SAS changes

Run a subset when iterating:

```bash
uv run python -m pytest tests/test_nio_compat.py
uv run python -m pytest tests/test_m3_contract.py tests/test_options_flow.py
```

Changes to SAS, commitments, emoji, or MAC handling also require a manual end-to-end verification with Element on a real homeserver. See [docs/NIO_COMPAT.md](docs/NIO_COMPAT.md) and [docs/SAS_ARCHITECTURE.md](docs/SAS_ARCHITECTURE.md).

When editing documentation, keep English and Chinese counterparts in sync where both exist. Runtime dependency pins live only in `manifest.json` and `requirements-dev.txt` — do not duplicate full requirement lists in docs.
