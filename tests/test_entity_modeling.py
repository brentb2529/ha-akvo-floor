"""Tests for entity modeling: unique_id stability, device grouping, entity categories,
comm-loss -> unavailable, and configurable register map application via HA config entry.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.const import CONF_HOST, CONF_PORT, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.akvo.akvo_client import AkvoConnectionError
from custom_components.akvo.const import (
    CONF_ENABLE_COMMANDS,
    CONF_PRESETS,
    DOMAIN,
)
from custom_components.akvo.register_map import CONF_REG_PREFIX

from tests.conftest import FakeTransport
from fake.fake_akvo_server import FakeAkvoState

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

PRESETS = {"1": "Deck mode", "3": "Pool mode"}


def _patched_transport(state: FakeAkvoState):
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
    hass: HomeAssistant,
    state: FakeAkvoState,
    *,
    enable_commands: bool = False,
    extra_data: dict | None = None,
) -> MockConfigEntry:
    data: dict = {CONF_HOST: "fake", CONF_PORT: 5020}
    if extra_data:
        data.update(extra_data)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options={CONF_PRESETS: PRESETS, CONF_ENABLE_COMMANDS: enable_commands},
    )
    entry.add_to_hass(hass)
    wrap = _patched_transport(state)
    with (
        patch("custom_components.akvo.ModbusTransport", return_value=wrap),
        patch("custom_components.akvo.coordinator.ModbusTransport", return_value=wrap),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------
# Unique IDs are stable and entry_id-based
# ---------------------------------------------------------------------------

async def test_unique_ids_are_entry_id_scoped(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    entry = await _add_entry(hass, state)
    ent_reg = er.async_get(hass)
    # Position sensor unique_id
    entity = ent_reg.async_get(
        "sensor.akvo_movable_floor_main_floor_position"
    )
    assert entity is not None
    assert entity.unique_id == f"{entry.entry_id}_main_floor_position"


async def test_unique_ids_do_not_clash_across_two_entries(hass: HomeAssistant) -> None:
    """Two integrations for two different AKVO controllers must have distinct IDs."""
    state1 = FakeAkvoState()
    state2 = FakeAkvoState()
    entry1 = await _add_entry(hass, state1)
    # second entry needs a different host to avoid "already_configured" abort
    entry2 = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "fake2", CONF_PORT: 5020},
        options={CONF_PRESETS: {}, CONF_ENABLE_COMMANDS: False},
    )
    entry2.add_to_hass(hass)
    wrap2 = _patched_transport(state2)
    with (
        patch("custom_components.akvo.ModbusTransport", return_value=wrap2),
        patch("custom_components.akvo.coordinator.ModbusTransport", return_value=wrap2),
    ):
        assert await hass.config_entries.async_setup(entry2.entry_id)
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    e1 = ent_reg.async_get("sensor.akvo_movable_floor_main_floor_position")
    # The second device gets a disambiguated slug from HA
    e2 = ent_reg.async_get("sensor.akvo_movable_floor_main_floor_position_2")
    assert e1 is not None and e2 is not None
    assert e1.unique_id != e2.unique_id


# ---------------------------------------------------------------------------
# Device grouping
# ---------------------------------------------------------------------------

async def test_all_entities_under_same_device(hass: HomeAssistant) -> None:
    from homeassistant.helpers import device_registry as dr

    state = FakeAkvoState()
    await _add_entry(hass, state)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    device_ids = {
        e.device_id
        for e in ent_reg.entities.values()
        if e.platform == DOMAIN
    }
    assert len(device_ids) == 1, "All AKVO entities must share one device"
    dev = dev_reg.async_get(list(device_ids)[0])
    assert dev is not None
    assert dev.manufacturer == "AKVO"
    assert dev.model == "Spiralift"


# ---------------------------------------------------------------------------
# Entity categories
# ---------------------------------------------------------------------------

async def test_fault_sensors_are_diagnostic(hass: HomeAssistant) -> None:
    from homeassistant.const import EntityCategory

    state = FakeAkvoState()
    await _add_entry(hass, state)
    ent_reg = er.async_get(hass)

    # Spot-check a fault sensor
    fault_entity = ent_reg.async_get(
        "binary_sensor.akvo_movable_floor_main_floor_vfd_fault"
    )
    assert fault_entity is not None
    assert fault_entity.entity_category == EntityCategory.DIAGNOSTIC


async def test_primary_safety_sensors_have_no_category(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    await _add_entry(hass, state)
    ent_reg = er.async_get(hass)

    # estop_active is primary safety — no entity_category
    estop = ent_reg.async_get("binary_sensor.akvo_movable_floor_emergency_stop")
    assert estop is not None
    assert estop.entity_category is None

    # floors_moving — primary display
    moving = ent_reg.async_get("binary_sensor.akvo_movable_floor_floors_moving")
    assert moving is not None
    assert moving.entity_category is None


# ---------------------------------------------------------------------------
# Comm-loss -> entities become unavailable
# ---------------------------------------------------------------------------

async def test_comm_loss_makes_entities_unavailable(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    entry = await _add_entry(hass, state)
    coordinator = entry.runtime_data

    # Initially available
    pos = hass.states.get("sensor.akvo_movable_floor_main_floor_position")
    assert pos is not None
    assert pos.state != STATE_UNAVAILABLE

    # Simulate Modbus transport failure on the next poll
    async def _failing_read(address: int, count: int) -> list[int]:
        raise AkvoConnectionError("TCP timeout")

    coordinator.client._read = _failing_read
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    pos_after = hass.states.get("sensor.akvo_movable_floor_main_floor_position")
    assert pos_after is not None
    assert pos_after.state == STATE_UNAVAILABLE

    binary = hass.states.get("binary_sensor.akvo_movable_floor_system_fault")
    assert binary is not None
    assert binary.state == STATE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Configurable register map applied from config-entry data
# ---------------------------------------------------------------------------

async def test_custom_register_map_applied_from_entry_data(hass: HomeAssistant) -> None:
    """Entry data with reg_* overrides must be reflected in the live coordinator map."""
    state = FakeAkvoState()
    # Override a few addresses (still pointing at the default-layout fake, so
    # values match — this just tests the map is wired, not that decoding changes).
    extra_data = {
        f"{CONF_REG_PREFIX}hr_status_1": 0,
        f"{CONF_REG_PREFIX}hr_command": 10,
        f"{CONF_REG_PREFIX}topplate_fault_count": 8,
    }
    entry = await _add_entry(hass, state, extra_data=extra_data)
    coordinator = entry.runtime_data
    assert coordinator.client._map.topplate_fault_count == 8
    assert coordinator.client._map.hr_command == 10


async def test_default_map_applied_when_no_overrides(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    entry = await _add_entry(hass, state)
    coordinator = entry.runtime_data
    from custom_components.akvo.register_map import RegisterMap
    assert coordinator.client._map == RegisterMap()


# ---------------------------------------------------------------------------
# New binary sensors present (estop_indoor, estop_outdoor, control_key_on)
# ---------------------------------------------------------------------------

async def test_new_binary_sensors_present(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    await _add_entry(hass, state)
    for entity_id in (
        "binary_sensor.akvo_movable_floor_e_stop_indoor",
        "binary_sensor.akvo_movable_floor_e_stop_outdoor",
        "binary_sensor.akvo_movable_floor_control_key_on",
    ):
        entity = hass.states.get(entity_id)
        assert entity is not None, f"{entity_id} not found"


async def test_estop_indoor_state(hass: HomeAssistant) -> None:
    state = FakeAkvoState()
    state.inject_estop(indoor=True)
    await _add_entry(hass, state)
    indoor = hass.states.get("binary_sensor.akvo_movable_floor_e_stop_indoor")
    assert indoor is not None
    assert indoor.state == "on"
    outdoor = hass.states.get("binary_sensor.akvo_movable_floor_e_stop_outdoor")
    assert outdoor is not None
    assert outdoor.state == "off"
