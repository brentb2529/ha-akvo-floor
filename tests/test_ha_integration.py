"""Home Assistant integration tests (config flow, entity setup, select gate).

Uses pytest-homeassistant-custom-component. The Modbus transport is patched to
talk to an in-process FakeAkvoState so no socket / hardware is involved.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.akvo.const import (
    CONF_ENABLE_COMMANDS,
    CONF_PRESETS,
    DOMAIN,
)

from tests.conftest import FakeTransport
from fake.fake_akvo_server import FakeAkvoState

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

PRESETS = {"3": "Pool mode", "1": "Deck mode"}


def _patched_transport(state: FakeAkvoState):
    """Patch ModbusTransport so HA uses the in-process fake."""
    transport = FakeTransport(state)

    class _Wrap:
        async def async_connect(self) -> None:
            return None

        async def async_close(self) -> None:
            return None

        async def read_holding(self, address: int, count: int):
            return await transport.read(address, count)

        async def write_holding(self, address: int, value: int) -> None:
            await transport.write(address, value)

    return _Wrap()


async def _add_entry(
    hass: HomeAssistant, state: FakeAkvoState, *, enable_commands: bool
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "fake", CONF_PORT: 5020},
        options={CONF_PRESETS: PRESETS, CONF_ENABLE_COMMANDS: enable_commands},
    )
    entry.add_to_hass(hass)
    wrap = _patched_transport(state)
    with (
        patch(
            "custom_components.akvo.ModbusTransport", return_value=wrap
        ),
        patch(
            "custom_components.akvo.coordinator.ModbusTransport", return_value=wrap
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_entities_created_and_populated(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    state.regs[6] = 1500  # main 1.5 m
    state.regs[8] = 425  # 4.25 A
    await _add_entry(hass, state, enable_commands=False)

    pos = hass.states.get("sensor.akvo_movable_floor_main_floor_position")
    assert pos is not None
    assert float(pos.state) == pytest.approx(1.5, abs=0.05)

    ready = hass.states.get("binary_sensor.akvo_movable_floor_system_ready")
    assert ready is not None
    assert ready.state == "on"

    sel = hass.states.get("select.akvo_movable_floor_configuration_request")
    assert sel is not None
    assert "Pool mode" in sel.attributes["options"]
    assert "Deck mode" in sel.attributes["options"]


async def test_select_rejects_when_commands_disabled(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    await _add_entry(hass, state, enable_commands=False)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": "select.akvo_movable_floor_configuration_request",
                "option": "Pool mode",
            },
            blocking=True,
        )
    # HR10 never written.
    assert state.regs[10] == 0


async def test_select_command_completes_when_enabled(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    await _add_entry(hass, state, enable_commands=True)

    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.akvo_movable_floor_configuration_request",
            "option": "Pool mode",  # config #3
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    # config #3 achieved bit (bit4) set on the fake.
    assert state.regs[1] & (1 << 4)
    # command cleared.
    assert state.regs[10] == 0


async def test_select_rejected_when_estop(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    state.inject_estop(indoor=True)
    await _add_entry(hass, state, enable_commands=True)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": "select.akvo_movable_floor_configuration_request",
                "option": "Pool mode",
            },
            blocking=True,
        )
    assert state.regs[10] == 0
