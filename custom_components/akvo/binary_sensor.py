"""AKVO binary sensors: safety/status bits + decoded fault words.

Device classes used:
  - SAFETY   : e-stop (active means a safety device is tripped)
  - MOVING   : floors moving (active means physical motion in progress)
  - PROBLEM  : any fault or bad-comm condition (active means fault present)
  - (none)   : system_ready, ready_for_external, control_key_on — informational

Entity categories:
  - DIAGNOSTIC : fault sensors (high cardinality, installer-level detail) and
                 communication / ready-for-external status
  - (none/main): primary safety and operational status sensors
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .akvo_client import AkvoState
from .coordinator import AkvoConfigEntry, AkvoCoordinator
from .entity import AkvoEntity


@dataclass(frozen=True, kw_only=True)
class AkvoBinaryDescription(BinarySensorEntityDescription):
    """Describes an AKVO binary sensor."""

    value_fn: Callable[[AkvoState], bool]


# Primary safety/status sensors (no entity_category = shown in the main card)
STATUS_SENSORS: tuple[AkvoBinaryDescription, ...] = (
    AkvoBinaryDescription(
        key="system_ready",
        translation_key="system_ready",
        # No device_class: "system ready" is a summary indicator, not a standard class
        value_fn=lambda s: s.system_ready,
    ),
    AkvoBinaryDescription(
        key="system_fault",
        translation_key="system_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: s.system_fault,
    ),
    AkvoBinaryDescription(
        # Combined e-stop (either indoor OR outdoor tripped)
        key="estop_active",
        translation_key="estop_active",
        device_class=BinarySensorDeviceClass.SAFETY,
        value_fn=lambda s: s.estop_active,
    ),
    AkvoBinaryDescription(
        key="estop_indoor",
        translation_key="estop_indoor",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.estop_indoor,
    ),
    AkvoBinaryDescription(
        key="estop_outdoor",
        translation_key="estop_outdoor",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.estop_outdoor,
    ),
    AkvoBinaryDescription(
        key="floors_moving",
        translation_key="floors_moving",
        device_class=BinarySensorDeviceClass.MOVING,
        value_fn=lambda s: s.floors_moving,
    ),
    AkvoBinaryDescription(
        key="bad_modbus_comm",
        translation_key="bad_modbus_comm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.bad_modbus_comm,
    ),
    AkvoBinaryDescription(
        key="ready_for_external",
        translation_key="ready_for_external",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.ready_for_external,
    ),
    AkvoBinaryDescription(
        key="control_key_on",
        translation_key="control_key_on",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.control_key_on,
    ),
)


def _drive_fault_descriptions(
    group: str, accessor: Callable[[AkvoState], dict[str, bool]]
) -> list[AkvoBinaryDescription]:
    """Build per-bit fault sensors for a drive group (main or baja).

    The fault-bit names come from the coordinator's register map via the decoded
    AkvoState rather than from const directly, so a project-specific map with
    different bit names is handled transparently at decode time.
    """
    from .const import DRIVE_FAULT_BITS  # default map names for entity setup

    descs: list[AkvoBinaryDescription] = []
    for name in DRIVE_FAULT_BITS.values():
        descs.append(
            AkvoBinaryDescription(
                key=f"{group}_fault_{name}",
                translation_key=f"{group}_fault_{name}",
                device_class=BinarySensorDeviceClass.PROBLEM,
                entity_category=EntityCategory.DIAGNOSTIC,
                value_fn=(
                    lambda s, n=name, acc=accessor: acc(s).get(n, False)
                ),
            )
        )
    return descs


def _topplate_fault_descriptions(count: int) -> list[AkvoBinaryDescription]:
    descs: list[AkvoBinaryDescription] = []
    for i in range(1, count + 1):
        key = f"topplate_fault_{i}"
        descs.append(
            AkvoBinaryDescription(
                key=key,
                translation_key="topplate_fault",
                device_class=BinarySensorDeviceClass.PROBLEM,
                entity_category=EntityCategory.DIAGNOSTIC,
                value_fn=(
                    lambda s, n=i: s.topplate_faults.get(f"topplate_{n}", False)
                ),
            )
        )
    return descs


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AkvoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AKVO binary sensors."""
    coordinator = entry.runtime_data
    # Top-plate count comes from the live register map so custom maps are respected.
    topplate_count = coordinator.client._map.topplate_fault_count
    descs: list[AkvoBinaryDescription] = list(STATUS_SENSORS)
    descs += _drive_fault_descriptions("main", lambda s: s.main_faults)
    descs += _drive_fault_descriptions("baja", lambda s: s.baja_faults)
    descs += _topplate_fault_descriptions(topplate_count)
    async_add_entities(AkvoBinarySensor(coordinator, d) for d in descs)


class AkvoBinarySensor(AkvoEntity, BinarySensorEntity):
    """An AKVO status, safety, or fault bit."""

    entity_description: AkvoBinaryDescription

    def __init__(
        self, coordinator: AkvoCoordinator, description: AkvoBinaryDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.key.startswith("topplate_fault_"):
            idx = description.key.rsplit("_", 1)[-1]
            self._attr_translation_placeholders = {"index": idx}

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data)
