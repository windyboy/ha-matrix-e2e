"""Config flow tests using the Home Assistant test harness. No real credentials."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matrix_e2ee import config_flow
from custom_components.matrix_e2ee.const import (
    CONF_ALLOWED_ROOMS,
    CONF_ALLOWED_USERS,
    CONF_COMMAND_PREFIX,
    CONF_HOMESERVER,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFICATION_PEER_USERS,
    DOMAIN,
)
from tests.fakes import FakeNio

HS = "https://matrix.example.org"
USERNAME = "@ha-bot:example.org"
PASSWORD = "pw"


@pytest.fixture
def hass_config_dir(tmp_path) -> str:
    """Isolate the config dir per test so login sessions do not leak."""
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations) -> None:
    """Make HA's loader discover the custom_components package."""


@pytest.fixture(autouse=True)
def _inject_fake_nio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_flow, "_NIO_CLIENT_FACTORY", FakeNio)


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    assert result["type"] == "create_entry"
    assert result["title"] == USERNAME
    assert result["data"] == {CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME}
    # Password must never be persisted in the entry data.
    assert CONF_PASSWORD not in result["data"]


async def test_user_flow_bad_password_shows_error(hass: HomeAssistant) -> None:
    def failing_factory(homeserver, user, **kwargs):
        nio = FakeNio(homeserver, user, **kwargs)
        nio.login_should_fail = True
        return nio

    config_flow._NIO_CLIENT_FACTORY = failing_factory
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_duplicate_aborts(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "single_instance_allowed"


async def test_user_flow_second_entry_different_username_aborts(
    hass: HomeAssistant,
) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="@other-bot:example.org",
        data={CONF_HOMESERVER: HS, CONF_USERNAME: "@other-bot:example.org"},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "single_instance_allowed"


async def test_user_flow_normalizes_homeserver(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOMESERVER: "https://matrix.example.org/",
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
        },
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_HOMESERVER: "https://matrix.example.org",
        CONF_USERNAME: USERNAME,
    }


async def test_user_flow_rejects_http(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOMESERVER: "http://evil.example",
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
        },
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "homeserver_http_not_allowed"}


async def test_user_flow_rejects_embedded_credentials(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOMESERVER: "https://user:token@matrix.example.org",
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
        },
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "homeserver_credentials"}


def test_format_emojis_empty() -> None:
    assert config_flow._format_emojis(None) == ""
    assert config_flow._format_emojis([]) == ""


def test_format_emojis_numbers_seven_pairs() -> None:
    emojis = [
        ["🐶", "Dog"],
        ["🐱", "Cat"],
        ["🦁", "Lion"],
        ["🐎", "Horse"],
        ["🦄", "Unicorn"],
        ["🐷", "Pig"],
        ["🐘", "Elephant"],
    ]
    rendered = config_flow._format_emojis(emojis)
    lines = rendered.split("\n")
    assert len(lines) == 7
    assert lines[0] == "1. 🐶  Dog"
    assert lines[3] == "4. 🐎  Horse"
    assert lines[6] == "7. 🐘  Elephant"


def test_format_emojis_numbers_from_one() -> None:
    rendered = config_flow._format_emojis([["⚓", "Anchor"], ["☎️", "Telephone"]])
    assert rendered == "1. ⚓  Anchor\n2. ☎️  Telephone"


async def test_import_creates_entry_with_options(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={
            CONF_HOMESERVER: HS,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_ALLOWED_ROOMS: ["!room:example.org"],
            CONF_ALLOWED_USERS: ["@admin:example.org"],
            CONF_COMMAND_PREFIX: "!",
        },
    )
    assert result["type"] == "create_entry"
    assert result["title"] == USERNAME
    assert result["data"] == {CONF_HOMESERVER: HS, CONF_USERNAME: USERNAME}
    assert result["options"] == {
        CONF_ALLOWED_ROOMS: ["!room:example.org"],
        CONF_ALLOWED_USERS: ["@admin:example.org"],
        CONF_VERIFICATION_PEER_USERS: [],
        CONF_COMMAND_PREFIX: "!",
    }
