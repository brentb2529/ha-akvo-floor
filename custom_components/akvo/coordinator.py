"""DataUpdateCoordinator for the AKVO movable floor."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .akvo_client import AkvoClient, AkvoConnectionError, AkvoState
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS
from .modbus_transport import ModbusTransport

_LOGGER = logging.getLogger(__name__)

type AkvoConfigEntry = ConfigEntry["AkvoCoordinator"]


class AkvoCoordinator(DataUpdateCoordinator[AkvoState]):
    """Polls HR0..HR9 and owns the AKVO safety client."""

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
        self.client = AkvoClient(
            read_fn=transport.read_holding,
            write_fn=transport.write_holding,
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
