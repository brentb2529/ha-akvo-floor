# AKVO Movable Floor — Home Assistant custom integration

A **thin, SAFETY-CRITICAL** Home Assistant integration for the AKVO Spiralift
movable pool floor over **Modbus TCP**.

---

## SAFETY — READ BEFORE USE

**The AKVO PLC is the sole safety authority.** This integration is a display
and configuration-request client only. It does not control, command, or
override the PLC's internal safety logic. Before enabling commands on any live
installation you MUST read and comply with every item below.

### What this integration does and does not do

| This integration DOES | This integration DOES NOT |
|---|---|
| Read HR0..HR9 status/fault/position/current registers | Command motion directly |
| Issue PLC-validated "move to configuration #N" requests (HR10) | Write HR10 bit0 (System Reset) — there is no code path that can |
| Maintain the HR11 external watchdog mirror while a command is active | Reset faults over Modbus |
| Abort and clear HR10 on any fault / e-stop / comm-loss / watchdog stall | Expose arbitrary position targets |
| Reject all requests when `enable_commands = False` (the **default**) | Bypass PLC interlocks |

### Command gate — default OFF

`enable_commands` defaults to **False**. The configuration-request select
entity is visible but rejects every option with a clear error until you
explicitly enable commands in Options. **Enable only after all of the following
are satisfied:**

1. The project-specific AKVO Modbus map document (`725-<project>`) has been
   obtained and every register address and bit position has been verified
   against this integration's register-map settings (see below).
2. On-hardware watchdog validation has been performed: observe HR11 mirroring
   HR0 bit13 correctly within the AKVO PLC's 5-second window.
3. A qualified operator has signed off on the configuration.

### Project-specific register map

The register addresses and bit positions in this integration default to the
AKVO example map `725-XXXXX`. **This example map is for development and testing
only.** Every real AKVO Spiralift installation is delivered with a project-
specific document (`725-<project>`) that defines the exact Modbus register
layout for that controller. You MUST verify your installation's map against
the defaults and override any differences in the integration's Options
(Advanced: Register map overrides) before enabling commands.

Incorrect register-map settings will result in wrong state decoding and missed
fault detection. The safety state machine cannot protect against a mis-mapped
installation.

### Watchdog

The AKVO PLC expects the external system to mirror HR0 bit13 into HR11 bit0
continuously. If the mirror lapses for more than 5 seconds the PLC sets
HR0 bit14 (bad Modbus comm) and STOPS any running configuration. This
integration mirrors the watchdog:

- During **idle** polling (every 0.5 s coordinator poll).
- During **active commands** (every 0.25 s service loop).

If the integration loses connectivity to the Modbus server, all entities
transition to `unavailable` and the watchdog mirror stops. The PLC will detect
comm loss and abort any in-progress move within its 5-second window.

---

## Entities

### Read (always available, no `enable_commands` required)

| Entity | Source | Device class / unit |
|---|---|---|
| `sensor.…_main_floor_position` | HR6 int16 × 0.001 | distance / m |
| `sensor.…_baja_position` | HR7 int16 × 0.001 | distance / m |
| `sensor.…_main_floor_motor_current` | HR8 int16 × 0.01 | current / A |
| `sensor.…_baja_motor_current` | HR9 int16 × 0.01 | current / A |
| `sensor.…_active_configuration` | HR1 achieved bits | text (diagnostic) |
| `binary_sensor.…_system_ready` | HR0 b0 | — |
| `binary_sensor.…_system_fault` | HR0 b1 | problem |
| `binary_sensor.…_emergency_stop` | HR0 b3 OR b4 | safety |
| `binary_sensor.…_e_stop_indoor` | HR0 b3 | safety (diagnostic) |
| `binary_sensor.…_e_stop_outdoor` | HR0 b4 | safety (diagnostic) |
| `binary_sensor.…_floors_moving` | HR1 b0 | moving |
| `binary_sensor.…_bad_modbus_comm` | HR0 b14 | problem (diagnostic) |
| `binary_sensor.…_ready_for_external_commands` | HR0 b15 | — (diagnostic) |
| `binary_sensor.…_control_key_on` | HR0 b2 | — (diagnostic) |
| `binary_sensor.…_{main,baja}_*_fault` (×9 each) | HR2 / HR3 bits | problem (diagnostic) |
| `binary_sensor.…_top_plate_fault_{1..14}` | HR4 bits | problem (diagnostic) |

Position sensors report signed metres (negative = above deck level).
Home Assistant converts automatically to any preferred unit.

### Command (gated, `enable_commands = False` by default)

| Entity | Mechanism |
|---|---|
| `select.…_configuration_request` | HR10 move-to-config #N + HR11 watchdog via safety state machine |

---

## Safety state machine (preset request)

1. **Gate check** — if `enable_commands` is False, raise immediately before touching HR10.
2. **Pre-check** — read HR0..HR9. Refuse unless HR0 bit15 *ready-for-external* is
   set **and** no fault (HR0 b1, HR2/3/4 ≠ 0) **and** no e-stop (HR0 b3/4) **and**
   comm good (HR0 b14 clear).
3. **Issue** — write EXACTLY ONE HR10 config bit (bit1..8 for configs 1..8).
   HR10 bit0 (System Reset) is **never** set — by construction, not by runtime check.
4. **Watchdog** — mirror HR0 bit13 → HR11 bit0 every 0.25 s while active.
5. **Hold** — keep the bit until the matching HR1 *configuration achieved* bit
   sets and motion stops, then clear HR10.
6. **Abort** — on ANY of: fault, e-stop, bad comm, ready-for-external dropped
   before completion, watchdog bit13 stalled > 5 s → clear HR10 and raise.
   The command is always cleared on the way out, even on unexpected exceptions.

---

## Installation

### HACS

1. Add this repository as a custom HACS repository (type: Integration).
2. Search for "AKVO Movable Floor" and install.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration → AKVO Movable Floor**.

### Manual

Copy `custom_components/akvo/` into your Home Assistant `custom_components/`
directory and restart.

---

## Configuration

### Step 1 — Connection

Enter the IP address (or hostname) and Modbus TCP port of your AKVO controller.
The integration validates the connection before proceeding.

### Step 2 — Presets & command gate

Name each AKVO configuration preset (1–8) you want available in Home Assistant
(e.g. "Pool mode", "Deck mode"). Leave a name blank to hide that preset.
`enable_commands` defaults to **OFF** — leave it off until the register map
and on-hardware validation are complete.

### Step 3 — Register map overrides (Advanced)

Every register address and bit position defaults to the example map `725-XXXXX`.
If your project-specific `725-<project>` document specifies different values,
enter them here. Incorrect values here will break safety detection.

All overrides can be changed later in **Options** (the same three-step flow).

---

## Options

Options expose the same preset names, command gate, and register-map overrides
as the initial setup flow. Changes trigger an automatic reload so the new
register map and preset names take effect immediately.

---

## Development & testing

```bash
python -m venv .venv
.venv/bin/pip install pymodbus pytest pytest-asyncio \
    pytest-homeassistant-custom-component

# Run the full suite (73 tests: safety, decode, register-map, watchdog, HA, e2e)
.venv/bin/python -m pytest

# Start the fake AKVO PLC (example map 725-XXXXX) on port 5020
.venv/bin/python fake/fake_akvo_server.py --port 5020
```

`fake/fake_akvo_server.py` is a simulated AKVO PLC. It serves HR0..HR9,
accepts HR10/HR11 writes, runs watchdog supervision, ramps a simulated move to
"achieved", and supports fault/e-stop injection for abort testing.

**Always build and test against the fake server only — never point at live
hardware.** The command path is gated off by default.

---

## Known limitations

- **No auto-discovery.** Modbus TCP has no standard discovery protocol; you
  must enter the controller IP manually.
- **Register map is example-only.** The default `725-XXXXX` map may not match
  your installation. You must obtain and verify your project's `725-<project>`
  document before enabling commands.
- **On-hardware validation required.** The watchdog timing and command-word
  layout must be validated against the actual PLC before live use.
- **No fault reset.** HR10 bit0 (System Reset) is deliberately unreachable from
  this integration. Fault resets must be performed at the AKVO HMI.
- **No arbitrary position control.** Only the eight predefined configuration
  presets can be requested; the PLC executes the move entirely under its own
  control.

---

## Diagnostics

**Settings → Devices & Services → AKVO Movable Floor → Download diagnostics**
provides a redacted JSON snapshot including: the active register map, the last
decoded PLC state, the last abort reason, and entry options. Attach this when
reporting issues.

---

## Removal

1. Go to **Settings → Devices & Services → AKVO Movable Floor → Delete**.
2. Restart Home Assistant (optional but recommended to clean up pymodbus state).
3. Remove `custom_components/akvo/` if installed manually.

---

## Quality scale

Self-assessed at **Silver** (targeting Gold). Key gaps:

- Individual drive-fault sensors should be `disabled_by_default = True` (high
  cardinality, installer-level detail).
- Brands repo icon/logo not yet submitted.
- Config-flow reconfigure step not yet implemented.

See `quality_scale.yaml` for the full rule-by-rule status.
