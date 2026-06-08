"""Config & options flow for AKVO Movable Floor.

SAFETY-CRITICAL: ``enable_commands`` defaults to False. An operator must
explicitly set it to True in options after the project-specific register map
(``725-<project>``) has been verified against this integration's register-map
overrides AND on-hardware watchdog validation has passed.

Register-map overrides
----------------------
The options flow exposes an "advanced" step where installers can override any
register address or bit position to match their project-specific ``725-<project>``
Modbus map. Keys are stored in config-entry *data* (not options) as ``reg_*``
fields (e.g. ``reg_hr_status_1 = 10``). The integration reloads automatically
when options change so a new RegisterMap is built on the next setup.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
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
from .register_map import CONF_REG_PREFIX, RegisterMap


# Fields of RegisterMap that are user-overridable (integers only; drive_fault_bits
# and float scales are excluded from the UI — they are rarely project-specific).
_OVERRIDABLE_REG_FIELDS: list[str] = [
    f.name
    for f in dataclass_fields(RegisterMap)
    if isinstance(f.default, int)
]


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
        fields[
            vol.Optional(f"preset_{n}", default=existing.get(str(n), ""))
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


def _reg_map_schema(existing_data: dict[str, Any]) -> vol.Schema:
    """Schema for the optional register-map override step.

    Each overridable integer field is shown with its default value pre-filled
    from *existing_data* so repeat edits are non-destructive.
    """
    default_map = RegisterMap()
    schema_fields: dict[Any, Any] = {}
    for fname in _OVERRIDABLE_REG_FIELDS:
        conf_key = CONF_REG_PREFIX + fname
        default_val = existing_data.get(conf_key, getattr(default_map, fname))
        schema_fields[vol.Optional(conf_key, default=default_val)] = vol.All(
            int, vol.Range(min=0, max=65535)
        )
    return vol.Schema(schema_fields)


def _collect_reg_overrides(
    user_input: dict[str, Any], existing_data: dict[str, Any]
) -> dict[str, Any]:
    """Extract reg_* overrides that differ from the RegisterMap defaults.

    We persist only non-default values to keep entry data compact. On read,
    ``RegisterMap.from_config`` falls back to defaults for absent keys.
    """
    default_map = RegisterMap()
    overrides: dict[str, Any] = {}
    for fname in _OVERRIDABLE_REG_FIELDS:
        conf_key = CONF_REG_PREFIX + fname
        value = user_input.get(conf_key, existing_data.get(conf_key))
        if value is None:
            continue
        if value != getattr(default_map, fname):
            overrides[conf_key] = int(value)
    return overrides


class AkvoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the AKVO config flow (user -> presets -> register_map)."""

    VERSION = 1

    def __init__(self) -> None:
        self._conn: dict[str, Any] = {}
        self._options: dict[str, Any] = {}

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
            self._options = {
                CONF_PRESETS: _collect_presets(user_input),
                CONF_ENABLE_COMMANDS: user_input[CONF_ENABLE_COMMANDS],
            }
            return await self.async_step_register_map()
        return self.async_show_form(
            step_id="presets",
            data_schema=_preset_schema({}),
        )

    async def async_step_register_map(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional: override register addresses for project-specific map."""
        if user_input is not None:
            reg_overrides = _collect_reg_overrides(user_input, {})
            data = {**self._conn, **reg_overrides}
            return self.async_create_entry(
                title="AKVO Movable Floor",
                data=data,
                options=self._options,
            )
        return self.async_show_form(
            step_id="register_map",
            data_schema=_reg_map_schema({}),
            description_placeholders={},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AkvoOptionsFlow()


class AkvoOptionsFlow(OptionsFlow):
    """Edit preset names, the enable_commands safety gate, and register-map overrides."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._pending_options = {
                CONF_PRESETS: _collect_presets(user_input),
                CONF_ENABLE_COMMANDS: user_input[CONF_ENABLE_COMMANDS],
            }
            return await self.async_step_register_map()
        return self.async_show_form(
            step_id="init",
            data_schema=_preset_schema(dict(self.config_entry.options)),
        )

    async def async_step_register_map(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            reg_overrides = _collect_reg_overrides(
                user_input, dict(self.config_entry.data)
            )
            # Persist register overrides into config-entry DATA (not options).
            # We update both the options (presets/gate) and the data (map).
            # HA's update_listener will reload the entry which re-builds the map.
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **{
                        k: v
                        for k, v in self.config_entry.data.items()
                        if not k.startswith(CONF_REG_PREFIX)
                    },
                    **reg_overrides,
                },
            )
            return self.async_create_entry(title="", data=self._pending_options)
        return self.async_show_form(
            step_id="register_map",
            data_schema=_reg_map_schema(dict(self.config_entry.data)),
            description_placeholders={},
        )
