"""Brand asset regression: icon/logo PNGs exist and manifest has no ``icon`` key.

Home Assistant 2026.3+ serves integration brand assets from the ``brand/``
directory via the brands proxy. Adding a manifest ``icon`` key is therefore
both unnecessary and discouraged for this integration.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "matrix_e2ee"
BRAND = COMPONENT / "brand"
MANIFEST = COMPONENT / "manifest.json"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# filename -> (width, height)
EXPECTED_IMAGES = {
    "icon.png": (256, 256),
    "icon@2x.png": (512, 512),
    "logo.png": (896, 256),
    "logo@2x.png": (1792, 512),
}


def _png_dimensions(data: bytes) -> tuple[int, int, int]:
    """Return (width, height, color_type) parsed from the PNG IHDR chunk."""
    assert data[:8] == PNG_SIGNATURE, "not a PNG file"
    assert data[12:16] == b"IHDR", "missing IHDR chunk"
    width, height = struct.unpack(">II", data[16:24])
    color_type = data[25]
    return width, height, color_type


def test_brand_assets_exist_with_expected_dimensions() -> None:
    for filename, (width, height) in EXPECTED_IMAGES.items():
        path = BRAND / filename
        assert path.is_file(), f"missing brand asset {path}"
        data = path.read_bytes()
        got_width, got_height, color_type = _png_dimensions(data)
        assert (got_width, got_height) == (width, height), f"bad size for {filename}"
        assert color_type == 6, f"{filename} must be RGBA (transparent background)"


def test_manifest_has_no_icon_key() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "icon" not in manifest
