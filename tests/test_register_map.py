"""Tests for RegisterMap: construction, overrides, decode correctness with custom maps.

Covers:
  - Default map matches the example-map constants in const.py
  - from_config() applies reg_* overrides correctly and ignores unknowns
  - read_count and reg_index computed properties are correct
  - decode_state() with a remapped register layout decodes correctly
  - decode_state() tolerates short-read guard and raises on undercount
  - Configurable topplate_fault_count
  - Configurable min_config / max_config
  - RegisterMap is frozen (immutable)
"""

from __future__ import annotations

import pytest

from custom_components.akvo.akvo_client import (
    AkvoCommandRejected,
    AkvoConnectionError,
    AkvoClient,
    decode_state,
)
from custom_components.akvo.const import (
    DRIVE_FAULT_BITS,
    HR_COMMAND,
    TOPPLATE_FAULT_COUNT,
)
from custom_components.akvo.register_map import CONF_REG_PREFIX, RegisterMap


# ---------------------------------------------------------------------------
# Default map matches const.py
# ---------------------------------------------------------------------------

def test_default_map_addresses_match_const():
    m = RegisterMap()
    assert m.hr_status_1 == 0
    assert m.hr_status_2 == 1
    assert m.hr_fault_main == 2
    assert m.hr_fault_baja == 3
    assert m.hr_fault_topplate == 4
    assert m.hr_pos_main == 6
    assert m.hr_pos_baja == 7
    assert m.hr_current_main == 8
    assert m.hr_current_baja == 9
    assert m.hr_command == 10
    assert m.hr_watchdog == 11


def test_default_map_bit_positions_match_const():
    m = RegisterMap()
    assert m.s1_system_ready == 0
    assert m.s1_system_fault == 1
    assert m.s1_estop_indoor == 3
    assert m.s1_estop_outdoor == 4
    assert m.s1_watchdog_from_akvo == 13
    assert m.s1_bad_modbus_comm == 14
    assert m.s1_ready_for_external == 15
    assert m.s2_floors_moving == 0
    assert m.s2_config_achieved_base == 2
    assert m.cmd_move_config_base == 1
    assert m.wd_external == 0


def test_default_map_fault_bits_match_const():
    m = RegisterMap()
    assert m.drive_fault_bits == DRIVE_FAULT_BITS
    assert m.topplate_fault_count == TOPPLATE_FAULT_COUNT


def test_default_map_read_count():
    m = RegisterMap()
    # Default map: HR0..HR9 = 10 registers
    assert m.read_start == 0
    assert m.read_count == 10


def test_default_map_is_frozen():
    m = RegisterMap()
    with pytest.raises((AttributeError, TypeError)):
        m.hr_command = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# from_config overrides
# ---------------------------------------------------------------------------

def test_from_config_applies_single_override():
    data = {f"{CONF_REG_PREFIX}hr_command": 20}
    m = RegisterMap.from_config(data)
    assert m.hr_command == 20
    # Other fields unchanged
    assert m.hr_status_1 == 0


def test_from_config_applies_multiple_overrides():
    data = {
        f"{CONF_REG_PREFIX}hr_status_1": 10,
        f"{CONF_REG_PREFIX}hr_status_2": 11,
        f"{CONF_REG_PREFIX}hr_pos_main": 16,
        f"{CONF_REG_PREFIX}hr_pos_baja": 17,
        f"{CONF_REG_PREFIX}hr_current_main": 18,
        f"{CONF_REG_PREFIX}hr_current_baja": 19,
        f"{CONF_REG_PREFIX}hr_fault_main": 12,
        f"{CONF_REG_PREFIX}hr_fault_baja": 13,
        f"{CONF_REG_PREFIX}hr_fault_topplate": 14,
        f"{CONF_REG_PREFIX}hr_outputs": 15,
    }
    m = RegisterMap.from_config(data)
    assert m.hr_status_1 == 10
    assert m.hr_pos_main == 16
    assert m.hr_current_baja == 19


def test_from_config_ignores_unknown_keys():
    data = {
        "host": "1.2.3.4",
        "port": 502,
        "enable_commands": False,
        "reg_nonexistent_field": 42,
    }
    # Must not raise; unknown keys silently ignored
    m = RegisterMap.from_config(data)
    assert m == RegisterMap()


def test_from_config_empty_data_gives_defaults():
    assert RegisterMap.from_config({}) == RegisterMap()


def test_from_config_topplate_count_override():
    data = {f"{CONF_REG_PREFIX}topplate_fault_count": 8}
    m = RegisterMap.from_config(data)
    assert m.topplate_fault_count == 8


def test_from_config_config_range_override():
    data = {
        f"{CONF_REG_PREFIX}min_config": 1,
        f"{CONF_REG_PREFIX}max_config": 4,
    }
    m = RegisterMap.from_config(data)
    assert m.min_config == 1
    assert m.max_config == 4


# ---------------------------------------------------------------------------
# read_count and reg_index with remapped addresses
# ---------------------------------------------------------------------------

def test_read_count_rebased_map():
    # Shift the entire read block up by 10 registers.
    data = {
        f"{CONF_REG_PREFIX}hr_status_1": 10,
        f"{CONF_REG_PREFIX}hr_status_2": 11,
        f"{CONF_REG_PREFIX}hr_fault_main": 12,
        f"{CONF_REG_PREFIX}hr_fault_baja": 13,
        f"{CONF_REG_PREFIX}hr_fault_topplate": 14,
        f"{CONF_REG_PREFIX}hr_outputs": 15,
        f"{CONF_REG_PREFIX}hr_pos_main": 16,
        f"{CONF_REG_PREFIX}hr_pos_baja": 17,
        f"{CONF_REG_PREFIX}hr_current_main": 18,
        f"{CONF_REG_PREFIX}hr_current_baja": 19,
    }
    m = RegisterMap.from_config(data)
    assert m.read_start == 10
    assert m.read_count == 10  # still 10 consecutive registers


def test_reg_index_default_map():
    m = RegisterMap()
    assert m.reg_index(0) == 0   # hr_status_1
    assert m.reg_index(6) == 6   # hr_pos_main
    assert m.reg_index(9) == 9   # hr_current_baja


def test_reg_index_out_of_range_raises():
    m = RegisterMap()
    with pytest.raises(ValueError, match="outside read block"):
        m.reg_index(10)  # HR10 is outside the read block (it's write-only)


def test_reg_index_rebased_map():
    data = {f"{CONF_REG_PREFIX + k}": v for k, v in {
        "hr_status_1": 10, "hr_status_2": 11, "hr_fault_main": 12,
        "hr_fault_baja": 13, "hr_fault_topplate": 14, "hr_outputs": 15,
        "hr_pos_main": 16, "hr_pos_baja": 17,
        "hr_current_main": 18, "hr_current_baja": 19,
    }.items()}
    m = RegisterMap.from_config(data)
    assert m.reg_index(10) == 0   # first register
    assert m.reg_index(16) == 6   # hr_pos_main at same relative position
    assert m.reg_index(19) == 9   # last register


# ---------------------------------------------------------------------------
# decode_state with default map (regression)
# ---------------------------------------------------------------------------

def test_decode_uses_default_map_when_none():
    raw = [0] * 10
    raw[0] = (1 << 0) | (1 << 15)  # system_ready + ready_for_external
    state = decode_state(raw, None)
    assert state.system_ready
    assert state.ready_for_external
    assert state.safe_for_command


def test_decode_short_read_raises():
    with pytest.raises(AkvoConnectionError, match="short read"):
        decode_state([0] * 5)


# ---------------------------------------------------------------------------
# decode_state with remapped addresses
# ---------------------------------------------------------------------------

def test_decode_remapped_positions():
    """Shift entire read block to HR10..HR19; verify positions decode correctly."""
    data = {f"{CONF_REG_PREFIX + k}": v for k, v in {
        "hr_status_1": 10, "hr_status_2": 11, "hr_fault_main": 12,
        "hr_fault_baja": 13, "hr_fault_topplate": 14, "hr_outputs": 15,
        "hr_pos_main": 16, "hr_pos_baja": 17,
        "hr_current_main": 18, "hr_current_baja": 19,
    }.items()}
    m = RegisterMap.from_config(data)

    # raw is indexed by offset within the block (0..9)
    raw = [0] * 10
    raw[0] = (1 << 0) | (1 << 15)   # hr_status_1 offset 0: system_ready + ready_for_ext
    raw[m.reg_index(m.hr_pos_main)] = 2000   # 2.000 m
    raw[m.reg_index(m.hr_current_main)] = 300  # 3.00 A

    state = decode_state(raw, m)
    assert state.system_ready
    assert state.ready_for_external
    assert state.main_position_m == pytest.approx(2.0, abs=0.001)
    assert state.main_current_a == pytest.approx(3.0, abs=0.01)


def test_decode_remapped_fault_bits():
    """Remap HR2 to a different address and verify fault decoding still works."""
    m = RegisterMap.from_config({f"{CONF_REG_PREFIX}hr_fault_main": 5})
    # read block covers HR0..HR9 since hr_fault_main=5 is within [0,9]
    raw = [0] * 10
    raw[0] = (1 << 0) | (1 << 15)  # healthy status
    raw[5] = 0x0001  # VFD fault in the remapped main-fault register

    state = decode_state(raw, m)
    assert state.main_faults["vfd"]
    assert state.any_fault


def test_decode_configurable_topplate_count():
    """topplate_fault_count=4 => only 4 fault bits decoded."""
    m = RegisterMap.from_config({f"{CONF_REG_PREFIX}topplate_fault_count": 4})
    raw = [0] * 10
    raw[4] = 0xFF  # all 8 low bits set

    state = decode_state(raw, m)
    # Only topplate_1..4 present
    assert all(state.topplate_faults[f"topplate_{i}"] for i in range(1, 5))
    assert "topplate_5" not in state.topplate_faults


def test_decode_configurable_max_config():
    """max_config=4 => configs 5..8 achieved bits not inspected."""
    m = RegisterMap.from_config({f"{CONF_REG_PREFIX}max_config": 4})
    raw = [0] * 10
    raw[0] = (1 << 0) | (1 << 15)
    # Set achieved bit for config 4 (bit 2+4-1=5)
    raw[1] = 1 << 5
    state = decode_state(raw, m)
    assert state.active_configuration == 4

    # Now set bit for config 5 (bit 2+5-1=6) — beyond max_config=4, ignored
    raw[1] = 1 << 6
    state2 = decode_state(raw, m)
    assert state2.active_configuration is None


# ---------------------------------------------------------------------------
# AkvoClient._config_command_word with custom map
# ---------------------------------------------------------------------------

def test_config_command_word_custom_base():
    """cmd_move_config_base=2 shifts all command bits up by one."""
    m = RegisterMap.from_config({f"{CONF_REG_PREFIX}cmd_move_config_base": 2})
    word = AkvoClient._config_command_word(1, m)
    # config 1 => bit (2 + 1 - 1) = bit 2
    assert word == (1 << 2)
    # bit0 never set
    assert not (word & 1)


def test_config_command_word_range_custom_max():
    m = RegisterMap.from_config({f"{CONF_REG_PREFIX}max_config": 4})
    with pytest.raises(AkvoCommandRejected, match="out of range"):
        AkvoClient._config_command_word(5, m)


# ---------------------------------------------------------------------------
# Comm-loss -> unavailable (decode tolerates unexpected bit patterns)
# ---------------------------------------------------------------------------

def test_decode_all_ff_registers_no_crash():
    """A Modbus exception frame that returns 0xFFFF in all regs must not crash."""
    raw = [0xFFFF] * 10
    # Should not raise; any_fault etc may be True but no exception
    state = decode_state(raw)
    assert state is not None
    # safe_for_command will be False (system_fault and estop bits set)
    assert not state.safe_for_command


def test_decode_all_zero_registers():
    """All-zero registers: nothing ready, nothing faulted."""
    state = decode_state([0] * 10)
    assert not state.system_ready
    assert not state.system_fault
    assert not state.any_fault
    assert not state.safe_for_command  # ready_for_external is also 0
