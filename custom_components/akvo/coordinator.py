"""DataUpdateCoordinator for the AKVO movable floor."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .akvo_client import AkvoClient, AkvoConnectionError, AkvoState
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS, WATCHDOG_SERVICE_INTERVAL, WATCHDOG_TIMEOUT
from .modbus_transport import ModbusTransport
from .register_map import RegisterMap

_LOGGER = logging.getLogger(__name__)

type AkvoConfigEntry = ConfigEntry["AkvoCoordinator"]


class AkvoCoordinator(DataUpdateCoordinator[AkvoState]):
    """Polls the AKVO status/fault/position/current registers and owns the safety client."""

    config_entry: AkvoConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AkvoConfigEntry,
        transport: ModbusTransport,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.transport = transport
        # Build the register map from config-entry data so any project-specific
        # address overrides (reg_* keys) are picked up automatically.
        reg_map = RegisterMap.from_config(entry.data)
        self.client = AkvoClient(
            read_fn=transport.read_holding,
            write_fn=transport.write_holding,
            reg_map=reg_map,
            watchdog_timeout=WATCHDOG_TIMEOUT,
            service_interval=WATCHDOG_SERVICE_INTERVAL,
        )

    async def _async_update_data(self) -> AkvoState:
        try:
            state = await self.client.async_read_state()
            # Idle keep-alive: mirror AKVO's watchdog so it doesn't declare comm
            # bad while no command is active. No-op while a command runs.
            await self.client.async_service_watchdog(state)
        except AkvoConnectionError as err:
            raise UpdateFailed(str(err)) from err
        return state
