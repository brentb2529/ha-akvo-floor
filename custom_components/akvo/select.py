"""AKVO configuration-preset request select (SAFETY-CRITICAL command path).

The select offers only the operator-enabled AKVO presets plus an explicit
"none" sentinel. Choosing a preset issues a PLC-MEDIATED move-to-config REQUEST
through the safety state machine in :mod:`akvo_client`:

  * The request is REFUSED unless HR0 bit15 "ready for external" is set, there
    is no fault, no e-stop, and comm is good.
  * Exactly ONE HR10 config bit (bit1..8) is set. HR10 bit0 (System Reset) is
    NEVER written and is NOT exposed.
  * The HR11 watchdog is mirrored continuously while the command is active.
  * The command bit is held until the matching HR1 "configuration achieved" bit
    sets, then cleared. Any fault / e-stop / comm-loss / bit15-drop / watchdog
    timeout ABORTS immediately.

Commands are GATED: if ``enable_commands`` is False (the DEFAULT) every request
is rejected before touching HR10. Live use requires explicit enablement AND the
project-specific 725-<project> map + on-hardware watchdog validation + approval.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .akvo_client import AkvoCommandRejected
from .const import (
    CONF_ENABLE_COMMANDS,
    CONF_PRESETS,
    DEFAULT_ENABLE_COMMANDS,
    DOMAIN,
    PRESET_NONE,
)
from .coordinator import AkvoConfigEntry, AkvoCoordinator
from .entity import AkvoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AkvoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the AKVO configuration-request select."""
    async_add_entities([AkvoConfigurationSelect(entry.runtime_data)])


class AkvoConfigurationSelect(AkvoEntity, SelectEntity):
    """Issues PLC-validated configuration-preset requests."""

    _attr_translation_key = "configuration"

    def __init__(self, coordinator: AkvoCoordinator) -> None:
        super().__init__(coordinator, "configuration")
        options = coordinator.config_entry.options
        # Map friendly name -> config number, for the enabled presets only.
        self._presets: dict[str, str] = options.get(CONF_PRESETS, {})
        self._name_to_config: dict[str, int] = {
            name: int(num) for num, name in self._presets.items()
        }
        self._enable_commands: bool = options.get(
            CONF_ENABLE_COMMANDS, DEFAULT_ENABLE_COMMANDS
        )
        self._attr_options = [PRESET_NONE, *self._name_to_config]
        if not self._enable_commands:
            _LOGGER.warning(
                "AKVO commands are DISABLED (enable_commands=False). The "
                "configuration select will REJECT every request. This is the "
                "safe default; enable only after the project map and on-hardware "
                "watchdog validation are approved"
            )

    @property
    def current_option(self) -> str:
        """Reflect the achieved configuration read back from the PLC."""
        cfg = self.coordinator.data.active_configuration
        if cfg is None:
            return PRESET_NONE
        return self._presets.get(str(cfg), PRESET_NONE)

    async def async_select_option(self, option: str) -> None:
        """Request a configuration preset via the safety state machine."""
        if option == PRESET_NONE:
            # We never command a "go home"/stop via this path; the PLC and HMI
            # own motion. Selecting none is a no-op request acknowledgement.
            return

        if not self._enable_commands:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="commands_disabled",
            )

        config = self._name_to_config.get(option)
        if config is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_preset",
                translation_placeholders={"option": option},
            )

        client = self.coordinator.client
        try:
            await client.async_request_configuration(config)
        except AkvoCommandRejected as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="request_rejected",
                translation_placeholders={"reason": str(err)},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()
