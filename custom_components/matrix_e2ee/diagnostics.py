"""Diagnostics platform for matrix_e2ee.

Returns a redacted snapshot of the bot session and crypto store for the Home
Assistant "Download diagnostics" feature. Never includes access tokens, pickle
keys, passwords, message bodies, or crypto material.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN


def _nio_version() -> str | None:
    """Return the installed matrix-nio version, if importable."""
    try:
        from importlib.metadata import version

        return version("matrix-nio")
    except Exception:  # noqa: BLE001 — diagnostics must never fail
        return None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a matrix_e2ee config entry."""
    client = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    diagnostics = client.safe_diagnostics() if client is not None else None

    manifest_version = None
    try:
        integration = await async_get_integration(hass, DOMAIN)
        manifest_version = integration.manifest.get("version")
    except Exception:  # noqa: BLE001 — diagnostics must not fail
        pass

    return {
        "integration_version": manifest_version,
        "nio_version": _nio_version(),
        "client": diagnostics,
    }
