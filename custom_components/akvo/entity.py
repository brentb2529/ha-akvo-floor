"""Shared AKVO entity base."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AkvoCoordinator


class AkvoEntity(CoordinatorEntity[AkvoCoordinator]):
    """Base entity: groups everything under one AKVO Modbus device.

    Unique IDs are ``<entry_id>_<key>`` so they survive host/port changes
    (the user can reconfigure the IP without losing history or automations).
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: AkvoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        host = entry.data.get(CONF_HOST, "")
        port = entry.data.get(CONF_PORT, 502)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="AKVO Movable Floor",
            manufacturer="AKVO",
            model="Spiralift",
            configuration_url=f"http://{host}:{port}" if host else None,
        )
