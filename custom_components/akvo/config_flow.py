"""Config & options flow for AKVO Movable Floor."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .akvo_client import AkvoConnectionError
from .const import (
    CONF_ENABLE_COMMANDS,
    CONF_PRESETS,
    DEFAULT_ENABLE_COMMANDS,
    DEFAULT_PORT,
    DOMAIN,
    MAX_CONFIG,
    MIN_CONFIG,
)
from .modbus_transport import ModbusTransport


async def _validate_connection(host: str, port: int) -> None:
    transport = ModbusTransport(host, port)
    try:
        await transport.async_connect()
        await transport.read_holding(0, 1)
    finally:
        await transport.async_close()


def _preset_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for naming/enabling each of the 8 AKVO presets.

    Presets map AKVO config #N (1..8) to a friendly name (e.g. "Pool mode").
    Leaving a name blank means that preset is NOT offered in the select.
    """
    fields: dict[Any, Any] = {}
    existing: dict[str, str] = defaults.get(CONF_PRESETS, {})
    for n in range(MIN_CONFIG, MAX_CONFIG + 1):
        key = str(n)
        fields[
            vol.Optional(
                f"preset_{n}", default=existing.get(key, "")
            )
        ] = str
    fields[
        vol.Required(
            CONF_ENABLE_COMMANDS,
            default=defaults.get(CONF_ENABLE_COMMANDS, DEFAULT_ENABLE_COMMANDS),
        )
    ] = bool
    return vol.Schema(fields)


def _collect_presets(user_input: dict[str, Any]) -> dict[str, str]:
    presets: dict[str, str] = {}
    for n in range(MIN_CONFIG, MAX_CONFIG + 1):
        name = (user_input.get(f"preset_{n}") or "").strip()
        if name:
            presets[str(n)] = name
    return presets


class AkvoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the AKVO config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._conn: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
            try:
                await _validate_connection(host, port)
            except AkvoConnectionError:
                errors["base"] = "cannot_connect"
            else:
                self._conn = {CONF_HOST: host, CONF_PORT: port}
                return await self.async_step_presets()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_presets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = {
                CONF_PRESETS: _collect_presets(user_input),
                CONF_ENABLE_COMMANDS: user_input[CONF_ENABLE_COMMANDS],
            }
            return self.async_create_entry(
                title="AKVO Movable Floor",
                data=self._conn,
                options=options,
            )
        return self.async_show_form(
            step_id="presets",
            data_schema=_preset_schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AkvoOptionsFlow()


class AkvoOptionsFlow(OptionsFlow):
    """Edit preset names and the enable_commands safety gate."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = {
                CONF_PRESETS: _collect_presets(user_input),
                CONF_ENABLE_COMMANDS: user_input[CONF_ENABLE_COMMANDS],
            }
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=_preset_schema(dict(self.config_entry.options)),
        )
