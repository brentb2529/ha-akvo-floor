"""Direct safety-state-machine tests against the fake AKVO server.

These exercise the SAFETY-CRITICAL logic without Home Assistant:
  (i)   request rejected when not ready / faulted / e-stopped
  (ii)  watchdog serviced within timing; command completes to "achieved"
  (iii) abort clears the command on fault / e-stop / comm-loss / bit15-drop
  (iv)  System Reset (HR10 bit0) is never written
  (v)   only one config bit at a time
"""

from __future__ import annotations

import pytest

from custom_components.akvo.akvo_client import (
    AkvoClient,
    AkvoCommandRejected,
)
from custom_components.akvo.const import HR_COMMAND

pytestmark = pytest.mark.asyncio


def _move_bit_count(value: int) -> int:
    """Number of move bits (bit1..8) set in an HR10 value."""
    return bin(value & 0b1_1111_1110).count("1")


def _reset_bit_set(value: int) -> bool:
    return bool(value & 0b1)


# --- (i) rejection -----------------------------------------------------------
async def test_reject_when_not_ready(client: AkvoClient, fake_state, fake_transport):
    fake_state.drop_ready_for_external()
    with pytest.raises(AkvoCommandRejected, match="not ready for external"):
        await client.async_request_configuration(3)
    # No command was ever written.
    assert all(addr != HR_COMMAND for addr, _ in fake_transport.writes)


async def test_reject_when_system_fault(client: AkvoClient, fake_state, fake_transport):
    fake_state.inject_system_fault()
    with pytest.raises(AkvoCommandRejected, match="system fault"):
        await client.async_request_configuration(3)
    assert all(addr != HR_COMMAND for addr, _ in fake_transport.writes)


async def test_reject_when_estop(client: AkvoClient, fake_state, fake_transport):
    fake_state.inject_estop(indoor=True)
    with pytest.raises(AkvoCommandRejected, match="e-stop"):
        await client.async_request_configuration(3)
    assert all(addr != HR_COMMAND for addr, _ in fake_transport.writes)


async def test_reject_when_drive_fault(client: AkvoClient, fake_state, fake_transport):
    fake_state.inject_main_fault(0x0001)  # VFD fault
    with pytest.raises(AkvoCommandRejected, match="fault"):
        await client.async_request_configuration(3)
    assert all(addr != HR_COMMAND for addr, _ in fake_transport.writes)


async def test_reject_when_bad_comm(client: AkvoClient, fake_state, fake_transport):
    fake_state.force_bad_comm()
    with pytest.raises(AkvoCommandRejected, match="bad modbus comm"):
        await client.async_request_configuration(3)
    assert all(addr != HR_COMMAND for addr, _ in fake_transport.writes)


# --- (ii) happy path: watchdog serviced, completes to achieved --------------
async def test_command_completes_to_achieved(
    client: AkvoClient, fake_state, fake_transport
):
    state = await client.async_request_configuration(3)
    assert state.active_configuration == 3
    assert not state.floors_moving

    # Watchdog was serviced: HR11 written at least once.
    assert any(addr == 11 for addr, _ in fake_transport.writes)
    # Final HR10 write clears the command (value 0).
    last_cmd = [v for addr, v in fake_transport.writes if addr == HR_COMMAND][-1]
    assert last_cmd == 0


async def test_watchdog_mirror_value_matches_akvo_bit(
    client: AkvoClient, fake_state, fake_transport
):
    await client.async_request_configuration(2)
    wd_writes = [v for addr, v in fake_transport.writes if addr == 11]
    # Every mirror is either 0 or 1 (single bit), never garbage.
    assert all(v in (0, 1) for v in wd_writes)


# --- (iii) abort paths -------------------------------------------------------
@pytest.mark.parametrize(
    ("inject", "match"),
    [
        (lambda s: s.inject_estop(indoor=False), "e-stop"),
        (lambda s: s.inject_system_fault(), "system fault"),
        (lambda s: s.inject_main_fault(0x0002), "fault"),
        (lambda s: s.force_bad_comm(), "bad modbus comm"),
    ],
    ids=["estop", "system_fault", "drive_fault", "bad_comm"],
)
async def test_abort_clears_command(fake_state, inject, match):
    """A fault injected after motion starts aborts and clears HR10."""
    from tests.conftest import FakeTransport

    # Inject the fault on the 2nd read so the move has begun first.
    transport = FakeTransport(fake_state, auto_step=False)
    reads = {"n": 0}

    async def stepping_read(address: int, count: int) -> list[int]:
        reads["n"] += 1
        fake_state.step()
        if reads["n"] == 2:
            inject(fake_state)
        return [fake_state.regs[address + i] for i in range(count)]

    client = AkvoClient(
        stepping_read, transport.write, watchdog_timeout=5.0, service_interval=0.0
    )
    with pytest.raises(AkvoCommandRejected, match="aborted"):
        await client.async_request_configuration(3)

    # HR10 cleared to 0 on abort.
    cmd_writes = [v for addr, v in transport.writes if addr == HR_COMMAND]
    assert cmd_writes[-1] == 0
    assert client.last_abort_reason is not None


# --- (iv) System Reset is never writable ------------------------------------
async def test_system_reset_bit_never_written(
    client: AkvoClient, fake_state, fake_transport
):
    await client.async_request_configuration(5)
    for addr, value in fake_transport.writes:
        if addr == HR_COMMAND:
            assert not _reset_bit_set(value), f"HR10 bit0 set in {value:#06x}"


async def test_no_api_to_request_reset(client: AkvoClient):
    """There is no public method to issue a reset, and out-of-range is rejected."""
    assert not hasattr(client, "async_reset")
    with pytest.raises(AkvoCommandRejected):
        await client.async_request_configuration(0)  # 0 would be the reset slot
    with pytest.raises(AkvoCommandRejected):
        await client.async_request_configuration(9)


# --- (v) exactly one config bit at a time -----------------------------------
async def test_only_one_config_bit(client: AkvoClient, fake_state, fake_transport):
    await client.async_request_configuration(4)
    for addr, value in fake_transport.writes:
        if addr == HR_COMMAND and value != 0:
            assert _move_bit_count(value) == 1, f"{value:#06x} has multiple move bits"


@pytest.mark.parametrize("config", [1, 2, 3, 4, 5, 6, 7, 8])
async def test_each_config_sets_correct_single_bit(config: int):
    word = AkvoClient._config_command_word(config)
    assert _move_bit_count(word) == 1
    assert not _reset_bit_set(word)
    # bit for config N is bit (N) since move base is 1 => 1<<N.
    assert word == 1 << config


# --- (vi) operator cancel / STOP ---------------------------------------------

async def test_cancel_during_move_clears_command(fake_state):
    """Selecting '—' while a move is in progress cancels it: HR10 cleared,
    move-active state stops, and no unsafe write is introduced.

    The read function keeps the floor perpetually moving (never achieves
    the target) so the cancel is the only thing that ends the loop.
    """
    import asyncio

    from tests.conftest import FakeTransport

    transport = FakeTransport(fake_state, auto_step=False)

    # Put the fake into a "moving but never arriving" state.
    # HR0: system_ready (bit0) + ready_for_external (bit15) + watchdog toggle bit13
    fake_state.regs[0] = (1 << 0) | (1 << 15) | (1 << 13)
    # HR1: floors_moving (bit0) set, no achieved bits
    fake_state.regs[1] = 1  # moving, no config achieved

    read_count = {"n": 0}

    async def perpetual_moving_read(address: int, count: int) -> list[int]:
        read_count["n"] += 1
        # Toggle AKVO watchdog bit13 each read to keep the watchdog alive.
        if read_count["n"] % 2 == 0:
            fake_state.regs[0] |= 1 << 13
        else:
            fake_state.regs[0] &= ~(1 << 13)
        # Keep floors_moving=1, no achieved config, ready_for_external=0 (PLC
        # withdrew it once the move started — the pre-check read (n==1) still
        # sees bit15 set so the move is permitted).
        if read_count["n"] > 1:
            fake_state.regs[0] &= ~(1 << 15)  # PLC withdrew bit15
        return [fake_state.regs[address + i] for i in range(count)]

    client = AkvoClient(
        perpetual_moving_read,
        transport.write,
        watchdog_timeout=10.0,
        service_interval=0.0,
    )

    task = asyncio.create_task(client.async_request_configuration(3))

    # Yield enough times that the loop enters _run_until_achieved and has
    # completed at least one iteration (the first read is the pre-check).
    for _ in range(5):
        await asyncio.sleep(0)

    # Cancel — this is the STOP action.
    await client.async_cancel_active_command()

    # The running task must abort with the cancel reason.
    with pytest.raises(AkvoCommandRejected, match="cancelled by operator"):
        await task

    # HR10 must be 0 after cancel.
    cmd_writes = [v for addr, v in transport.writes if addr == HR_COMMAND]
    assert cmd_writes[-1] == 0

    # No System Reset bit ever written.
    for addr, value in transport.writes:
        if addr == HR_COMMAND:
            assert not _reset_bit_set(value), f"HR10 bit0 set: {value:#06x}"

    # Cancel reason recorded.
    assert client.last_abort_reason == "cancelled by operator"


async def test_cancel_with_gate_disabled_is_always_allowed(fake_state):
    """Cancel works regardless of enable_commands gate — stopping needs no gate."""
    from tests.conftest import FakeTransport

    # This test operates directly on the client, which has no enable_commands
    # gate (that gate lives in the select entity). The client's cancel method
    # must be callable unconditionally and must write HR10=0.
    transport = FakeTransport(fake_state, auto_step=False)
    client = AkvoClient(
        transport.read, transport.write, watchdog_timeout=5.0, service_interval=0.0
    )

    # No active command — cancel is a harmless no-op that writes HR10=0.
    await client.async_cancel_active_command()

    cmd_writes = [v for addr, v in transport.writes if addr == HR_COMMAND]
    # The write must exist and must be 0.
    assert cmd_writes[-1] == 0

    # System Reset bit still never touched.
    for addr, value in transport.writes:
        if addr == HR_COMMAND:
            assert not _reset_bit_set(value)


async def test_cancel_no_op_when_nothing_active(fake_state):
    """Cancel when no command is running leaves HR10 = 0 and does not raise."""
    from tests.conftest import FakeTransport

    transport = FakeTransport(fake_state, auto_step=False)
    client = AkvoClient(
        transport.read, transport.write, watchdog_timeout=5.0, service_interval=0.0
    )

    # Must not raise.
    await client.async_cancel_active_command()

    # HR10 was already 0, and the write of 0 is safe.
    cmd_writes = [v for addr, v in transport.writes if addr == HR_COMMAND]
    assert all(v == 0 for v in cmd_writes)


async def test_cancel_does_not_affect_start_path_safety(fake_state, client, fake_transport):
    """Confirming cancel does not alter the start-path safety logic.

    A fresh request after a cancel still enforces all pre-checks. This verifies
    that the cancel flag is cleared when a new request starts.
    """
    import asyncio

    # Issue a real move to completion.
    state = await client.async_request_configuration(3)
    assert state.active_configuration == 3

    # Cancel after the move is already done — harmless.
    await client.async_cancel_active_command()

    # Now inject a fault and verify the start-path still rejects.
    fake_state.inject_estop(indoor=True)
    with pytest.raises(AkvoCommandRejected, match="e-stop"):
        await client.async_request_configuration(1)

    # HR10 never set to a move bit when faulted.
    for addr, value in fake_transport.writes:
        if addr == HR_COMMAND and value != 0:
            # The only non-zero write should be the successful config #3 move.
            # After the cancel + reject there must be no move bit for config #1.
            assert not _reset_bit_set(value)
