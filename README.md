# AKVO Movable Floor — Home Assistant custom integration

A **thin, SAFETY-CRITICAL** Home Assistant integration for the AKVO Spiralift
movable pool floor over **Modbus TCP**.

> ⚠️ **The AKVO PLC is the sole safety authority.** This integration NEVER
> commands motion directly. It issues only PLC-validated *configuration-preset
> REQUESTS* (HR10 "Move to configuration #N"), maintains the HR11 watchdog while
> a request is active, and aborts on any fault / e-stop / comm-loss. It NEVER
> writes HR10 bit0 (System Reset), never resets faults over Modbus, and never
> exposes arbitrary positions. **Commands default OFF** (`enable_commands`).

## Entities

**Read (always available):**

| Entity | Source | Class / unit |
|---|---|---|
| `sensor.akvo_movable_floor_main_floor_position` | HR6 int16 ×0.001 | distance / m |
| `sensor.akvo_movable_floor_baja_position` | HR7 int16 ×0.001 | distance / m |
| `sensor.akvo_movable_floor_main_floor_motor_current` | HR8 int16 ×0.01 | current / A |
| `sensor.akvo_movable_floor_baja_motor_current` | HR9 int16 ×0.01 | current / A |
| `sensor.akvo_movable_floor_active_configuration` | HR1 achieved bits | text |
| `binary_sensor…_system_ready` | HR0 b0 | — |
| `binary_sensor…_system_fault` | HR0 b1 | problem |
| `binary_sensor…_emergency_stop` | HR0 b3/b4 | safety |
| `binary_sensor…_floors_moving` | HR1 b0 | moving |
| `binary_sensor…_bad_modbus_comm` | HR0 b14 | problem (diag) |
| `binary_sensor…_ready_for_external_commands` | HR0 b15 | — (diag) |
| `binary_sensor…_{main,baja}_*_fault` | HR2 / HR3 bits | problem (diag) |
| `binary_sensor…_top_plate_fault_{1..14}` | HR4 bits | problem (diag) |

**Command (gated, default OFF):**

| Entity | Mechanism |
|---|---|
| `select.akvo_movable_floor_configuration_request` | HR10 move-to-config #N + HR11 watchdog, via the safety state machine |

## Safety state machine (preset request)

1. **Pre-check** — refuse unless HR0 bit15 *ready-for-external* is set **and** no
   fault (HR0 bit1, HR2/3/4 ≠ 0) **and** no e-stop (HR0 bit3/4) **and** comm good
   (HR0 bit14 clear). If commands are disabled, refuse before touching HR10.
2. **Issue** — set EXACTLY ONE HR10 config bit (bit1..8). HR10 bit0 (reset) is
   never set, by construction.
3. **Watchdog** — mirror HR0 bit13 → HR11 bit0 continuously while active. The
   coordinator also mirrors during idle polling so AKVO never declares comm bad.
4. **Hold** — keep the bit until the matching HR1 *configuration achieved* bit
   sets and motion stops, then clear HR10.
5. **Abort** — on ANY fault / e-stop / bad comm / ready-for-external withdrawn
   before completion / watchdog stall: clear HR10 and raise.

## Development & testing

```bash
python -m venv .venv && .venv/bin/pip install pymodbus pytest pytest-asyncio \
    pytest-homeassistant-custom-component
.venv/bin/python -m pytest          # 38 tests: safety, decode, watchdog, HA, e2e
.venv/bin/python fake/fake_akvo_server.py --port 5020   # fake AKVO PLC
```

`fake/fake_akvo_server.py` is a SIMULATED AKVO PLC (example map `725-XXXXX`). It
serves HR0..HR9, accepts HR10/HR11 writes, runs the watchdog supervision, ramps a
simulated move to "achieved", and supports fault/e-stop injection for abort
tests. **Build and test against the fake only — never live hardware.** The
command path is GATED: not enabled live until the project-specific `725-<project>`
map, on-hardware watchdog validation, and explicit approval.
