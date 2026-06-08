"""Tests for register decoding (signedness, scaling, bit maps)."""

from __future__ import annotations

from custom_components.akvo.akvo_client import decode_state


def _u16(v: int) -> int:
    return v & 0xFFFF


def test_signed_position_negative_above_deck():
    raw = [0] * 10
    raw[6] = _u16(-150)  # -0.150 m (above deck)
    raw[7] = _u16(1500)  # 1.500 m
    raw[8] = _u16(-50)  # -0.50 A
    raw[9] = _u16(425)  # 4.25 A
    state = decode_state(raw)
    assert state.main_position_m == -0.15
    assert state.baja_position_m == 1.5
    assert round(state.main_current_a, 2) == -0.5
    assert round(state.baja_current_a, 2) == 4.25


def test_status_bits_decode():
    raw = [0] * 10
    # HR0: system ready(0) + e-stop indoor(3) + bad comm(14) + ready-ext(15)
    raw[0] = (1 << 0) | (1 << 3) | (1 << 14) | (1 << 15)
    state = decode_state(raw)
    assert state.system_ready
    assert state.estop_indoor
    assert state.estop_active
    assert state.bad_modbus_comm
    assert state.ready_for_external
    assert not state.system_fault
    # safe_for_command must be False (estop + bad comm present)
    assert not state.safe_for_command


def test_active_configuration_from_hr1():
    raw = [0] * 10
    # config #3 achieved => bit (2 + 3 - 1) = bit4
    raw[1] = 1 << 4
    state = decode_state(raw)
    assert state.active_configuration == 3
    assert not state.floors_moving


def test_fault_word_decode():
    raw = [0] * 10
    raw[2] = (1 << 0) | (1 << 3)  # main: vfd + motor_overload
    raw[3] = 1 << 1  # baja: overtravel_up
    raw[4] = 1 << 13  # top-plate #14
    state = decode_state(raw)
    assert state.main_faults["vfd"]
    assert state.main_faults["motor_overload"]
    assert not state.main_faults["direction"]
    assert state.baja_faults["overtravel_up"]
    assert state.topplate_faults["topplate_14"]
    assert state.any_fault


def test_clean_state_is_safe():
    raw = [0] * 10
    raw[0] = (1 << 0) | (1 << 15)  # ready + ready-for-external
    state = decode_state(raw)
    assert state.safe_for_command
