"""Unit tests for homeserver URL normalization and origin extraction."""

from __future__ import annotations

import pytest

from custom_components.matrix_e2ee.url import (
    HomeserverURLInvalid,
    homeserver_origin,
    normalize_homeserver,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://matrix.example.org/", "https://matrix.example.org"),
        ("https://matrix.example.org", "https://matrix.example.org"),
        ("matrix.example.org", "https://matrix.example.org"),
        ("https://matrix.example.org:8448", "https://matrix.example.org:8448"),
        ("https://matrix.example.org:443", "https://matrix.example.org"),
        ("http://localhost", "http://localhost"),
        ("http://localhost:8008", "http://localhost:8008"),
        ("http://127.0.0.1", "http://127.0.0.1"),
        ("http://[::1]:8008", "http://[::1]:8008"),
    ],
)
def test_normalize_homeserver_valid(raw: str, expected: str) -> None:
    assert normalize_homeserver(raw) == expected


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("http://evil.example", "http_not_allowed"),
        ("https://user:token@matrix.example.org", "credentials"),
        ("https://user@matrix.example.org", "credentials"),
        ("ftp://matrix.example.org", "unsupported_scheme"),
        ("", "invalid"),
        ("   ", "invalid"),
        ("https://", "invalid"),
    ],
)
def test_normalize_homeserver_rejected(raw: str, reason: str) -> None:
    with pytest.raises(HomeserverURLInvalid) as excinfo:
        normalize_homeserver(raw)
    assert excinfo.value.reason == reason


def test_origin_ignores_path_and_trailing_slash() -> None:
    assert homeserver_origin("https://matrix.example.org/") == (
        "https://matrix.example.org"
    )
    assert homeserver_origin("https://matrix.example.org/_matrix") == (
        "https://matrix.example.org"
    )
    assert homeserver_origin("https://matrix.example.org:443") == (
        "https://matrix.example.org"
    )


def test_origin_distinguishes_hosts() -> None:
    assert homeserver_origin("https://a.example.org") != homeserver_origin(
        "https://b.example.org"
    )
