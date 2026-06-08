"""Diagnostics support for AKVO Movable Floor.

Exposes the decoded register snapshot, the register-map in use, and the
config-entry metadata for issue reports. Credentials and secrets are redacted
(this integration has none beyond the host/port which are useful for diagnostics).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import AkvoConfigEntry

# Nothing sensitive in AKVO data, but redact host in case the installation
# is on a private network and the user shares diagnostics publicly.
TO_REDACT: set[str] = {"host"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: AkvoConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for an AKVO config entry."""
    coordinator = entry.runtime_data
    state = coordinator.data
    reg_map = coordinator.client._map

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "register_map": {
            "hr_status_1": reg_map.hr_status_1,
            "hr_status_2": reg_map.hr_status_2,
            "hr_fault_main": reg_map.hr_fault_main,
            "hr_fault_baja": reg_map.hr_fault_baja,
            "hr_fault_topplate": reg_map.hr_fault_topplate,
            "hr_pos_main": reg_map.hr_pos_main,
            "hr_pos_baja": reg_map.hr_pos_baja,
            "hr_current_main": reg_map.hr_current_main,
            "hr_current_baja": reg_map.hr_current_baja,
            "hr_command": reg_map.hr_command,
            "hr_watchdog": reg_map.hr_watchdog,
            "read_start": reg_map.read_start,
            "read_count": reg_map.read_count,
            "topplate_fault_count": reg_map.topplate_fault_count,
            "min_config": reg_map.min_config,
            "max_config": reg_map.max_config,
            "position_scale": reg_map.position_scale,
            "current_scale": reg_map.current_scale,
        },
        "last_state": {
            "system_ready": state.system_ready,
            "system_fault": state.system_fault,
            "estop_indoor": state.estop_indoor,
            "estop_outdoor": state.estop_outdoor,
            "bad_modbus_comm": state.bad_modbus_comm,
            "ready_for_external": state.ready_for_external,
            "floors_moving": state.floors_moving,
            "active_configuration": state.active_configuration,
            "main_position_m": state.main_position_m,
            "baja_position_m": state.baja_position_m,
            "main_current_a": state.main_current_a,
            "baja_current_a": state.baja_current_a,
            "main_faults": state.main_faults,
            "baja_faults": state.baja_faults,
            "topplate_faults": state.topplate_faults,
        },
        "last_abort_reason": coordinator.client.last_abort_reason,
    }
