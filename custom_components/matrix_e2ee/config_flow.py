"""Config flow for the matrix_e2ee integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
import homeassistant.helpers.config_validation as cv

from .client import MatrixE2EEClient, MatrixE2EEError
from .const import (
    CONF_ALLOWED_ROOMS,
    CONF_ALLOWED_USERS,
    CONF_COMMAND_PREFIX,
    CONF_HOMESERVER,
    DEFAULT_COMMAND_PREFIX,
    DOMAIN,
    ERROR_LOGIN_FAILED,
    ERROR_PASSWORD_REQUIRED,
)

# Tests patch this to inject a fake nio client (no real network or crypto).
_NIO_CLIENT_FACTORY: Any = None

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOMESERVER): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): cv.string})


def _base_error(code: str) -> str:
    if code in (ERROR_LOGIN_FAILED, ERROR_PASSWORD_REQUIRED):
        return "invalid_auth"
    return "cannot_connect"


def _csv_to_list(value: str) -> list[str]:
    """Split a comma-separated string into a trimmed, non-empty list."""
    return [item.strip() for item in value.split(",") if item.strip()]


class MatrixE2EEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for matrix_e2ee."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect homeserver / username / password, then test the login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._ensure_login(user_input)
            except MatrixE2EEError as err:
                errors["base"] = _base_error(err.code)
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_HOMESERVER: user_input[CONF_HOMESERVER],
                        CONF_USERNAME: user_input[CONF_USERNAME],
                    },
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_import(
        self, import_info: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Migrate a YAML configuration into a ConfigEntry."""
        if import_info is None:
            return self.async_abort(reason="unknown")
        username = import_info[CONF_USERNAME]
        try:
            await self._ensure_login(
                {
                    CONF_HOMESERVER: import_info[CONF_HOMESERVER],
                    CONF_USERNAME: username,
                    CONF_PASSWORD: import_info.get(CONF_PASSWORD),
                }
            )
        except MatrixE2EEError:
            return self.async_abort(reason="login_failed")
        await self.async_set_unique_id(username)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=username,
            data={
                CONF_HOMESERVER: import_info[CONF_HOMESERVER],
                CONF_USERNAME: username,
            },
            options={
                CONF_ALLOWED_ROOMS: import_info.get(CONF_ALLOWED_ROOMS, []),
                CONF_ALLOWED_USERS: import_info.get(CONF_ALLOWED_USERS, []),
                CONF_COMMAND_PREFIX: import_info.get(
                    CONF_COMMAND_PREFIX, DEFAULT_COMMAND_PREFIX
                ),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow editing the homeserver (username stays read-only)."""
        entry = self._get_reconfigure_entry()
        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            CONF_HOMESERVER, default=entry.data[CONF_HOMESERVER]
                        ): cv.string,
                    }
                ),
            )
        return self.async_update_reload_and_abort(
            entry, data_updates={CONF_HOMESERVER: user_input[CONF_HOMESERVER]}
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for editing access controls."""
        return MatrixE2EEOptionsFlow()

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-authenticate a soft-logged-out client using the running instance."""
        errors: dict[str, str] = {}
        if user_input is not None and CONF_PASSWORD in user_input:
            client = self.hass.data[DOMAIN][self.context["entry_id"]]
            try:
                await client.async_reauthenticate(user_input[CONF_PASSWORD])
            except MatrixE2EEError as err:
                errors["base"] = _base_error(err.code)
            else:
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth", data_schema=REAUTH_SCHEMA, errors=errors
        )

    async def _ensure_login(self, user_input: dict[str, Any]) -> None:
        """Log in (or restore an existing session) and persist the device.

        This is the test-before-configure step: the session file is only
        written after a successful first login.
        """
        client = MatrixE2EEClient(
            config_dir=self.hass.config.path(),
            homeserver=user_input[CONF_HOMESERVER],
            username=user_input[CONF_USERNAME],
            password=user_input.get(CONF_PASSWORD),
            allowed_rooms=[],
            allowed_users=[],
            command_prefix=DEFAULT_COMMAND_PREFIX,
            fire_event=lambda event_type, data: None,
            nio_client_factory=_NIO_CLIENT_FACTORY,
        )
        try:
            await client.async_start()
        finally:
            await client.async_stop()


class MatrixE2EEOptionsFlow(OptionsFlowWithReload):
    """Handle matrix_e2ee options: access controls and command prefix."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit allowed_rooms / allowed_users / command_prefix."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_ALLOWED_ROOMS: _csv_to_list(user_input[CONF_ALLOWED_ROOMS]),
                    CONF_ALLOWED_USERS: _csv_to_list(user_input[CONF_ALLOWED_USERS]),
                    CONF_COMMAND_PREFIX: user_input[CONF_COMMAND_PREFIX],
                }
            )
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ALLOWED_ROOMS,
                        default=",".join(options.get(CONF_ALLOWED_ROOMS, [])),
                    ): cv.string,
                    vol.Optional(
                        CONF_ALLOWED_USERS,
                        default=",".join(options.get(CONF_ALLOWED_USERS, [])),
                    ): cv.string,
                    vol.Optional(
                        CONF_COMMAND_PREFIX,
                        default=options.get(CONF_COMMAND_PREFIX, DEFAULT_COMMAND_PREFIX),
                    ): cv.string,
                }
            ),
        )
