"""Config flow for the matrix_e2ee integration."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)

try:
    from homeassistant.config_entries import OptionsFlowWithReload
except ImportError:  # pragma: no cover — HA < 2025.2
    OptionsFlowWithReload = OptionsFlow  # type: ignore[assignment,misc]

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
import homeassistant.helpers.config_validation as cv

from .client import MatrixE2EEClient, MatrixE2EEError
from .const import (
    CONF_ALLOWED_ROOMS,
    CONF_ALLOWED_USERS,
    CONF_COMMAND_PREFIX,
    CONF_HOMESERVER,
    CONF_VERIFICATION_PEER_USERS,
    DEFAULT_COMMAND_PREFIX,
    DOMAIN,
    ERROR_LOGIN_FAILED,
    ERROR_PASSWORD_REQUIRED,
    ERROR_VERIFICATION_TIMEOUT,
    VERIFICATION_TIMEOUT_SECONDS,
)

# OptionsFlowWithReload schedules the entry reload automatically when options
# change. On older HA where it is absent we fall back to plain OptionsFlow and
# must reload the entry ourselves.
_OPTIONS_FLOW_AUTO_RELOAD = OptionsFlowWithReload is not OptionsFlow

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


def _format_emojis(emojis: list[list[str]] | None) -> str:
    """Render SAS emoji/number pairs for the compare step."""
    if not emojis:
        return ""
    return "\n".join(f"{emoji}  {name}" for emoji, name in emojis)


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
                CONF_VERIFICATION_PEER_USERS: import_info.get(
                    CONF_VERIFICATION_PEER_USERS, []
                ),
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
            verification_peer_users=[],
            command_prefix=DEFAULT_COMMAND_PREFIX,
            fire_event=lambda event_type, data: None,
            nio_client_factory=_NIO_CLIENT_FACTORY,
        )
        try:
            await client.async_start()
        finally:
            await client.async_stop()


class MatrixE2EEOptionsFlow(OptionsFlowWithReload):
    """Handle matrix_e2ee options: access controls, command prefix, verification."""

    def __init__(self) -> None:
        """Track the active verification transaction across steps."""
        self._txn: str | None = None

    def _client(self) -> MatrixE2EEClient | None:
        return self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

    def _snapshot(self) -> dict[str, Any] | None:
        if self._txn is None:
            return None
        client = self._client()
        if client is None:
            return None
        return client.sas_snapshot(self._txn)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["access_controls", "verify_device"],
        )

    async def async_step_access_controls(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit allowed_rooms / allowed_users / verification_peer_users / command_prefix."""
        if user_input is not None:
            result = self.async_create_entry(
                data={
                    CONF_ALLOWED_ROOMS: _csv_to_list(user_input[CONF_ALLOWED_ROOMS]),
                    CONF_ALLOWED_USERS: _csv_to_list(user_input[CONF_ALLOWED_USERS]),
                    CONF_VERIFICATION_PEER_USERS: _csv_to_list(
                        user_input[CONF_VERIFICATION_PEER_USERS]
                    ),
                    CONF_COMMAND_PREFIX: user_input[CONF_COMMAND_PREFIX],
                }
            )
            if not _OPTIONS_FLOW_AUTO_RELOAD:
                await self.hass.config_entries.async_schedule_reload(
                    self.config_entry.entry_id
                )
            return result
        options = self.config_entry.options
        return self.async_show_form(
            step_id="access_controls",
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
                        CONF_VERIFICATION_PEER_USERS,
                        default=",".join(options.get(CONF_VERIFICATION_PEER_USERS, [])),
                    ): cv.string,
                    vol.Optional(
                        CONF_COMMAND_PREFIX,
                        default=options.get(
                            CONF_COMMAND_PREFIX, DEFAULT_COMMAND_PREFIX
                        ),
                    ): cv.string,
                }
            ),
        )

    async def async_step_verify_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for a verification initiated from the user's Matrix client.

        The wizard does not initiate anything: the user verifies the bot's
        session from Element, and this step waits for the inbound SAS to appear.
        """
        client = self._client()
        if client is None:
            return self.async_abort(reason="no_client")
        return self.async_show_progress(
            step_id="wait_inbound",
            progress_action="wait_inbound",
            progress_task=self.hass.async_create_task(self._wait_for_inbound()),
        )

    async def async_step_wait_inbound(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Progress step: move to comparison once an inbound SAS is detected."""
        return self.async_show_progress_done(next_step_id="compare")

    async def async_step_compare(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the SAS emojis and ask the user to confirm a match."""
        if self._txn is None:
            return self.async_abort(reason="verification_timeout")
        snapshot = self._snapshot()
        if snapshot is None:
            return self.async_abort(reason="verification_failed")
        if snapshot["canceled"]:
            return self.async_abort(reason="verification_canceled")
        emojis = snapshot.get("emojis")
        if not emojis:
            return self.async_abort(reason="verification_timeout")
        return self.async_show_menu(
            step_id="compare",
            menu_options=["match", "mismatch"],
            description_placeholders={
                "emojis": _format_emojis(emojis),
                "user_id": snapshot.get("user_id") or "",
                "device_id": snapshot.get("device_id") or "",
            },
        )

    async def async_step_match(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the SAS match and wait for the peer's MAC."""
        client = self._client()
        txn = self._txn
        if client is None or txn is None:
            return self.async_abort(reason="verification_failed")
        snapshot = self._snapshot()
        if snapshot is not None and snapshot["canceled"]:
            return self.async_abort(reason="verification_canceled")
        try:
            await client.async_confirm_verification(txn)
        except MatrixE2EEError as err:
            if err.code == ERROR_VERIFICATION_TIMEOUT:
                return self.async_abort(reason="verification_timeout")
            return self.async_abort(reason="verification_failed")
        snapshot = self._snapshot()
        if snapshot is not None and snapshot["verified"]:
            self._txn = None
            return self.async_abort(reason="verification_complete")
        return self.async_show_progress(
            step_id="wait_done",
            progress_action="verify",
            progress_task=self.hass.async_create_task(self._wait_for_done()),
        )

    async def async_step_mismatch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cancel the SAS because the emojis do not match."""
        client = self._client()
        txn = self._txn
        self._txn = None
        if client is not None and txn is not None:
            try:
                await client.async_cancel_verification(txn)
            except MatrixE2EEError:
                pass
        return self.async_abort(reason="verification_canceled")

    async def async_step_wait_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Progress step: move to the finish check once the SAS settles."""
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report the final verification outcome."""
        snapshot = self._snapshot()
        self._txn = None
        if snapshot is not None and snapshot["verified"]:
            return self.async_abort(reason="verification_complete")
        if snapshot is not None and snapshot["canceled"]:
            return self.async_abort(reason="verification_canceled")
        return self.async_abort(reason="verification_timeout")

    async def _wait_for_inbound(self) -> None:
        """Poll until an inbound SAS shows emojis (or the wait times out)."""
        deadline = self.hass.loop.time() + VERIFICATION_TIMEOUT_SECONDS
        while self.hass.loop.time() < deadline:
            client = self._client()
            if client is None:
                return
            snapshot = client.latest_sas_snapshot()
            if snapshot is not None and snapshot.get("emojis"):
                self._txn = snapshot["transaction_id"]
                return
            await asyncio.sleep(0.25)

    async def _wait_for_done(self) -> None:
        """Poll until the verification reaches verified or canceled."""
        deadline = self.hass.loop.time() + VERIFICATION_TIMEOUT_SECONDS
        while self.hass.loop.time() < deadline:
            snapshot = self._snapshot()
            if snapshot is None or snapshot["verified"] or snapshot["canceled"]:
                return
            await asyncio.sleep(0.25)
