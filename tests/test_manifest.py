"""Regression: manifest.json must explicitly declare the matrix-nio[e2e] extra deps.

Home Assistant's requirement manager drops the ``[e2e]`` extra and only compares
the base package version. If these deps are not listed explicitly, HA skips
installing them and setup fails closed (W1N-140).
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "matrix_e2ee"
    / "manifest.json"
)

# matrix-nio 0.26.0 ``[e2e]`` extra dependencies (PyPI Requires-Dist).
E2EE_EXTRA_DEPS = {
    "vodozemac",
    "peewee",
    "cachetools",
    "atomicwrites",
}


def _base_name(requirement: str) -> str:
    name = requirement.split("[", 1)[0]
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if sep in name:
            name = name.split(sep, 1)[0]
            break
    return name.strip()


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_declares_matrix_nio_with_e2e_extra() -> None:
    manifest = _load_manifest()
    assert "matrix-nio[e2e]==0.26.0" in manifest["requirements"]


def test_manifest_explicitly_lists_e2ee_extra_dependencies() -> None:
    manifest = _load_manifest()
    names = {_base_name(req) for req in manifest["requirements"]}
    assert E2EE_EXTRA_DEPS <= names
