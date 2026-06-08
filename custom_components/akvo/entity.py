"""Shared AKVO entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AkvoCoordinator


class AkvoEntity(CoordinatorEntity[AkvoCoordinator]):
    """Base entity grouping everything under one AKVO device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AkvoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="AKVO Movable Floor",
            manufacturer="AKVO",
            model="Spiralift",
        )
