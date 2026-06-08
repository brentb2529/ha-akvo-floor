"""The AKVO Movable Floor integration.

SAFETY-CRITICAL. The AKVO PLC is the sole safety authority. This integration is
a thin Modbus TCP client that issues PLC-validated configuration-preset REQUESTS
only. It NEVER commands motion directly, NEVER resets faults over Modbus, and
NEVER exposes arbitrary positions. Command output is GATED OFF by default
(see ``enable_commands``); read entities are always available.
"""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .akvo_client import AkvoConnectionError
from .coordinator import AkvoConfigEntry, AkvoCoordinator
from .modbus_transport import ModbusTransport

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: AkvoConfigEntry) -> bool:
    """Set up AKVO from a config entry."""
    transport = ModbusTransport(entry.data[CONF_HOST], entry.data[CONF_PORT])
    try:
        await transport.async_connect()
    except AkvoConnectionError as err:
        await transport.async_close()
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = AkvoCoordinator(hass, entry, transport)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AkvoConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.transport.async_close()
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: AkvoConfigEntry) -> None:
    """Reload when options (presets / enable_commands) change."""
    await hass.config_entries.async_reload(entry.entry_id)
