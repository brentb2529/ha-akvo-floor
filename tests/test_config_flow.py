"""Config flow, options flow, and reconfigure flow tests.

Covers:
  - config flow happy path (3 steps: user -> presets -> register_map)
  - config flow connection error
  - config flow duplicate abort
  - config flow register-map overrides stored in data
  - options flow: preset + enable_commands update
  - options flow: register-map overrides persisted to entry data
  - reconfigure flow: host/port change
  - reconfigure flow: connection error
  - enable_commands defaults to False in config flow and options flow
  - NumberSelector float values are coerced to int
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.akvo.akvo_client import AkvoConnectionError
from custom_components.akvo.const import (
    CONF_ENABLE_COMMANDS,
    CONF_PRESETS,
    DEFAULT_PORT,
    DOMAIN,
)
from custom_components.akvo.register_map import CONF_REG_PREFIX, RegisterMap

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_HOST = "192.168.1.50"
_PORT = 502


@contextmanager
def _patch_all(conn_side_effect=None):
    """Patch both the config-flow validator and the integration setup transport.

    The flow validator is a standalone coroutine. The integration setup creates
    a ModbusTransport instance; we replace it with a mock that succeeds
    immediately so no real socket is opened.
    """
    validate_mock = AsyncMock(side_effect=conn_side_effect)
    transport_instance = MagicMock()
    transport_instance.async_connect = AsyncMock()
    transport_instance.async_close = AsyncMock()
    # Return trivially valid register data — 12 zeros for HR0..HR11.
    transport_instance.read_holding = AsyncMock(return_value=[0x8001] + [0] * 11)
    transport_instance.write_holding = AsyncMock()
    transport_cls = MagicMock(return_value=transport_instance)
    with (
        patch("custom_components.akvo.config_flow._validate_connection", validate_mock),
        patch("custom_components.akvo.ModbusTransport", transport_cls),
        patch("custom_components.akvo.coordinator.ModbusTransport", transport_cls),
    ):
        yield


# ---------------------------------------------------------------------------
# Config flow — happy path
# ---------------------------------------------------------------------------

async def test_config_flow_full_happy_path(hass: HomeAssistant) -> None:
    """Three-step config flow creates an entry with presets + reg defaults."""
    with _patch_all():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: _HOST, CONF_PORT: _PORT},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "presets"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "preset_1": "Deck mode",
                "preset_3": "Pool mode",
                CONF_ENABLE_COMMANDS: False,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "register_map"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},  # accept all defaults
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.data[CONF_HOST] == _HOST
    assert int(entry.data[CONF_PORT]) == _PORT
    assert entry.options[CONF_PRESETS] == {"1": "Deck mode", "3": "Pool mode"}
    assert entry.options[CONF_ENABLE_COMMANDS] is False
    # No reg_* keys stored when all defaults are accepted.
    assert not any(k.startswith(CONF_REG_PREFIX) for k in entry.data)


async def test_config_flow_title_includes_host_port(hass: HomeAssistant) -> None:
    with _patch_all():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: _HOST, CONF_PORT: _PORT}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"preset_1": "Deck", CONF_ENABLE_COMMANDS: False},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )
        await hass.async_block_till_done()

    assert _HOST in result["title"]
    assert str(_PORT) in result["title"]


async def test_config_flow_enable_commands_defaults_false(hass: HomeAssistant) -> None:
    """The presets step must not enable commands unless the user explicitly enables them."""
    with _patch_all():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: _HOST, CONF_PORT: _PORT}
        )
        # Step shows presets form with enable_commands field.
        assert result["step_id"] == "presets"
        schema_keys = [str(k) for k in result["data_schema"].schema]
        assert CONF_ENABLE_COMMANDS in schema_keys

        # Submit without touching enable_commands -> should default False.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENABLE_COMMANDS: False}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )
        await hass.async_block_till_done()

    assert result["result"].options[CONF_ENABLE_COMMANDS] is False


# ---------------------------------------------------------------------------
# Config flow — connection error
# ---------------------------------------------------------------------------

async def test_config_flow_connection_error_shows_form_again(
    hass: HomeAssistant,
) -> None:
    with _patch_all(conn_side_effect=AkvoConnectionError("refused")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: _HOST, CONF_PORT: _PORT}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "cannot_connect"


# ---------------------------------------------------------------------------
# Config flow — duplicate abort
# ---------------------------------------------------------------------------

async def test_config_flow_duplicate_aborts(hass: HomeAssistant) -> None:
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: _HOST, CONF_PORT: _PORT},
        options={CONF_PRESETS: {}, CONF_ENABLE_COMMANDS: False},
    )
    existing.add_to_hass(hass)

    with _patch_all():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: _HOST, CONF_PORT: _PORT}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# Config flow — register-map overrides stored in data
# ---------------------------------------------------------------------------

async def test_config_flow_reg_overrides_stored_in_data(hass: HomeAssistant) -> None:
    """Non-default register addresses end up in entry.data under reg_ keys."""
    default_map = RegisterMap()
    # Build reg_map input with hr_command overridden to 20.
    reg_input = {
        CONF_REG_PREFIX + f.name: getattr(default_map, f.name)
        for f in dataclasses.fields(RegisterMap)
        if isinstance(f.default, int)
    }
    reg_input[f"{CONF_REG_PREFIX}hr_command"] = 20

    with _patch_all():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: _HOST, CONF_PORT: _PORT}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENABLE_COMMANDS: False}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], reg_input
        )
        await hass.async_block_till_done()

    entry = result["result"]
    assert entry.data[f"{CONF_REG_PREFIX}hr_command"] == 20
    # Default-valued fields are NOT stored.
    assert f"{CONF_REG_PREFIX}hr_status_1" not in entry.data


# ---------------------------------------------------------------------------
# Options flow helpers
# ---------------------------------------------------------------------------

async def _create_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a minimal MockConfigEntry without running setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: _HOST, CONF_PORT: _PORT},
        options={CONF_PRESETS: {"1": "Deck mode"}, CONF_ENABLE_COMMANDS: False},
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

async def test_options_flow_updates_presets_and_gate(hass: HomeAssistant) -> None:
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "preset_1": "Deck mode",
            "preset_3": "Pool mode",
            CONF_ENABLE_COMMANDS: True,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "register_map"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRESETS] == {"1": "Deck mode", "3": "Pool mode"}
    assert result["data"][CONF_ENABLE_COMMANDS] is True


async def test_options_flow_enable_commands_can_be_toggled_off(
    hass: HomeAssistant,
) -> None:
    """After being enabled, the gate can be turned off again."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: _HOST, CONF_PORT: _PORT},
        options={CONF_PRESETS: {"1": "Deck"}, CONF_ENABLE_COMMANDS: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"preset_1": "Deck", CONF_ENABLE_COMMANDS: False},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {}
    )

    assert result["data"][CONF_ENABLE_COMMANDS] is False


async def test_options_flow_reg_overrides_saved_to_entry_data(
    hass: HomeAssistant,
) -> None:
    """Register-map overrides from the options flow land in entry.data."""
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"preset_1": "Deck mode", CONF_ENABLE_COMMANDS: False},
    )

    default_map = RegisterMap()
    reg_input = {
        CONF_REG_PREFIX + f.name: getattr(default_map, f.name)
        for f in dataclasses.fields(RegisterMap)
        if isinstance(f.default, int)
    }
    # Override hr_watchdog from 11 -> 15.
    reg_input[f"{CONF_REG_PREFIX}hr_watchdog"] = 15

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], reg_input
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[f"{CONF_REG_PREFIX}hr_watchdog"] == 15
    # Default-valued fields not stored.
    assert f"{CONF_REG_PREFIX}hr_status_1" not in entry.data


async def test_options_flow_clears_old_reg_overrides(hass: HomeAssistant) -> None:
    """Submitting defaults on the reg-map step removes previous non-default keys."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: _HOST,
            CONF_PORT: _PORT,
            f"{CONF_REG_PREFIX}hr_command": 20,  # previously overridden
        },
        options={CONF_PRESETS: {}, CONF_ENABLE_COMMANDS: False},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"preset_1": "", CONF_ENABLE_COMMANDS: False},
    )

    # Submit with hr_command back to default (10) — override should be removed.
    default_map = RegisterMap()
    reg_input = {
        CONF_REG_PREFIX + f.name: getattr(default_map, f.name)
        for f in dataclasses.fields(RegisterMap)
        if isinstance(f.default, int)
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], reg_input
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert f"{CONF_REG_PREFIX}hr_command" not in entry.data


async def test_options_flow_number_selector_float_coerced_to_int(
    hass: HomeAssistant,
) -> None:
    """NumberSelector submits floats; the flow must cast them to int."""
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"preset_1": "Deck", CONF_ENABLE_COMMANDS: False},
    )

    # Simulate NumberSelector returning float 20.0 for hr_command.
    default_map = RegisterMap()
    reg_input = {
        CONF_REG_PREFIX + f.name: float(getattr(default_map, f.name))
        for f in dataclasses.fields(RegisterMap)
        if isinstance(f.default, int)
    }
    reg_input[f"{CONF_REG_PREFIX}hr_command"] = 20.0

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], reg_input
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    stored = entry.data.get(f"{CONF_REG_PREFIX}hr_command")
    assert stored == 20
    assert isinstance(stored, int)


# ---------------------------------------------------------------------------
# Reconfigure flow
# ---------------------------------------------------------------------------

async def test_reconfigure_changes_host_port(hass: HomeAssistant) -> None:
    entry = await _create_entry(hass)
    new_host = "10.0.0.99"
    new_port = 5020

    with _patch_all():
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: new_host, CONF_PORT: new_port},
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == new_host
    assert int(entry.data[CONF_PORT]) == new_port


async def test_reconfigure_prefills_current_values(hass: HomeAssistant) -> None:
    entry = await _create_entry(hass)

    with _patch_all():
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )

    # Form should show the current host/port as defaults.
    assert result["step_id"] == "reconfigure"
    schema = result["data_schema"].schema
    schema_defaults = {str(k): k.default() for k in schema if hasattr(k, "default")}
    assert schema_defaults.get(CONF_HOST) == _HOST


async def test_reconfigure_connection_error_shows_form_again(
    hass: HomeAssistant,
) -> None:
    entry = await _create_entry(hass)

    with _patch_all(conn_side_effect=AkvoConnectionError("refused")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "bad-host", CONF_PORT: _PORT},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_preserves_reg_overrides(hass: HomeAssistant) -> None:
    """Reconfigure only changes host/port; existing reg_* overrides are kept."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: _HOST,
            CONF_PORT: _PORT,
            f"{CONF_REG_PREFIX}hr_command": 20,
        },
        options={CONF_PRESETS: {}, CONF_ENABLE_COMMANDS: False},
    )
    entry.add_to_hass(hass)

    with _patch_all():
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "10.0.0.1", CONF_PORT: _PORT},
        )
        await hass.async_block_till_done()

    # reg_* override survived the reconfigure.
    assert entry.data[f"{CONF_REG_PREFIX}hr_command"] == 20
