"""Config, options, and reconfigure flows for AKVO Movable Floor.

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
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

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

# Selector for 16-bit register addresses / bit positions (0..65535).
_REG_SELECTOR = NumberSelector(
    NumberSelectorConfig(min=0, max=65535, step=1, mode=NumberSelectorMode.BOX)
)

# Selector for preset name strings (plain single-line text).
_TEXT_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))


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
    Uses TextSelector for proper UI text inputs and BooleanSelector for the
    safety gate toggle.
    """
    fields: dict[Any, Any] = {}
    existing: dict[str, str] = defaults.get(CONF_PRESETS, {})
    for n in range(MIN_CONFIG, MAX_CONFIG + 1):
        fields[
            vol.Optional(f"preset_{n}", default=existing.get(str(n), ""))
        ] = _TEXT_SELECTOR
    fields[
        vol.Required(
            CONF_ENABLE_COMMANDS,
            default=defaults.get(CONF_ENABLE_COMMANDS, DEFAULT_ENABLE_COMMANDS),
        )
    ] = BooleanSelector()
    return vol.Schema(fields)


def _collect_presets(user_input: dict[str, Any]) -> dict[str, str]:
    presets: dict[str, str] = {}
    for n in range(MIN_CONFIG, MAX_CONFIG + 1):
        name = (user_input.get(f"preset_{n}") or "").strip()
        if name:
            presets[str(n)] = name
    return presets


def _reg_map_schema(existing_data: dict[str, Any]) -> vol.Schema:
    """Schema for the register-map override step.

    Each overridable integer field is shown with its default value pre-filled
    from *existing_data* so repeat edits are non-destructive. Uses
    NumberSelector for proper numeric UI inputs with range validation.
    """
    default_map = RegisterMap()
    schema_fields: dict[Any, Any] = {}
    for fname in _OVERRIDABLE_REG_FIELDS:
        conf_key = CONF_REG_PREFIX + fname
        default_val = existing_data.get(conf_key, getattr(default_map, fname))
        schema_fields[
            vol.Optional(conf_key, default=int(default_val))
        ] = _REG_SELECTOR
    return vol.Schema(schema_fields)


def _collect_reg_overrides(
    user_input: dict[str, Any], existing_data: dict[str, Any]
) -> dict[str, Any]:
    """Extract reg_* overrides that differ from the RegisterMap defaults.

    We persist only non-default values to keep entry data compact. On read,
    ``RegisterMap.from_config`` falls back to defaults for absent keys.
    NumberSelector returns floats; cast to int here.
    """
    default_map = RegisterMap()
    overrides: dict[str, Any] = {}
    for fname in _OVERRIDABLE_REG_FIELDS:
        conf_key = CONF_REG_PREFIX + fname
        raw = user_input.get(conf_key, existing_data.get(conf_key))
        if raw is None:
            continue
        value = int(raw)
        if value != getattr(default_map, fname):
            overrides[conf_key] = value
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
            port = int(user_input[CONF_PORT])
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
                vol.Required(CONF_HOST): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
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
                CONF_ENABLE_COMMANDS: bool(user_input[CONF_ENABLE_COMMANDS]),
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
            host = self._conn[CONF_HOST]
            port = int(self._conn[CONF_PORT])
            data = {CONF_HOST: host, CONF_PORT: port, **reg_overrides}
            return self.async_create_entry(
                title=f"AKVO Movable Floor ({host}:{port})",
                data=data,
                options=self._options,
            )
        return self.async_show_form(
            step_id="register_map",
            data_schema=_reg_map_schema({}),
            description_placeholders={},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow changing host/port without deleting the entry.

        All entity history and automations are preserved because unique_ids are
        based on entry_id, not on host/port.
        """
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = int(user_input[CONF_PORT])
            try:
                await _validate_connection(host, port)
            except AkvoConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_HOST: host,
                        CONF_PORT: port,
                    },
                    title=f"AKVO Movable Floor ({host}:{port})",
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=entry.data.get(CONF_HOST, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(
                    CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AkvoOptionsFlow()


class AkvoOptionsFlow(OptionsFlow):
    """Edit preset names, the enable_commands safety gate, and register-map overrides."""

    def __init__(self) -> None:
        self._pending_options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._pending_options = {
                CONF_PRESETS: _collect_presets(user_input),
                CONF_ENABLE_COMMANDS: bool(user_input[CONF_ENABLE_COMMANDS]),
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
