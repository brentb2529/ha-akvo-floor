"""Constants and default AKVO register-map values.

SAFETY-CRITICAL COMPONENT. The AKVO PLC is the sole safety authority. This
integration is a *thin* client that issues PLC-validated configuration-preset
REQUESTS only. It never commands motion directly, never resets faults over
Modbus, and never exposes arbitrary positions.

The register addresses and bit positions below are DEFAULT values taken from the
EXAMPLE Modbus table ``725-XXXXX``. The project-specific map (``725-<project>``)
MUST be reconciled and on-hardware watchdog validation MUST pass before commands
are enabled on a live installation.

For project-specific maps, use ``RegisterMap.from_config(entry.data)``
(see :mod:`register_map`). The config/options flow exposes per-field overrides
under the ``reg_`` prefix so installers can adapt without code changes.
"""

from __future__ import annotations

DOMAIN = "akvo"

# --- Connection --------------------------------------------------------------
DEFAULT_PORT = 502
# NOTE: 172.20.1.100 is only the documented EXAMPLE address. Dev/test always
# points at a FAKE server. Never use as-default for a live installation.
DEFAULT_HOST = ""

CONF_ENABLE_COMMANDS = "enable_commands"
CONF_PRESETS = "presets"   # mapping of config number (1..8) -> friendly name

# Commands default OFF. The select renders but rejects every request until an
# operator explicitly enables commands AND the project map is validated.
DEFAULT_ENABLE_COMMANDS = False

MIN_CONFIG = 1
MAX_CONFIG = 8

# --- Polling -----------------------------------------------------------------
# Not user-configurable (HA guideline). Fast poll because this drives a safety
# display and feeds the watchdog/abort logic.
UPDATE_INTERVAL_SECONDS = 0.5

# --- Watchdog timing ---------------------------------------------------------
# AKVO mirrors HR0 bit13; external system must echo to HR11 bit0 within 1s.
# AKVO declares comm bad after >5s without a correct mirror and STOPS motion.
WATCHDOG_SERVICE_INTERVAL = 0.25  # how often we mirror while a command is active
WATCHDOG_TIMEOUT = 5.0            # our self-imposed abort ceiling (<= AKVO's 5s)

# --- Default register addresses (example map 725-XXXXX) ---------------------
# These constants mirror RegisterMap defaults. Keep in sync with register_map.py.
# Tests and the fake server reference these directly so they are preserved here.
HR_STATUS_1 = 0
HR_STATUS_2 = 1
HR_FAULT_MAIN = 2
HR_FAULT_BAJA = 3
HR_FAULT_TOPPLATE = 4
HR_OUTPUTS = 5
HR_POS_MAIN = 6
HR_POS_BAJA = 7
HR_CURRENT_MAIN = 8
HR_CURRENT_BAJA = 9
HR_COMMAND = 10
HR_WATCHDOG = 11

READ_COUNT = 10  # HR0..HR9 read in one block (default map)
WRITE_REGISTERS = (HR_COMMAND, HR_WATCHDOG)

# --- HR0 General Status Word 1 bits (default map) ---------------------------
S1_SYSTEM_READY = 0
S1_SYSTEM_FAULT = 1
S1_CONTROL_KEY_ON = 2
S1_ESTOP_INDOOR = 3
S1_ESTOP_OUTDOOR = 4
S1_POWER_VOLTAGE_DISABLED = 5
S1_OPERATOR_LOGGED_IN = 6
S1_ADMIN_LOGGED_IN = 7
S1_TECHNICAL_LOGGED_IN = 8
S1_WATCHDOG_FROM_AKVO = 13
S1_BAD_MODBUS_COMM = 14
S1_READY_FOR_EXTERNAL = 15

# --- HR1 Status Word 2 bits (default map) ------------------------------------
S2_FLOORS_MOVING = 0
S2_NOT_PREDEFINED_CONFIG = 1
# bits 2..9 => configuration #1..#8 achieved
S2_CONFIG_ACHIEVED_BASE = 2  # config N achieved => bit (S2_CONFIG_ACHIEVED_BASE + N - 1)

# --- HR10 command bits (default map) -----------------------------------------
CMD_SYSTEM_RESET = 0      # DELIBERATELY NEVER WRITTEN / NEVER EXPOSED.
CMD_MOVE_CONFIG_BASE = 1  # move-to-config N => bit (CMD_MOVE_CONFIG_BASE + N - 1)

# --- HR11 watchdog bit (default map) -----------------------------------------
WD_EXTERNAL = 0

# --- Fault bit labels (for decoded diagnostic binary_sensors) ----------------
# HR2 main-floor / HR3 baja share the same drive fault layout (default map).
DRIVE_FAULT_BITS: dict[int, str] = {
    0: "vfd",
    1: "overtravel_up",
    2: "overtravel_down",
    3: "motor_overload",
    4: "direction",
    5: "no_movement",
    6: "off_speed",
    7: "position_ref",
    8: "relative_position",
}

# HR4 top-plate faults #1..#14 => bits 0..13 (default map).
TOPPLATE_FAULT_COUNT = 14

# Scaling (default map; both values are floats for mypy)
POSITION_SCALE = 0.001  # int16 metres x1000
CURRENT_SCALE = 0.01    # int16 amps x100

# Sentinel select option meaning "no request / clear".
PRESET_NONE = "—"
