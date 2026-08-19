"""Homeserver URL parsing and normalization helpers."""

from __future__ import annotations

from urllib.parse import urlsplit

_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}


class HomeserverURLInvalid(ValueError):
    """Raised when a homeserver URL fails validation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _format_authority(scheme: str, host: str, port: int | None) -> str:
    """Render ``scheme://host[:port]``, re-bracketing IPv6 literals."""
    display = f"[{host}]" if ":" in host else host
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        return f"{scheme}://{display}:{port}"
    return f"{scheme}://{display}"


def homeserver_origin(url: str) -> str:
    """Return the canonical ``scheme://host[:port]`` origin of a homeserver URL.

    Path and trailing slash are ignored; default ports (443 for https, 80 for
    http) are dropped. Used to decide whether a reconfigure moves the bot to a
    different server (which requires a new device) or only tweaks the URL.
    """
    value = (url or "").strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not host:
        return value
    return _format_authority(scheme, host, parts.port)


def normalize_homeserver(url: str) -> str:
    """Validate and normalize a homeserver URL to ``scheme://host[:port]``.

    - Bare hosts default to ``https``.
    - Embedded credentials are rejected.
    - ``http`` is rejected unless the host is localhost/loopback.
    - Unsupported schemes are rejected.
    - Trailing slashes, paths, and default ports are stripped.

    Raises :class:`HomeserverURLInvalid` with a stable ``reason`` code:
    ``invalid``, ``credentials``, ``http_not_allowed``, or
    ``unsupported_scheme``.
    """
    value = (url or "").strip()
    if not value:
        raise HomeserverURLInvalid("invalid")
    if "://" not in value:
        value = "//" + value
    try:
        parts = urlsplit(value)
    except ValueError as err:
        raise HomeserverURLInvalid("invalid") from err
    if parts.username is not None or parts.password is not None:
        raise HomeserverURLInvalid("credentials")
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if not host:
        raise HomeserverURLInvalid("invalid")
    if scheme == "http":
        if host not in _LOCALHOST_HOSTS:
            raise HomeserverURLInvalid("http_not_allowed")
    elif scheme != "https":
        raise HomeserverURLInvalid("unsupported_scheme")
    return _format_authority(scheme, host, parts.port)
