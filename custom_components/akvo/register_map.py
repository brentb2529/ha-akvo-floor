"""AKVO configurable register map.

SAFETY-CRITICAL COMPONENT.

The AKVO Spiralift communicates over Modbus TCP. The exact holding-register
addresses and bit positions are project-specific: AKVO ships a per-project
document ``725-<project>`` that defines the concrete map for that installation.
The values here are defaults taken from the EXAMPLE map ``725-XXXXX``; they
MUST be verified against the actual ``725-<project>`` document for every site
before commands are enabled.

This module provides :class:`RegisterMap`, a frozen dataclass that carries every
address, bit-position, and scaling constant used by the client and coordinator.
The map is constructed once during config-entry setup from ``config_entry.data``
and injected into the client and decode functions, so an installer can adapt the
integration to their project-specific map without code changes.

Config-entry data keys that override defaults are named ``REG_<FIELD>`` (e.g.
``REG_HR_STATUS_1 = 5``). Any key absent from the entry data falls back to the
field default below. The config-flow / options-flow exposes these overrides in
an advanced section labelled "Register map overrides (project-specific)".
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


# Config-entry data key prefix for register-map overrides.
CONF_REG_PREFIX = "reg_"


@dataclass(frozen=True)
class RegisterMap:
    """All Modbus addresses, bit positions, and scaling constants for one site.

    Defaults match the AKVO example map ``725-XXXXX``. Every integer field can
    be overridden in config-entry data by a key equal to
    ``CONF_REG_PREFIX + <field_name>`` (e.g. ``"reg_hr_status_1"``).
    """

    # ------------------------------------------------------------------ #
    # Holding-register addresses                                           #
    # ------------------------------------------------------------------ #
    hr_status_1: int = 0          # HR0 — General Status Word 1
    hr_status_2: int = 1          # HR1 — General Status Word 2
    hr_fault_main: int = 2        # HR2 — Main-floor drive faults
    hr_fault_baja: int = 3        # HR3 — Baja drive faults
    hr_fault_topplate: int = 4    # HR4 — Top-plate faults
    hr_outputs: int = 5           # HR5 — Output word (read-only, not decoded yet)
    hr_pos_main: int = 6          # HR6 — Main floor position (int16 × 0.001 m)
    hr_pos_baja: int = 7          # HR7 — Baja position (int16 × 0.001 m)
    hr_current_main: int = 8      # HR8 — Main motor current (int16 × 0.01 A)
    hr_current_baja: int = 9      # HR9 — Baja motor current (int16 × 0.01 A)
    hr_command: int = 10          # HR10 — External command word (write)
    hr_watchdog: int = 11         # HR11 — External watchdog mirror (write)

    # ------------------------------------------------------------------ #
    # HR0 (Status Word 1) bit positions                                   #
    # ------------------------------------------------------------------ #
    s1_system_ready: int = 0
    s1_system_fault: int = 1
    s1_control_key_on: int = 2
    s1_estop_indoor: int = 3
    s1_estop_outdoor: int = 4
    s1_power_voltage_disabled: int = 5
    s1_operator_logged_in: int = 6
    s1_admin_logged_in: int = 7
    s1_technical_logged_in: int = 8
    s1_watchdog_from_akvo: int = 13
    s1_bad_modbus_comm: int = 14
    s1_ready_for_external: int = 15

    # ------------------------------------------------------------------ #
    # HR1 (Status Word 2) bit positions                                   #
    # ------------------------------------------------------------------ #
    s2_floors_moving: int = 0
    s2_not_predefined_config: int = 1
    # Config N achieved => bit (s2_config_achieved_base + N - 1)
    s2_config_achieved_base: int = 2

    # ------------------------------------------------------------------ #
    # HR10 command bit positions                                           #
    # ------------------------------------------------------------------ #
    # cmd_system_reset (bit 0) is DELIBERATELY NEVER WRITTEN.
    cmd_move_config_base: int = 1   # config N => bit (cmd_move_config_base + N - 1)

    # ------------------------------------------------------------------ #
    # HR11 watchdog bit position                                           #
    # ------------------------------------------------------------------ #
    wd_external: int = 0

    # ------------------------------------------------------------------ #
    # Fault-word layout                                                    #
    # ------------------------------------------------------------------ #
    # Drive fault bits (HR2 main, HR3 baja — same layout for both)
    drive_fault_bits: dict[int, str] = field(default_factory=lambda: {
        0: "vfd",
        1: "overtravel_up",
        2: "overtravel_down",
        3: "motor_overload",
        4: "direction",
        5: "no_movement",
        6: "off_speed",
        7: "position_ref",
        8: "relative_position",
    })
    # Number of top-plate fault bits in HR4 (bits 0..N-1)
    topplate_fault_count: int = 14

    # ------------------------------------------------------------------ #
    # Scaling                                                              #
    # ------------------------------------------------------------------ #
    position_scale: float = 0.001   # int16 × scale = metres
    current_scale: float = 0.01     # int16 × scale = amperes

    # ------------------------------------------------------------------ #
    # Config range                                                         #
    # ------------------------------------------------------------------ #
    min_config: int = 1
    max_config: int = 8

    # ------------------------------------------------------------------ #
    # Read block size                                                      #
    # ------------------------------------------------------------------ #
    @property
    def read_start(self) -> int:
        """First HR address in the read block (always hr_status_1)."""
        return self.hr_status_1

    @property
    def read_count(self) -> int:
        """Number of consecutive registers to read starting at read_start.

        Must cover through hr_current_baja. For the default map that is 10
        (HR0..HR9); for a re-based map it may differ.
        """
        last = max(
            self.hr_status_1,
            self.hr_status_2,
            self.hr_fault_main,
            self.hr_fault_baja,
            self.hr_fault_topplate,
            self.hr_outputs,
            self.hr_pos_main,
            self.hr_pos_baja,
            self.hr_current_main,
            self.hr_current_baja,
        )
        return last - self.read_start + 1

    def reg_index(self, hr_address: int) -> int:
        """Offset of *hr_address* within the read block.

        Raises :class:`ValueError` if the address is outside the block.
        """
        idx = hr_address - self.read_start
        if idx < 0 or idx >= self.read_count:
            raise ValueError(
                f"Register {hr_address} outside read block "
                f"[{self.read_start}, {self.read_start + self.read_count - 1}]"
            )
        return idx

    # ------------------------------------------------------------------ #
    # Construction helpers                                                 #
    # ------------------------------------------------------------------ #
    @classmethod
    def from_config(cls, data: dict) -> "RegisterMap":
        """Build a RegisterMap from config-entry data, falling back to defaults.

        Any key of the form ``"reg_<field>"`` in *data* overrides the
        corresponding field. Unknown keys are silently ignored so that map
        expansion does not break existing entries.
        """
        overrides: dict[str, object] = {}
        known = {f.name for f in fields(cls) if f.name != "drive_fault_bits"}
        for key, value in data.items():
            if key.startswith(CONF_REG_PREFIX):
                field_name = key[len(CONF_REG_PREFIX):]
                if field_name in known:
                    overrides[field_name] = value
        # drive_fault_bits is not user-overridable via config; it stays default.
        return cls(**overrides)
