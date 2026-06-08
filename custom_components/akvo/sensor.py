"""AKVO sensors: positions, motor currents, active configuration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfElectricCurrent, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .akvo_client import AkvoState
from .const import CONF_PRESETS, PRESET_NONE
from .coordinator import AkvoConfigEntry, AkvoCoordinator
from .entity import AkvoEntity


@dataclass(frozen=True, kw_only=True)
class AkvoSensorDescription(SensorEntityDescription):
    """Describes an AKVO sensor."""

    value_fn: Callable[[AkvoState], float | str | None]


SENSORS: tuple[AkvoSensorDescription, ...] = (
    AkvoSensorDescription(
        key="main_floor_position",
        translation_key="main_floor_position",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda s: s.main_position_m,
    ),
    AkvoSensorDescription(
        key="baja_position",
        translation_key="baja_position",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda s: s.baja_position_m,
    ),
    AkvoSensorDescription(
        key="main_floor_motor_current",
        translation_key="main_floor_motor_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda s: s.main_current_a,
    ),
    AkvoSensorDescription(
        key="baja_motor_current",
        translation_key="baja_motor_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda s: s.baja_current_a,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AkvoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AKVO sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        AkvoSensor(coordinator, desc) for desc in SENSORS
    ]
    entities.append(AkvoActiveConfigurationSensor(coordinator))
    async_add_entities(entities)


class AkvoSensor(AkvoEntity, SensorEntity):
    """A scalar AKVO sensor."""

    entity_description: AkvoSensorDescription

    def __init__(
        self, coordinator: AkvoCoordinator, description: AkvoSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        return self.entity_description.value_fn(self.coordinator.data)


class AkvoActiveConfigurationSensor(AkvoEntity, SensorEntity):
    """Active configuration derived from HR1 achieved bits."""

    _attr_translation_key = "active_configuration"

    def __init__(self, coordinator: AkvoCoordinator) -> None:
        super().__init__(coordinator, "active_configuration")
        self._presets: dict[str, str] = coordinator.config_entry.options.get(
            CONF_PRESETS, {}
        )

    @property
    def native_value(self) -> str:
        cfg = self.coordinator.data.active_configuration
        if cfg is None:
            return PRESET_NONE
        return self._presets.get(str(cfg), f"Configuration #{cfg}")
