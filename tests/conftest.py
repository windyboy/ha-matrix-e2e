"""Shared fixtures for matrix_e2ee tests. No real Matrix credentials."""

from __future__ import annotations

import sys
from pathlib import Path

# Load the Home Assistant custom-component test plugin (provides the `hass`
# fixture for Config Flow / Options Flow / Reauth tests). Installed via
# requirements-dev.txt.
pytest_plugins = "pytest_homeassistant_custom_component"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
